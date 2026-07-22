"""FastAPI server for the LLM agent orchestration service.

HTTP routes served:
    POST /api/chat (SSE)                             -> orchestrator.stream_events (internal)
    POST /api/chat/{agent_name} (SSE)                -> orchestrator.stream_events (internal)
    GET  /health                                     -> health check
    GET  /api/agents/{name}                          -> agent_store.get_agent
    PUT  /api/agents/{name}                          -> agent_store.update_agent
    POST /api/agents                                 -> agent_store.create_agent
    GET  /api/agents                                 -> agent_store.list_agents
    GET  /api/backlog, /api/backlog/{session_id}     -> backlog stats
    GET  /api/session/history                        -> session_store.get_turns
    GET  /embed/{path}                               -> embed widget static
    POST /admin/abuse-config/reload                  -> reload abuse config

HTTP routes called (outbound):
    MCPClient.call_tool()    -> mcp-gateway:POST /mcp/message?sessionId=... (SSE/JSON-RPC)
    MCPClient.list_tools()   -> mcp-gateway:POST /mcp/message?sessionId=... (SSE/JSON-RPC)
    RagClient.build_rag_context()  -> rag:POST /context
    RagClient.search_documents()   -> rag:POST /search
    RagClient.list_documents()     -> rag:POST /documents/list
    DataServiceClient.get()        -> data-service:GET /{entity}/{id}
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Callable, Awaitable
from uuid import uuid4

import uvicorn

from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api_service.agent.orchestrator import LLMAgent
from api_service.agent.types import AgentEventData
from api_service.backlog import backlog
from api_service.sessions import session_store
from helperium_sdk.settings import settings
from api_service.http_models import (
    BacklogDetailResponse,
    BacklogEvent,
    BacklogListResponse,
    BacklogSessionMetadata,
    ChatMessage,
    ChatRequest,
    HealthResponse,
    SessionHistoryResponse,
    AgentCreateRequest,
    AgentUpdateRequest,
    AgentResponse,
    AgentListResponse,
    VoiceConfig,
    VoiceAgentConfig,
)
from api_service.agent_store import AgentStore
from api_service.anti_abuse import AntiAbuseChecker, TokenBucket
from api_service.abuse_live import get_live_abuse_provider
from api_service.log_config import configure_logging
from api_service.prometheus_metrics import (
    init_metrics,
    chat_sessions_total,
    chat_messages_total,
    embed_widget_requests,
)
from api_service.guardrails import get_guard_checker
from api_service.spending import get_spending_checker
from api_service.provider_store import get_provider_store
from api_service.error_messages import classify_error
from helperium_sdk.tracing import (
    setup_opentelemetry,
    instrument_fastapi,
    add_span_attributes,
    shutdown as otel_shutdown,
)

# Audio / Voice imports
import base64
from fastapi import UploadFile, File, Form
from api_service.audio.voice_config import (
    load_voice_config,
    save_voice_config,
    resolve_voice_config,
)
from api_service.audio.stt_engine import STTEngine
from api_service.audio.tts_engine import TTSEngine

configure_logging()
setup_opentelemetry("api-service")
logger = logging.getLogger("api_service.server")

# Enable debug logging for agent if DEMO_DEBUG is set
if os.environ.get("DEMO_DEBUG", "").lower() in ("1", "true", "yes"):
    logging.getLogger("api_service.agent").setLevel(logging.DEBUG)
    logging.getLogger("mcp").setLevel(logging.DEBUG)
    logger.info("Debug logging enabled for agent and MCP")


# === Lazy agent singleton (init на первом запросе, а не при импорте) ===
_agent_instance: LLMAgent | None = None
_agent_lock = threading.Lock()
_agent_store: AgentStore | None = None


def get_agent_store() -> AgentStore:
    global _agent_store
    if _agent_store is None:
        with _agent_lock:
            if _agent_store is None:
                db_path = os.environ.get(
                    "AGENT_DB_PATH",
                    str(Path(settings.session_db_path).parent / "agents.sqlite"),
                )
                _agent_store = AgentStore(db_path)
                logger.info("Agent store initialized at %s", db_path)
    return _agent_store


def get_agent() -> LLMAgent:
    """Получить (или создать) глобальный экземпляр агента.

    Инициализируется лениво — при первом обращении, а не при импорте модуля.
    Это позволяет:
      - менять окружение до первого запроса (тесты, разные конфиги)
      - не падать при импорте если MCP/БД недоступны
      - пересоздавать агента между тестами
    """
    global _agent_instance
    if _agent_instance is None:
        with _agent_lock:
            if _agent_instance is None:
                logger.info("Initializing LLM agent...")
                _agent_instance = LLMAgent()
                logger.info("LLM agent initialized")
    return _agent_instance


# === Business Logic Handlers ===


async def get_health() -> HealthResponse:
    """Health check endpoint."""
    payload: dict[str, Any] = {"api": "ok"}
    try:
        payload["ollama"] = await get_agent().health()
    except Exception as exc:
        payload["ollama"] = {"status": "error", "error": str(exc)}
    return HealthResponse(**payload)


async def get_backlog_list() -> BacklogListResponse:
    """List all backlog sessions."""
    sessions = backlog.list_sessions()
    return BacklogListResponse(sessions=[BacklogSessionMetadata(**s) for s in sessions])


async def get_backlog_detail(
    session_id: str, limit: int = 500, offset: int = 0
) -> BacklogDetailResponse:
    """Read records of a specific backlog session."""
    records = backlog.read_session(session_id, limit=limit, offset=offset)
    return BacklogDetailResponse(
        records=[BacklogEvent(**r) for r in records],
        session_id=session_id,
        count=len(records),
    )


async def get_session_history(session_id: str = "default") -> SessionHistoryResponse:
    """Get chat history for a session."""
    history = await asyncio.to_thread(session_store.history_messages, session_id)
    return SessionHistoryResponse(messages=[ChatMessage(**m) for m in history])


async def _check_abuse(
    request: Request,
    session_id: str | None,
    message: str,
    agent_abuse_config: dict | None = None,
) -> StreamingResponse | None:
    """Check if this request passes anti-abuse checks.

    Returns a StreamingResponse with error if blocked, None if allowed.
    """
    live = get_live_abuse_provider()
    effective_cfg = live.get_effective_config(agent_abuse_config)
    checker = AntiAbuseChecker(effective_cfg.to_anti_abuse_config())
    token_bucket = TokenBucket(effective_cfg.to_anti_abuse_config())

    user_agent = request.headers.get("User-Agent", "") or ""
    ip = request.client.host if request.client else "127.0.0.1"
    safe_id = session_id or "unknown"

    # Token bucket check (rate limit per session+IP+UA)
    allowed, ctx = token_bucket.allow(safe_id, ip, user_agent)
    if not allowed:
        retry_after = ctx.get("retry_after", 1.0)
        return StreamingResponse(
            _single_error(f"Too many requests. Retry after {retry_after:.0f}s."),
            media_type="text/event-stream",
            headers={"Retry-After": str(int(retry_after))},
        )

    safe_id = session_id or "unknown"

    # Get session history to check message count & interval
    history = await asyncio.to_thread(session_store.history_messages, safe_id)
    n_msg = len(history)
    last_msg_time = None
    if history:
        import time

        last_msg = history[-1]
        ts = last_msg.get("timestamp") or last_msg.get("created_at")
        if ts:
            try:
                last_msg_time = (
                    time.time() - float(ts) if isinstance(ts, (int, float)) else None
                )
            except (ValueError, TypeError):
                pass

    # Full anti-abuse check
    check_result = checker.check(
        session_id=safe_id,
        ip=ip,
        user_agent=user_agent,
        message=message,
        n_msg=n_msg,
        last_msg_time_since=last_msg_time,
    )
    if not check_result.allowed:
        return StreamingResponse(
            _single_error(f"Request blocked: {check_result.reason}"),
            media_type="text/event-stream",
        )

    return None


def _get_lang(request: Request) -> str:
    """Extract language from Accept-Language header."""
    accept = request.headers.get("Accept-Language", "")
    if accept.startswith("ru"):
        return "ru"
    return "en"


async def chat_handler(request: Request) -> StreamingResponse:
    """Streaming chat endpoint that handles user messages."""
    try:
        body = await request.json()
        chat_req = ChatRequest(**body)
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Invalid request body: {exc}"),
            media_type="text/event-stream",
        )

    message = chat_req.message
    session_id = chat_req.session_id
    tenant_header = request.headers.get("X-Tenant-ID", "")
    tenant_ids = (
        [t.strip() for t in tenant_header.split(",") if t.strip()]
        if tenant_header
        else None
    )

    if not message:
        return StreamingResponse(
            _single_error("Введите вопрос."), media_type="text/event-stream"
        )

    if not session_id:
        return StreamingResponse(
            _single_error("Введите session_id."), media_type="text/event-stream"
        )

    # Anti-abuse check
    abuse_result = await _check_abuse(request, session_id, message)
    if abuse_result is not None:
        return abuse_result

    # Prefix: direct sessions are isolated from agent sessions
    effective_session_id = f"direct:{session_id}"
    chat_sessions_total.inc()
    chat_messages_total.labels(status="sent").inc()

    lang = _get_lang(request)

    async def events():
        try:
            async for event in get_agent().stream_events(
                message,
                session_id=effective_session_id,
                tenant_ids=tenant_ids,
                lang=lang,
            ):
                payload = _event_payload(event.type, event.data)
                if payload is not None:
                    yield _sse(payload)
            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse({"type": "error", "text": classify_error(exc, lang)})

    return StreamingResponse(events(), media_type="text/event-stream")


async def _single_error(text: str):
    """Yield a single error event."""
    yield _sse({"type": "error", "text": text})


def _sse(payload: dict[str, Any]) -> str:
    """Format a payload as a Server-Sent Event."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_payload(event_type: str, data: AgentEventData) -> dict[str, Any] | None:
    """Convert internal agent events to the browser-facing SSE payload."""
    if event_type in ("tool_call", "tool_result", "final", "error", "done"):
        logger.info(f"[SERVER] event_type={event_type}, data={str(data)[:200]}")
    else:
        logger.debug(f"[SERVER] event_type={event_type}, data={str(data)[:200]}")
    if event_type == "token":
        return {"type": "token", "text": data.get("data")}
    if event_type == "final":
        text = data.get("content") if isinstance(data, dict) else ""
        return {"type": "final", "text": text}
    if event_type == "tool_call":
        name = data.get("name", "") if isinstance(data, dict) else ""
        display_name = data.get("display_name", "") or name
        return {
            "type": "tool_call",
            "name": name,
            "display_name": display_name,
        }
    if event_type == "tool_result":
        name = data.get("name", "") if isinstance(data, dict) else ""
        display_name = data.get("display_name", "") or name
        result = data.get("result") if isinstance(data, dict) else None
        payload: dict[str, Any] = {
            "type": "tool_result",
            "name": name,
            "display_name": display_name,
        }
        if result is not None:
            payload["result"] = result
        return payload
    if event_type == "error":
        text = data.get("message") if isinstance(data, dict) else data
        return {"type": "error", "text": text}
    if event_type == "audio":
        audio_data = data.get("data", "") if isinstance(data, dict) else ""
        return {"type": "audio", "data": audio_data}
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — инициализация и cleanup агента."""
    # Startup: OTel FastAPI instrumentation (after routes are registered)
    try:
        instrument_fastapi(app, "api-service")
    except Exception as exc:
        logger.warning("FastAPI instrumentation failed: %s", exc)

    # Startup: прогреваем агента
    logger.info("Warming up LLM agent...")
    try:
        get_agent()  # lazy init при старте, а не при первом запросе
        logger.info("LLM agent ready")
    except Exception as exc:
        logger.warning("Agent warmup failed (will retry on first request): %s", exc)

    yield

    # Shutdown: закрываем MCP-сессию, если она была открыта
    logger.info("API server shutting down")
    try:
        agent = _agent_instance
        if agent is not None and agent.mcp_client is not None:
            await agent.mcp_client.close()
    except Exception as exc:
        logger.warning("MCP client close failed: %s", exc)
    # Flush OTel spans before exit
    try:
        otel_shutdown()
    except Exception as exc:
        logger.warning("OTel shutdown failed: %s", exc)

    logger.info("Shutdown complete")


# Create the API application
app = FastAPI(
    title="Agent-Tutor Core API",
    description="Backend API for the Agent-Tutor system. Handles orchestration, session history, and tool integration.",
    version="1.1.0",
    lifespan=lifespan,
)

# Prometheus metrics (must be before middleware, at module level)
init_metrics(app)
# OpenTelemetry FastAPI instrumentation (after all routes/middleware are registered)
# Called in lifespan startup to ensure routes are decorated first


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    """Prometheus metrics endpoint — returns all registered metrics."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# Rate limiter
# Default: 30 requests per minute per IP (configurable via CHAT_RATE_LIMIT env)
rate_limit = os.environ.get("CHAT_RATE_LIMIT", "30/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=[rate_limit])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)

# CORS middleware
# allow_origins from env: comma-separated, or default to localhost:8080 for dev.
# Set CORS_ALLOW_ORIGINS=* explicitly for embed/production to allow all origins.
cors_origins_raw = os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:8080")
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()] or [
    "http://localhost:8080"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Tenant-ID", "X-Correlation-ID"],
)

