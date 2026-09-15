"""FastAPI-based web server with reverse proxy to multiple upstream services.

HTTP routes proxied:
    proxy_manifest()        -> data-service:GET /mcp/manifest
    proxy_data_entity()     -> data-service:GET /{entity}
    proxy_data_stats()      -> data-service:GET /stats
    proxy_rag_documents()   -> rag:POST /documents/list
    proxy_agents()          -> api-service:GET /api/agents (name-only projection)
    proxy_health()          -> api-service:GET /health
    proxy_session_history() -> api-service:GET /api/session/history (capability)
    proxy_report()          -> api-service:POST /api/reports
    proxy_chat()            -> api-service:POST /api/chat (SSE)
    proxy_chat_by_agent()   -> api-service:POST /api/chat/{agent_name} (SSE)
    proxy_embed()           -> api-service:GET /embed/{path}
    proxy_tenant_api(data/) -> data-service:GET /{path}
    proxy_tenant_api(rag/)  -> rag:POST|GET /{path}
    proxy_tenant_api(api/)  -> api-service allowlist: chat/chat/*, health,
                               reports, embed/* (everything else 404s)
    get_tenants()           -> DEMO_TENANTS env, no data-service discovery

Stage 0.4: Translated from Starlette to FastAPI + /api/* reverse proxy + SSE-proxy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable
from uuid import uuid4

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from demo.settings import settings
from helperium_sdk.tracing import (
    setup_opentelemetry,
    instrument_fastapi,
    add_span_attributes,
    shutdown as otel_shutdown,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("demo.web.server")

# OpenTelemetry setup
setup_opentelemetry("demo-web")


STATIC_DIR = Path(__file__).resolve().parent / "static"

# API proxy configuration
API_BASE_URL = f"http://{settings.api_host}:{settings.api_port}"


def _build_api_url(path: str) -> str:
    """Build full API URL from path."""
    return f"{API_BASE_URL}{path}" if path.startswith("/") else f"{API_BASE_URL}/{path}"


def _interrupted_text(request: Request) -> str:
    """Terminal error text in the visitor's language.

    Same source as api-service: the widget renders this text verbatim, so a
    Russian-speaking visitor must not get an English error (and vice versa).
    """
    if request.headers.get("accept-language", "").startswith("ru"):
        return "Соединение с ассистентом прервано. Попробуйте ещё раз."
    return "The connection to the assistant was interrupted. Please try again."


async def _get_base_proxy_headers(
    request: Request, attach_bearer: bool = True
) -> dict[str, str]:
    """Build base headers for internal service proxies (data-service, RAG).

    Does NOT include session capability token — only api-service needs it.
    """
    headers = {
        "user-agent": request.headers.get("user-agent", "demo-web-proxy"),
        "accept": request.headers.get("accept", "*/*"),
        "accept-language": request.headers.get("accept-language", ""),
        "accept-encoding": request.headers.get("accept-encoding", ""),
    }

    # Forward X-Tenant-ID if present in the browser request OR in request.state
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id and hasattr(request.state, "tenant_id"):
        tenant_id = request.state.tenant_id
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id

    # Единый correlation ID на цепочку браузер → прокси → upstream:
    # если браузер не прислал свой, отдаём наверх id из middleware прокси.
    correlation_id = request.headers.get("x-correlation-id") or getattr(
        request.state, "correlation_id", None
    )
    # demo-web — доверенный край для браузерного трафика: реальный клиент —
    # это TCP-peer соединения. Клиентский X-Forwarded-For не должен доходить
    # до upstream: per-IP лимиты и форензика ключуются по адресу пира.
    client = request.client
    if client and client.host:
        headers["x-forwarded-for"] = client.host

    if correlation_id:
        headers["x-correlation-id"] = correlation_id

    # Forward content-type for requests with body
    if request.method in ("POST", "PUT", "PATCH"):
        ct = request.headers.get("content-type")
        if ct:
            headers["content-type"] = ct

    # Add bearer token for internal services (data-service, RAG) that need it.
    if attach_bearer and settings.api_bearer_token:
        headers["authorization"] = f"Bearer {settings.api_bearer_token}"

    # Pentest H1: demo/web — публичный край. api-secrets (полные api_key)
    # выдаются api-service только при X-Full-Keys: 1 (opt-in для admin-
    # dashboard). Браузерный клиент не должен уметь этого попросить через
    # наш прокси — заголовок всегда выбрасывается.
    headers.pop("x-full-keys", None)

    return headers



async def _get_api_proxy_headers(
    request: Request, attach_bearer: bool = True
) -> dict[str, str]:
    """Build headers for proxying to API service (includes session capability)."""
    headers = await _get_base_proxy_headers(request, attach_bearer=attach_bearer)

    # Session capability travels with the browser: chat turns and transcript
    # reads authenticate with the token minted for that session (pentest F4).
    session_token = request.headers.get("x-session-token")
    if session_token:
        headers["x-session-token"] = session_token

    return headers


# Backward-compatibility alias for existing tests.
# The old _get_proxy_headers included both bearer and session token.
# Use _get_base_proxy_headers for internal services (data/rag) and
# _get_api_proxy_headers for api-service routes.
_get_proxy_headers = _get_api_proxy_headers


async def _proxy_to_api(
    request: Request,
    api_path: str,
    stream: bool = False,
    attach_bearer: bool = True,
) -> Response | StreamingResponse:
    """Proxy request to API service."""
    http_client = request.app.state.http_client
    url = _build_api_url(api_path)
    headers = await _get_api_proxy_headers(request, attach_bearer=attach_bearer)

    body = await request.body() if request.method != "GET" else None

    logger.debug(f"Proxy {request.method} {api_path} -> {url}")
    logger.debug(f"Proxy headers: {headers}")
    if body:
        logger.debug(f"Proxy body size: {len(body)} bytes")

    try:
        # Pentest 2026-09-14 robustness: WEB_PROXY_TIMEOUT applies to the gap
        # BETWEEN streamed chunks, but an agent turn can legitimately stay
        # silent longer while it thinks or runs tools — a 30s read timeout
        # used to kill the SSE proxy leg mid-stream. Streaming requests
        # therefore carry an unbounded read timeout; connection/pool limits
        # stay bounded and api-service owns the real upstream deadlines.
        build_kwargs: dict[str, Any] = {}
        if stream:
            build_kwargs["timeout"] = httpx.Timeout(
                settings.web_proxy_timeout, read=None
            )
        proxy_req = http_client.build_request(
            request.method,
            url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
            **build_kwargs,
        )

        if stream:
            response = await http_client.send(proxy_req, stream=True)

            if response.status_code != 200:
                # ``send(..., stream=True)`` leaves the response body unread.
                # Read it before forwarding an upstream SSE error (for example
                # a 429 from the chat limiter), otherwise accessing
                # ``response.content`` raises ``ResponseNotRead`` and masks the
                # real response with a 500 from this proxy.
                content = await response.aread()
                response_headers = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower()
                    not in (
                        "connection",
                        "content-length",
                        "keep-alive",
                        "proxy-authenticate",
                        "proxy-authorization",
                        "te",
                        "trailer",
                        "transfer-encoding",
                        "upgrade",
                    )
                }
                return Response(
                    content=content,
                    status_code=response.status_code,
                    headers=response_headers,
                    media_type=response.headers.get("content-type"),
                )

            async def stream_gen():
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                except Exception as exc:
                    # A dead upstream must never end the stream silently: the
                    # widget waits for a terminal event, otherwise the visitor
                    # is stuck on the "thinking" indicator forever.
                    logger.error("SSE proxy interrupted: %s", exc)
                    error_payload: dict[str, Any] = {
                        "type": "error",
                        "text": _interrupted_text(request),
                    }
                    correlation = headers.get("x-correlation-id")
                    if correlation:
                        error_payload["correlation_id"] = correlation
                    yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
                    yield 'data: {"type": "done"}\n\n'

            # Filter out hop-by-hop headers
            response_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower()
                not in (
                    "connection",
                    "content-length",
                    "keep-alive",
                    "proxy-authenticate",
                    "proxy-authorization",
                    "te",
                    "trailer",
                    "transfer-encoding",
                    "upgrade",
                )
            }

            return StreamingResponse(
                stream_gen(),
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )
        else:
            response = await http_client.send(proxy_req, stream=False)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )

    except httpx.ConnectError as exc:
        logger.error(f"API connection error: {exc}")
        return Response(
            content=b"API service unavailable",
            status_code=502,
            headers={"content-type": "text/plain"},
        )
    except httpx.TimeoutException as exc:
        logger.error(f"API timeout: {exc}")
        return Response(
            content=b"API service timeout",
            status_code=504,
            headers={"content-type": "text/plain"},
        )
    except httpx.HTTPStatusError as exc:
        return Response(
            content=exc.response.content,
            status_code=exc.response.status_code,
            headers=dict(exc.response.headers),
        )
    except Exception as exc:
        # Never echo an unexpected exception to the browser: its message can
        # carry internal hosts, filesystem paths or upstream credentials.
        # Detail goes to the log, the client gets a generic retryable error.
        logger.error(f"Proxy error: {exc}")
        return Response(
            content=b"Proxy error",
            status_code=500,
            headers={"content-type": "text/plain"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Create HTTP client for API proxying
    http_client = httpx.AsyncClient(timeout=settings.web_proxy_timeout)
    app.state.http_client = http_client

    # Startup
    logger.info(f"Web server starting on {settings.web_host}:{settings.web_port}")
    logger.info(f"API URL: {API_BASE_URL}")
    if settings.api_bearer_token:
        logger.info("Bearer token configured")

    # OTel FastAPI instrumentation
    try:
        instrument_fastapi(app, "demo-web")
    except Exception as exc:
        logger.warning("FastAPI instrumentation failed: %s", exc)

    yield

    # Shutdown
    logger.info("Web server shutting down")
    await http_client.aclose()
    otel_shutdown()


# Create FastAPI app
app = FastAPI(
    title="Agent-Tutor Web Frontend",
    description="Web server that serves the static frontend and acts as a reverse proxy to the Core API.",
    version="1.1.0",
    lifespan=lifespan,
)

# CORS middleware
# WEB_ORIGIN from env: comma-separated explicit origins (pentest F5: a
# wildcard — alone or mixed in — is rejected and falls back to the dev
# default; reflecting ``access-control-allow-origin: *`` made every proxied
# response readable by any web page).
_CORS_DEFAULT_ORIGINS = ["http://localhost:8080"]


def _cors_origins_from(raw: str | None) -> list[str]:
    origins = [o.strip() for o in (raw or "").split(",") if o.strip()]
    if not origins or "*" in origins:
        logger.error(
            "WEB_ORIGIN must list explicit origins; wildcard '*' is rejected."
        )
        return list(_CORS_DEFAULT_ORIGINS)
    return origins


cors_origins = _cors_origins_from(settings.web_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Tenant-ID", "X-Correlation-ID"],
)


# --- Correlation ID middleware ---
@app.middleware("http")
async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id

    # Enrich OTel span with tenant context
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

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# --- Routes ---


@app.get("/")
async def index() -> FileResponse:
    """Serve the main index.html page."""
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Web service health check."""
    return {
        "web": "ok",
        "api_base_url": API_BASE_URL,
        "token_configured": bool(settings.api_bearer_token),
    }


