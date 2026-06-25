from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Callable, Awaitable
from uuid import uuid4

import uvicorn

from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from demo.api.agent.orchestrator import LLMAgent
from demo.api.agent.types import AgentEventData
from demo.api.backlog import backlog
from demo.api.data import data_repository
from demo.api.sessions import session_store
from demo.settings import settings
from demo.api.http_models import (
    BacklogDetailResponse,
    BacklogListResponse,
    ChatRequest,
    DataOverviewResponse,
    HealthResponse,
    SessionHistoryResponse,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("demo.api.server")

# Enable debug logging for agent if DEMO_DEBUG is set
if os.environ.get("DEMO_DEBUG", "").lower() in ("1", "true", "yes"):
    logging.getLogger("demo.api.agent").setLevel(logging.DEBUG)
    logging.getLogger("mcp").setLevel(logging.DEBUG)
    print("[DEMO] Debug logging enabled for agent and MCP")


# === Lazy agent singleton (init на первом запросе, а не при импорте) ===
_agent_instance: LLMAgent | None = None
_agent_lock = threading.Lock()


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


async def get_data() -> DataOverviewResponse:
    """Get demo data overview."""
    return DataOverviewResponse(data=data_repository.overview())


async def get_backlog_list() -> BacklogListResponse:
    """List all backlog sessions."""
    return BacklogListResponse(sessions=backlog.list_sessions())


async def get_backlog_detail(
    session_id: str, limit: int = 500, offset: int = 0
) -> BacklogDetailResponse:
    """Read records of a specific backlog session."""
    records = backlog.read_session(session_id, limit=limit, offset=offset)
    return BacklogDetailResponse(
        records=records, session_id=session_id, count=len(records)
    )


async def get_session_history(session_id: str = "default") -> SessionHistoryResponse:
    """Get chat history for a session."""
    history = session_store.history_messages(session_id)
    return SessionHistoryResponse(messages=history)


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

    if not message:
        return StreamingResponse(
            _single_error("Введите вопрос."), media_type="text/event-stream"
        )

    if not session_id:
        return StreamingResponse(
            _single_error("Введите session_id."), media_type="text/event-stream"
        )

    async def events():
        try:
            async for event in get_agent().stream_events(
                message, session_id=session_id
            ):
                payload = _event_payload(event.type, event.data)
                if payload is not None:
                    yield _sse(payload)
            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse({"type": "error", "text": _format_error(exc)})

    return StreamingResponse(events(), media_type="text/event-stream")


async def _single_error(text: str):
    """Yield a single error event."""
    yield _sse({"type": "error", "text": text})


def _sse(payload: dict[str, Any]) -> str:
    """Format a payload as a Server-Sent Event."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_payload(event_type: str, data: AgentEventData) -> dict[str, Any] | None:
    """Convert internal agent events to the browser-facing SSE payload."""
    logger.info(f"[SERVER] event_type: {event_type}, data: {str(data)[:200]}")
    if event_type == "token":
        return {"type": "token", "text": data.get("data")}
    if event_type == "final":
        text = data.get("content") if isinstance(data, dict) else ""
        return {"type": "final", "text": text}
    if event_type == "tool_call":
        name = data.get("name") if isinstance(data, dict) else ""
        return {"type": "tool_call", "name": name}
    if event_type == "tool_result":
        name = data.get("name") if isinstance(data, dict) else ""
        return {"type": "tool_result", "name": name}
    if event_type == "error":
        text = data.get("message") if isinstance(data, dict) else data
        return {"type": "error", "text": text}
    return None


def _format_error(exc: BaseException) -> str:
    """Format an exception for error reporting."""
    if isinstance(exc, ExceptionGroup):
        return "; ".join(_format_error(item) for item in exc.exceptions)
    return str(exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — инициализация и cleanup агента."""
    # Startup: прогреваем агента
    logger.info("Warming up LLM agent...")
    try:
        get_agent()  # lazy init при старте, а не при первом запросе
        logger.info("LLM agent ready")
    except Exception as exc:
        logger.warning("Agent warmup failed (will retry on first request): %s", exc)

    yield

    # Shutdown
    logger.info("API server shutting down")


# Create the API application
app = FastAPI(
    title="Agent-Tutor Core API",
    description="Backend API for the Agent-Tutor system. Handles orchestration, session history, and tool integration.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --- Correlation ID middleware ---
@app.middleware("http")
async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id
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


@app.get(
    "/api/data",
    response_model=DataOverviewResponse,
    summary="Обзор данных",
    description="Возвращает сводную информацию о доступных данных университета.",
)
async def data_endpoint():
    return await get_data()


@app.post(
    "/api/chat",
    summary="Стриминг чата",
    description="Основной эндпоинт для общения с агентом. Возвращает поток SSE событий.",
)
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
    "/api/session/history",
    response_model=SessionHistoryResponse,
    summary="История сессии",
    description="Возвращает историю сообщений для указанной сессии.",
)
async def session_history_endpoint(session_id: str = Query("default")):
    return await get_session_history(session_id)


def main() -> None:
    """Run the API server."""
    # Cleanup old backlog files on each startup
    try:
        backlog.cleanup_old()
        logger.info("Backlog cleanup completed")
    except Exception as exc:
        logger.warning("Backlog cleanup failed: %s", exc)

    uvicorn.run(
        "demo.api.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