# Mount embed widget static files
# Resolution order:
#   1. EMBED_DIR env var (absolute override for production)
#   2. <project_root>/embed/dist/
embed_override = os.environ.get("EMBED_DIR")
if embed_override:
    embed_path = Path(embed_override)
else:
    embed_path = Path(__file__).resolve().parent.parent.parent / "embed" / "dist"
if embed_path.is_dir():
    app.mount("/embed", StaticFiles(directory=str(embed_path)), name="embed")
    logger.info("Embed widget mounted at /embed from %s", embed_path)
else:
    logger.warning(
        "Embed directory not found at %s, /embed will be unavailable", embed_path
    )


# --- Embed security headers middleware ---
@app.middleware("http")
async def add_embed_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    response = await call_next(request)
    if request.url.path.startswith("/embed/"):
        embed_widget_requests.labels(endpoint=request.url.path).inc()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Cache static assets
        if request.url.path.endswith((".js", ".css")):
            # Widget JS/CSS — versioned by path, safe to cache 1 hour
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            # Non-asset embed files: short cache to avoid stale 404s
            response.headers.setdefault("Cache-Control", "no-cache")
    return response


# --- Correlation ID middleware ---
@app.middleware("http")
async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id

    # Enrich OTel span with request metadata
    tenant_id = request.headers.get("X-Tenant-ID", "")
    if tenant_id:
        add_span_attributes({"tenant.id": tenant_id})
    add_span_attributes(
        {
            "correlation_id": correlation_id,
            "http.method": request.method,
            "http.target": request.url.path,
        }
    )

    logger.info(
        "Request started",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    logger.info(
        "Request completed",
        extra={
            "correlation_id": correlation_id,
            "status_code": response.status_code,
        },
    )
    return response


# Register routes
@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка здоровья API",
    description="Проверяет работоспособность API и доступность LLM провайдера.",
)
async def health_endpoint():
    return await get_health()