# --- API Reverse Proxy Routes ---


# --- Data-service reverse proxy (read-only обзор для web UI) ---

DATA_SERVICE_URL = settings.data_service_url


async def _proxy_to_data_service(
    request: Request,
    data_path: str,
) -> Response:
    """Proxy GET request to data-service напрямую (минуя api-service).

    data-service — единственный владелец БД. Web не должен идти к data-service
    через api-service, потому что это лишний hop и api-service не должен заниматься
    трансляцией данных (его ответственность — агент).
    """
    http_client = request.app.state.http_client
    url = f"{DATA_SERVICE_URL}{data_path}"
    headers = await _get_base_proxy_headers(request)
    logger.debug("data-service proxy: %s -> %s", request.method, url)
    # Прокидываем query-параметры (pattern, limit, fields и т.д.) —
    # data-service стратегии (grep/filter) требуют их.
    response = await http_client.get(
        url, headers=headers, params=dict(request.query_params)
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={
            "Content-Type": response.headers.get("Content-Type", "application/json")
        },
    )


@app.get("/api/data/stats")
async def proxy_data_stats(request: Request) -> Response:
    return await _proxy_to_data_service(request, "/stats")


@app.get("/api/manifest")
async def proxy_manifest(request: Request) -> Response:
    """Proxy to data-service /mcp/manifest — единый источник метаданных."""
    return await _proxy_to_data_service(request, "/mcp/manifest")