@app.post(
    "/api/chat",
    summary="Стриминг чата",
    description="Основной эндпоинт для общения с агентом. Возвращает поток SSE событий.",
)
@limiter.limit(rate_limit)
async def chat_endpoint(request: Request) -> StreamingResponse:
    return await chat_handler(request)


@app.get(
    "/api/backlog",
    response_model=BacklogListResponse,
    summary="Список сессий бэклога",
    description="Возвращает список всех сохраненных сессий из бэклога.",
)
async def backlog_list_endpoint():
    return await get_backlog_list()


@app.get(
    "/api/backlog/{session_id}",
    response_model=BacklogDetailResponse,
    summary="Детали сессии бэклога",
    description="Возвращает все события конкретной сессии.",
)
async def backlog_detail_endpoint(
    session_id: str, limit: int = Query(500, ge=1), offset: int = Query(0, ge=0)
):
    return await get_backlog_detail(session_id, limit, offset)


@app.get(
    "/api/backlog/stats/{session_id}",
    summary="Статистика LLM-вызовов сессии",
    description="Возвращает агрегированную статистику по токенам, стоимости и количеству вызовов для сессии.",
)
async def backlog_stats_endpoint(session_id: str):
    """Get aggregated LLM stats for a session."""
    from api_service.backlog import backlog

    stats = backlog.get_session_stats(session_id)
    return stats


@app.get(
    "/api/backlog/errors",
    summary="Последние ошибки бэклога",
    description="Возвращает последние ошибки по всем сессиям.",
)
async def backlog_errors_endpoint(limit: int = Query(50, ge=1, le=200)):
    """Get recent errors across all sessions."""
    from api_service.backlog import backlog

    errors = backlog.get_recent_errors(limit=limit)
    return {"errors": errors, "total": len(errors)}


@app.get(
    "/api/backlog/export/{session_id}",
    response_class=StreamingResponse,
    summary="Экспорт сессии бэклога",
    description="Export backlog session as JSONL for fine-tuning.",
)
async def export_backlog(session_id: str):
    """Export backlog session as JSONL for fine-tuning."""

    async def generate():
        records = backlog._read_records(session_id)
        for r in records:
            event = r.get("event")
            if event == "turn_start":
                msg = {
                    "role": "user",
                    "content": r.get("data", {}).get("user_message", ""),
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "model_response":
                data = r.get("data", {})
                content = data.get("content", "")
                if content:
                    msg = {"role": "assistant", "content": content}
                    yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "tool_call":
                data = r.get("data", {})
                msg = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": data.get("name", ""),
                                "arguments": json.dumps(
                                    data.get("arguments", {}), ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "tool_result":
                data = r.get("data", {})
                msg = {
                    "role": "tool",
                    "tool_call_id": f"call_{data.get('name', '')}",
                    "content": data.get("result", ""),
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.jsonl"'},
    )


@app.get(
    "/api/session/history",
    response_model=SessionHistoryResponse,
    summary="История сессии",
    description="Возвращает историю сообщений для указанной сессии.",
)
async def session_history_endpoint(
    session_id: str = Query("default"), agent_name: str = Query(None)
):
    effective = f"agent:{agent_name}:{session_id}" if agent_name else session_id
    return await get_session_history(effective)


# ── Guardrails Admin API ──


@app.get("/admin/guardrails")
async def get_guardrails():
    """Get current guard config."""
    checker = get_guard_checker()
    return {
        "enabled": checker.config.enabled,
        "block_on_match": checker.config.block_on_match,
        "blocked_count": checker.config.blocked_count,
    }


@app.post("/admin/guardrails")
async def update_guardrails(config: dict):
    """Update guard config. Accepts: enabled, block_on_match."""
    checker = get_guard_checker()
    if "enabled" in config:
        checker.config.enabled = bool(config["enabled"])
    if "block_on_match" in config:
        val = str(config["block_on_match"])
        if val in ("block", "warn"):
            checker.config.block_on_match = val
    return {
        "enabled": checker.config.enabled,
        "block_on_match": checker.config.block_on_match,
        "blocked_count": checker.config.blocked_count,
    }


# ── Spending Admin API ──


@app.get("/admin/spending")
async def get_all_spending():
    """Get spending overview."""
    checker = get_spending_checker()
    return {
        "enabled": checker.config.enabled,
        "default_budget": checker.config.default_budget,
        "period": checker.config.period,
    }


@app.get("/admin/spending/{tenant_id}")
async def get_tenant_spending(tenant_id: str):
    """Get spending for a specific tenant."""
    checker = get_spending_checker()
    return checker.get_spending(tenant_id)


@app.post("/admin/spending/{tenant_id}")
async def set_tenant_budget(tenant_id: str, config: dict):
    """Set per-tenant budget override.

    Body: {"budget": 100.0}
    """
    budget = float(config.get("budget", 0))
    if budget < 0:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Budget must be >= 0")
    checker = get_spending_checker()
    checker.set_budget(tenant_id, budget)
    return checker.get_spending(tenant_id)


# ── Live Abuse Config Admin API ──


@app.get("/admin/abuse-config")
async def get_abuse_config():
    """Get the current effective abuse configuration (from file + env)."""
    provider = get_live_abuse_provider()
    cfg = provider.get_config()
    from api_service.abuse_live import _serialize_config

    return _serialize_config(cfg)


@app.post("/admin/abuse-config/reload")
async def reload_abuse_config():
    """Reload abuse config from disk and apply runtime settings.

    Called by admin-dashboard after saving new config.
    Applies runtime settings (history, loops) to the live settings object.
    """
    provider = get_live_abuse_provider()
    cfg = provider.reload()
    provider.apply_runtime_settings()
    from api_service.abuse_live import _serialize_config

    return {"status": "ok", "config": _serialize_config(cfg)}


@app.post("/admin/abuse-config")
async def save_abuse_config(data: dict):
    """Save new abuse config directly (admin dashboard alternative endpoint)."""
    provider = get_live_abuse_provider()
    cfg = provider.save_config(data)
    provider.apply_runtime_settings()
    from api_service.abuse_live import _serialize_config

    return {"status": "ok", "config": _serialize_config(cfg)}


# ── LLM Providers Admin API ──


@app.get("/admin/llm-providers")
async def list_llm_providers():
    """List all LLM providers with masked API keys."""
    store = get_provider_store()
    return {
        "providers": store.list_providers(),
        "fallback_enabled": store.get_fallback_enabled(),
    }


@app.get("/admin/llm-provider-list")
async def list_litellm_providers():
    """List all available providers from LiteLLM (live, no hardcode)."""
    from api_service.provider_store import get_litellm_provider_list

    providers = get_litellm_provider_list()
    return {
        "providers": providers,
        "count": len(providers),
    }


@app.get("/admin/llm-providers/{name}")
async def get_llm_provider(name: str):
    """Get a single LLM provider with masked API key."""
    from fastapi import HTTPException

    store = get_provider_store()
    provider = store.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return provider


@app.post("/admin/llm-providers", status_code=201)
async def add_llm_provider(body: dict):
    """Add a new LLM provider.

    Body:
        name (required): unique provider name
        model (required): model identifier (e.g. openai/gpt-4o, anthropic/claude-3-sonnet)
        provider: provider type — auto-detected from model prefix if omitted
        api_key: API key (will not be returned in full)
        api_base: custom API base URL
        enabled: whether the provider is active (default: true)
    """
    from fastapi import HTTPException

    store = get_provider_store()
    try:
        result = store.add_provider(
            name=body["name"],
            model=body["model"],
            provider=body.get("provider", ""),
            api_key=body.get("api_key"),
            api_base=body.get("api_base"),
            enabled=body.get("enabled", True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing required field: {exc}")
    return result


@app.put("/admin/llm-providers/{name}")
async def update_llm_provider(name: str, body: dict):
    """Update an existing LLM provider.

    Omitted fields keep their current value.
    Set api_key="" to keep existing key (not change it).
    Set api_key="__clear__" to clear the key.
    """
    from fastapi import HTTPException

    store = get_provider_store()
    result = store.update_provider(
        name=name,
        model=body.get("model"),
        provider=body.get("provider"),
        api_key=body.get("api_key"),
        api_base=body.get("api_base"),
        enabled=body.get("enabled"),
        label=body.get("label"),
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return result


@app.delete("/admin/llm-providers/{name}")
async def delete_llm_provider(name: str):
    """Delete an LLM provider."""
    from fastapi import HTTPException

    store = get_provider_store()
    if not store.delete_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"deleted": True}


@app.post("/admin/llm-providers/{name}/toggle")
async def toggle_llm_provider(name: str):
    """Toggle a provider on/off."""
    from fastapi import HTTPException

    store = get_provider_store()
    provider_data = await store.get_provider(name)
    if not provider_data:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    new_enabled = not provider_data["enabled"]
    result = await store.set_enabled(name, new_enabled)
    return result


# ── LLM Config Admin API (legacy, from env vars) ──


@app.get("/admin/llm-config")
async def get_llm_config():
    """Get current LLM provider fallback configuration."""
    from api_service.provider_store import get_provider_store

    store = get_provider_store()
    providers = await store.list_providers()

    return {
        "fallback_enabled": store.get_fallback_enabled(),
        "providers": providers,
        "num_models": len(providers),
    }


# ── Voice Config ──


def _preserve_fields(
    body_providers: list[dict], current_providers: list, fields: list[str]
) -> None:
    """Preserve masked sensitive fields when updating provider configs.

    Frontend sends empty strings for api_key/api_base/voice to avoid
    exposing secrets in the PUT body. This function fills them back
    from the currently stored values when the incoming value is falsy.
    """
    existing = {p.name: p for p in current_providers}
    for p in body_providers:
        for field in fields:
            if not p.get(field):
                prev = existing.get(p.get("name", ""))
                if prev and getattr(prev, field, None):
                    p[field] = getattr(prev, field)


@app.get("/api/voice-config")
async def get_voice_config():
    """Get current voice (STT/TTS) configuration."""
    config = load_voice_config()
    return config.model_dump(mode="json")


@app.put("/api/voice-config")
async def update_voice_config(body: dict) -> dict:
    """Update and persist voice configuration.

    Preserves existing api_key/api_base/voice per provider when the
    incoming value is None or empty (masks sensitive fields).
    """
    current = load_voice_config()

    # Preserve masked fields (frontend sends empty strings for security)
    _preserve_fields(
        body.get("stt_providers", []), current.stt_providers, ["api_key", "api_base"]
    )
    _preserve_fields(
        body.get("tts_providers", []),
        current.tts_providers,
        ["api_key", "api_base", "voice"],
    )

    config = VoiceConfig(**body)
    save_voice_config(config)
    return config.model_dump(mode="json")


# ── Agent CRUD ──


@app.post(
    "/api/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Создать агента",
    description="Создаёт нового агента с указанными tenant_id.",
)
async def create_agent_endpoint(req: AgentCreateRequest) -> AgentResponse:
    try:
        result = await asyncio.to_thread(
            get_agent_store().create_agent,
            name=req.name,
            description=req.description,
            tenant_ids=req.tenant_ids,
            widget_config=req.widget_config.model_dump() if req.widget_config else None,
            llm_config=req.llm_config.model_dump() if req.llm_config else None,
            provider_priority=req.provider_priority or None,
            abuse_config=req.abuse_config,
            system_prompt=req.system_prompt,
            voice_config=req.voice_config.model_dump() if req.voice_config else None,
        )
        return AgentResponse(**result)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/api/agents",
    response_model=AgentListResponse,
    summary="Список агентов",
    description="Возвращает список всех созданных агентов.",
)
async def list_agents_endpoint() -> AgentListResponse:
    agents = await asyncio.to_thread(get_agent_store().list_agents)
    return AgentListResponse(agents=[AgentResponse(**a) for a in agents])


@app.get(
    "/api/agents/{name}",
    response_model=AgentResponse,
    summary="Получить агента",
    description="Возвращает данные конкретного агента по имени.",
)
async def get_agent_endpoint(name: str) -> AgentResponse:
    from fastapi import HTTPException

    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentResponse(**agent)


@app.put(
    "/api/agents/{name}",
    response_model=AgentResponse,
    summary="Обновить агента",
    description="Обновляет описание и/или tenant_id агента.",
)
async def update_agent_endpoint(name: str, req: AgentUpdateRequest) -> AgentResponse:
    from fastapi import HTTPException

    result = await asyncio.to_thread(
        get_agent_store().update_agent,
        name=name,
        description=req.description,
        tenant_ids=req.tenant_ids,
        widget_config=req.widget_config.model_dump() if req.widget_config else None,
        llm_config=req.llm_config.model_dump() if req.llm_config else None,
        provider_priority=req.provider_priority,
        abuse_config=req.abuse_config,
        system_prompt=req.system_prompt,
        voice_config=req.voice_config.model_dump() if req.voice_config else None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentResponse(**result)


@app.get(
    "/api/agents/{name}/widget-config",
    summary="Конфиг виджета для агента",
    description="Возвращает настройки embed-виджета для указанного агента.",
)
async def agent_widget_config_endpoint(name: str) -> dict:
    """Get widget configuration for an agent. Used by embed.js."""
    from fastapi import HTTPException

    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    cfg = agent.get("widget_config") or {}
    return {
        "title": cfg.get("title", "Ассистент"),
        "greeting": cfg.get("greeting", "Чем могу помочь?"),
        "accent_color": cfg.get("accent_color", "#0f766e"),
        "position": cfg.get("position", "right"),
    }


@app.delete(
    "/api/agents/{name}",
    status_code=204,
    summary="Удалить агента",
    description="Удаляет агента по имени.",
)
async def delete_agent_endpoint(name: str):
    from fastapi import HTTPException

    deleted = await asyncio.to_thread(get_agent_store().delete_agent, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return None


# ── Chat by agent name ──


async def chat_agent_handler(request: Request, name: str) -> StreamingResponse:
    """Chat with a specific agent (resolves tenant_ids from agent store)."""
    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        return StreamingResponse(
            _single_error(f"Agent '{name}' not found"),
            media_type="text/event-stream",
            status_code=404,
        )

    try:
        body = await request.json()
        chat_req = ChatRequest(**body)
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Invalid request body: {exc}"),
            media_type="text/event-stream",
        )

    message = chat_req.message
    session_id = chat_req.session_id
    tenant_ids_raw = agent.get("tenant_ids")
    # empty list → no tenant restriction (default tenant is fine)
    # None (key missing) → also no restriction
    # ["shop"] → scope to shop tenant only
    tenant_ids = tenant_ids_raw if tenant_ids_raw else None
    llm_config = agent.get("llm_config")
    # system_prompt: новое отдельное поле (через админку), fallback на старый llm_config.system_prompt
    system_prompt = agent.get("system_prompt") or (
        llm_config.get("system_prompt") if llm_config else None
    )

    # Anti-abuse with per-agent config merge
    agent_abuse_config = agent.get("abuse_config")
    abuse_result = await _check_abuse(request, session_id, message, agent_abuse_config)
    if abuse_result is not None:
        return abuse_result

    # Resolve provider priority -> prioritized LLM client
    provider_priority = agent.get("provider_priority", [])
    request_llm = None
    if provider_priority:
        pass  # handled inside stream_events via provider_priority param
    elif llm_config:
        from api_service.agent.llm_client import create_client

        request_llm = create_client(llm_config)

    if not message:
        return StreamingResponse(
            _single_error("Введите вопрос."), media_type="text/event-stream"
        )

    if not session_id:
        return StreamingResponse(
            _single_error("Введите session_id."), media_type="text/event-stream"
        )

    # Prefix: each agent has isolated session namespace
    effective_session_id = f"agent:{name}:{session_id}"

    lang = _get_lang(request)

    async def events():
        try:
            kwargs: dict = dict(
                user_message=message,
                session_id=effective_session_id,
                tenant_ids=tenant_ids,
                system_prompt=system_prompt,
                lang=lang,
            )
            if provider_priority:
                kwargs["provider_priority"] = provider_priority
            elif request_llm:
                kwargs["llm_client"] = request_llm
            elif llm_config:
                kwargs["llm_config"] = llm_config
            async for event in get_agent().stream_events(**kwargs):
                payload = _event_payload(event.type, event.data)
                if payload is not None:
                    yield _sse(payload)
            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse({"type": "error", "text": classify_error(exc, lang)})

    return StreamingResponse(events(), media_type="text/event-stream")


# ── Voice Chat (должен быть ДО /api/chat/{name}, иначе {name} перехватит "voice") ──


@app.post("/api/chat/voice")
@limiter.limit(rate_limit)
async def chat_voice_endpoint(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
    agent: str | None = Form(None),
    lang: str | None = Form(None),
) -> StreamingResponse:
    """Voice chat endpoint.

    Accepts an audio file via multipart/form-data, transcribes it via
    the configured STT provider(s), feeds the text through the existing
    chat pipeline, and streams the response as SSE events.

    If TTS providers are configured, the final answer is also sent as
    an ``type="audio"`` SSE event with base64-encoded audio bytes.
    """
    try:
        audio_bytes = await audio.read()
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Failed to read audio: {exc}"),
            media_type="text/event-stream",
        )

    if not audio_bytes:
        return StreamingResponse(
            _single_error("Empty audio file"),
            media_type="text/event-stream",
        )

    # Load voice config
    voice_config = load_voice_config()
    if not voice_config.enabled:
        return StreamingResponse(
            _single_error("Voice input is disabled"),
            media_type="text/event-stream",
        )

    # Size check
    if len(audio_bytes) > voice_config.max_voice_message_size:
        size_mb = len(audio_bytes) / (1024 * 1024)
        max_mb = voice_config.max_voice_message_size / (1024 * 1024)
        return StreamingResponse(
            _single_error(f"Audio too large: {size_mb:.1f}MB > {max_mb:.0f}MB"),
            media_type="text/event-stream",
        )

    # Resolve per-agent overrides
    resolved_config = voice_config
    agent_data = None
    if agent:
        agent_data = await asyncio.to_thread(get_agent_store().get_agent, agent)
        if agent_data:
            agent_voice_config = agent_data.get("voice_config")
            if agent_voice_config:
                agent_voice_obj = VoiceAgentConfig(**agent_voice_config)
                resolved_config = resolve_voice_config(voice_config, agent_voice_obj)

    # Re-check after agent overrides (voice_input_disabled, etc.)
    if not resolved_config.enabled:
        return StreamingResponse(
            _single_error("Voice input is disabled"),
            media_type="text/event-stream",
        )

    # Build STT engine and transcribe
    stt_engine = STTEngine.from_config(resolved_config)
    try:
        stt_result = await stt_engine.transcribe(audio_bytes)
    except RuntimeError as exc:
        logger.error(
            "Voice: STT failed provider=%s session=%s error=%s",
            resolved_config.stt_providers[0].name
            if resolved_config.stt_providers
            else "none",
            session_id,
            exc,
        )
        return StreamingResponse(
            _single_error(classify_error(exc, lang or "ru")),
            media_type="text/event-stream",
        )
    except Exception as exc:
        logger.exception(
            "Voice: STT unexpected error provider=%s session=%s",
            resolved_config.stt_providers[0].name
            if resolved_config.stt_providers
            else "none",
            session_id,
        )
        return StreamingResponse(
            _single_error(classify_error(exc, lang or "ru")),
            media_type="text/event-stream",
        )

    text = stt_result.text
    if not text.strip():
        return StreamingResponse(
            _single_error("No speech detected"),
            media_type="text/event-stream",
        )

    logger.info(
        "Voice: STT=%s text=%s (session=%s)",
        stt_result.provider_name,
        text[:80],
        session_id,
    )

    # Determine tenant_ids: from agent config (primary) or X-Tenant-ID header (override)
    tenant_ids = None
    if agent_data and agent_data.get("tenant_ids"):
        tenant_ids_raw = agent_data.get("tenant_ids")
        tenant_ids = tenant_ids_raw if tenant_ids_raw else None
    if not tenant_ids:
        tenant_header = request.headers.get("X-Tenant-ID", "")
        tenant_ids = (
            [t.strip() for t in tenant_header.split(",") if t.strip()]
            if tenant_header
            else None
        )

    # Agent-specific session prefix
    if agent:
        effective_session_id = f"agent:{agent}:{session_id}"
    else:
        effective_session_id = f"direct:{session_id}"

    # Resolve lang
    request_lang = lang or _get_lang(request)

    # Resolve LLM client from agent config (same as chat_agent_handler)
    if agent_data:
        llm_config_agent = agent_data.get("llm_config")
        provider_priority = agent_data.get("provider_priority", [])
        system_prompt = agent_data.get("system_prompt") or (
            llm_config_agent.get("system_prompt") if llm_config_agent else None
        )
    else:
        llm_config_agent = None
        provider_priority = []
        system_prompt = None

    request_llm = None
    if provider_priority:
        pass  # handled inside stream_events via provider_priority param
    elif llm_config_agent:
        from api_service.agent.llm_client import create_client

        request_llm = create_client(llm_config_agent)

    # Build TTS engine if configured
    tts_enabled = (
        len(resolved_config.tts_providers) > 0 and resolved_config.tts_fallback_enabled
    )
    tts_engine = TTSEngine.from_config(resolved_config) if tts_enabled else None

    async def events():
        final_text = ""
        try:
            kwargs: dict = dict(
                user_message=text,
                session_id=effective_session_id,
                tenant_ids=tenant_ids,
                system_prompt=system_prompt,
                lang=request_lang,
            )
            if provider_priority:
                kwargs["provider_priority"] = provider_priority
            elif request_llm:
                kwargs["llm_client"] = request_llm
            elif llm_config_agent:
                kwargs["llm_config"] = llm_config_agent
            async for event in get_agent().stream_events(
                **kwargs,
            ):
                payload = _event_payload(event.type, event.data)
                if payload is not None:
                    if event.type == "final":
                        final_text = event.data.get("content", "")
                    yield _sse(payload)

            # TTS: synthesize final response if configured
            if tts_engine and final_text.strip():
                try:
                    tts_result = await tts_engine.synthesize(final_text)
                    audio_b64 = base64.b64encode(tts_result.audio_bytes).decode()
                    yield _sse(
                        {
                            "type": "audio",
                            "data": audio_b64,
                        }
                    )
                except Exception as exc:
                    logger.warning("TTS synthesis failed: %s", exc)

            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse(
                {
                    "type": "error",
                    "text": classify_error(exc, request_lang),
                }
            )

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post(
    "/api/chat/{name}",
    summary="Чат с агентом по имени",
    description="Стриминг чата с конкретным агентом. Tenant IDs берутся из конфига агента.",
)
@limiter.limit(rate_limit)
async def chat_by_agent_endpoint(name: str, request: Request) -> StreamingResponse:
    return await chat_agent_handler(request, name)


def main() -> None:
    """Run the API server."""
    # Cleanup old backlog files on each startup
    try:
        backlog.cleanup_old()
        logger.info("Backlog cleanup completed")
    except Exception as exc:
        logger.warning("Backlog cleanup failed: %s", exc)

    # Apply runtime settings from live abuse config
    try:
        get_live_abuse_provider().apply_runtime_settings()
        logger.info("Runtime settings applied from abuse config")
    except Exception as exc:
        logger.warning("Failed to apply runtime settings: %s", exc)

    uvicorn.run(
        "api_service.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