@app.get("/api/data/{entity_name:path}")
async def proxy_data_entity(request: Request, entity_name: str) -> Response:
    """Generic data-service proxy: /api/data/students -> GET /students, etc."""
    return await _proxy_to_data_service(request, f"/{entity_name}")


# --- RAG reverse proxy (документы для web UI) ---

RAG_SERVICE_URL = settings.rag_service_url


RAG_UNAVAILABLE_BODY = json.dumps(
    {
        "available": False,
        "warning": "RAG service is not running.",
    }
).encode()


async def _proxy_to_rag(
    request: Request,
    rag_path: str,
    method: str = "GET",
    json_body: dict | None = None,
) -> Response:
    """Proxy to RAG service with graceful fallback."""
    http_client = request.app.state.http_client
    url = f"{RAG_SERVICE_URL}{rag_path}"
    headers = await _get_base_proxy_headers(request)
    try:
        if json_body is not None:
            response = await getattr(http_client, method.lower())(
                url, json=json_body, headers=headers
            )
        else:
            response = await getattr(http_client, method.lower())(url, headers=headers)
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers={
                "Content-Type": response.headers.get("Content-Type", "application/json")
            },
        )
    except httpx.ConnectError:
        # Only connection refused / service not running → graceful 200 fallback
        return Response(
            content=RAG_UNAVAILABLE_BODY,
            status_code=200,
            headers={"Content-Type": "application/json"},
        )
    except httpx.TimeoutException:
        # Timeout → propagate as 504 Gateway Timeout
        logger.warning("RAG proxy timeout", extra={"path": rag_path})
        return Response(
            content=json.dumps({"error": "RAG service timeout"}).encode(),
            status_code=504,
            headers={"Content-Type": "application/json"},
        )


@app.get("/api/rag/documents")
async def proxy_rag_documents(request: Request) -> Response:
    """GET-обёртка над POST /documents/list в RAG-сервисе."""
    return await _proxy_to_rag(request, "/documents/list", method="POST", json_body={})


# --- API Reverse Proxy Routes (только агент: chat, sessions, backlog) ---


@app.get("/api/agents")
async def proxy_agents(request: Request) -> Response:
    """Public projection of the agent list (pentest F1).

    The demo UI selects agents by name only; llm_config (provider, model,
    masked key), system_prompt and provider priority never cross the demo
    edge, even though api-service masks the key itself.
    """
    response = await _proxy_to_api(request, "/api/agents")
    if response.status_code != 200:
        return response
    try:
        payload = json.loads(response.body)
        projected = {
            "agents": [
                {"name": agent["name"]}
                for agent in payload.get("agents", [])
                if isinstance(agent, dict) and "name" in agent
            ]
        }
    except (ValueError, TypeError):
        return response
    return Response(
        content=json.dumps(projected), status_code=200, media_type="application/json"
    )


@app.api_route(
    "/api/tenant/{tenant_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy_tenant_api(request: Request, tenant_id: str, path: str):
    """
    Special demo route: allows specifying tenant in URL.
    Example: /api/tenant/school-a/chat -> proxies to /api/chat with X-Tenant-ID: school-a
    """
    # Store tenant_id in request.state so proxy functions can access it
    request.state.tenant_id = tenant_id

    # Determine if we should proxy to data-service, rag, or api based on the path
    if path.startswith("data/"):
        return await _proxy_to_data_service(request, f"/{path.replace('data/', '', 1)}")
    elif path.startswith("rag/"):
        rag_subpath = path.replace("rag/", "", 1)
        # Special case: rag/documents -> POST /documents/list (matching /api/rag/documents behavior)
        if rag_subpath == "documents":
            return await _proxy_to_rag(
                request, "/documents/list", method="POST", json_body={}
            )
        else:
            return await _proxy_to_rag(request, f"/{rag_subpath}")
    else:
        # Default to API — но только узкий allowlist (pentest H1):
        # demo/web — публичный край, он не должен быть прокси к admin-контуру
        # api-service (agents/backlog/voice/llm-providers и т.д.) с серверным
        # bearer-токеном. Разрешены только публичные поверхности демо.
        is_sse = request.method == "POST" and (path == "chat" or path.startswith("chat/"))
        if path.startswith("api/"):
            api_path = path.replace("api/", "", 1)
            upstream = f"/{api_path}"
        else:
            api_path = path
            upstream = f"/api/{api_path}"
        allowed_api = (
            api_path == "chat"
            or api_path.startswith("chat/")
            or api_path in ("health", "reports")
            or api_path.startswith("embed/")
        )
        if not allowed_api:
            raise HTTPException(status_code=404, detail=f"Unknown API route: {path}")
        return await _proxy_to_api(request, upstream, stream=is_sse)


@app.get("/api/tenants")
async def get_tenants(request: Request) -> Response:
    """Return the demo tenant list.

    Pentest M2/H1: previously this discovered tenants from the data-service
    /health endpoint, leaking the full tenant inventory (incl. e2e-*/test-*)
    to anyone who can reach demo/web. Discovery is gone: the list comes only
    from the explicit DEMO_TENANTS env var, falling back to the configured
    default tenant.
    """
    explicit = settings.demo_tenants.strip()
    if explicit:
        return Response(
            content=json.dumps(
                {"tenants": [t.strip() for t in explicit.split(",") if t.strip()]}
            ),
            media_type="application/json",
        )
    return Response(
        content=json.dumps({"tenants": [settings.default_tenant_id]}),
        media_type="application/json",
    )


@app.get("/api/health")
async def proxy_health(request: Request) -> Response:
    return await _proxy_to_api(request, "/health")


@app.get("/api/session/history")
async def proxy_session_history(request: Request) -> Response:
    """Transcript reads authenticate with the browser's session capability
    token (pentest F4); the server bearer is deliberately not attached, so
    api-service enforces the capability per session_id."""
    session_id = request.query_params.get("session_id", "")
    agent_name = request.query_params.get("agent_name")
    path = f"/api/session/history?session_id={session_id}"
    if agent_name:
        path += f"&agent_name={agent_name}"
    return await _proxy_to_api(request, path, attach_bearer=False)


@app.post("/api/chat", response_model=None)
async def proxy_chat(request: Request):
    """Proxy the SSE chat endpoint."""
    return await _proxy_to_api(request, "/api/chat", stream=True)


@app.post("/api/chat/{agent_name}", response_model=None)
async def proxy_chat_by_agent(request: Request, agent_name: str):
    """Proxy SSE chat for a named agent."""
    return await _proxy_to_api(request, f"/api/chat/{agent_name}", stream=True)


@app.post("/api/reports", response_model=None)
async def proxy_report(request: Request):
    """Proxy the widget problem-report endpoint (non-SSE JSON POST)."""
    return await _proxy_to_api(request, "/api/reports", stream=False)


# ── Embed widget proxy (from api-service /embed) ──


@app.get("/embed/{embed_path:path}")
async def proxy_embed(request: Request, embed_path: str):
    """Proxy embed widget static files from api-service."""
    return await _proxy_to_api(request, f"/embed/{embed_path}", stream=False)


def main() -> None:
    """Run the web server."""
    import uvicorn

    uvicorn.run(
        "demo.web.server:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
