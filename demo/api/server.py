from __future__ import annotations

import json
import logging
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from demo.api.agent import agent
from demo.api.agent.types import AgentEventData
from demo.api.backlog import backlog
from demo.api.data import data_repository
from demo.api.sessions import session_store
from demo.settings import settings

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


async def health(_: Request) -> JSONResponse:
    """Health check endpoint."""
    payload: dict[str, Any] = {"api": "ok"}
    try:
        payload["ollama"] = await agent.health()
    except Exception as exc:
        payload["ollama"] = {"status": "error", "error": str(exc)}
    return JSONResponse(payload)


async def data(_: Request) -> JSONResponse:
    """Get demo data overview."""
    return JSONResponse(data_repository.overview())


async def backlog_list(_: Request) -> JSONResponse:
    """List all backlog sessions."""
    return JSONResponse(backlog.list_sessions())


async def backlog_detail(request: Request, session_id: str) -> JSONResponse:
    """Read records of a specific backlog session."""
    limit = int(request.query_params.get("limit", "500"))
    offset = int(request.query_params.get("offset", "0"))
    return JSONResponse(backlog.read_session(session_id, limit=limit, offset=offset))


async def session_history(request: Request, session_id: str = "default") -> JSONResponse:
    """Get chat history for a session."""
    session_id = str(session_id or request.query_params.get("session_id", "")) or "default"
    history = session_store.history_messages(session_id)
    return JSONResponse({"messages": history})


async def chat(request: Request) -> StreamingResponse:
    """Streaming chat endpoint that handles user messages."""
    body = await request.json()
    message = str(body.get("message", "")).strip()
    session_id = str(body.get("session_id", "")).strip() or "default"
    if not message:
        return StreamingResponse(_single_error("Введите вопрос."), media_type="text/event-stream")

    async def events():
        try:
            async for event in agent.stream_events(message, session_id=session_id):
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
    logger.info(f"[SERVER] event_type: {event_type}, data: {str(data)[:20]}")
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


# Create the API application
app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# Register routes
@app.get("/health")
async def health_endpoint(request: Request) -> JSONResponse:
    return await health(request)


@app.get("/api/data")
async def data_endpoint(request: Request) -> JSONResponse:
    return await data(request)


@app.post("/api/chat")
async def chat_endpoint(request: Request) -> StreamingResponse:
    return await chat(request)


@app.get("/api/backlog")
async def backlog_list_endpoint(request: Request) -> JSONResponse:
    return await backlog_list(request)


@app.get("/api/backlog/{session_id}")
async def backlog_detail_endpoint(request: Request, session_id: str) -> JSONResponse:
    return await backlog_detail(request, session_id)


@app.get("/api/session/history")
async def session_history_endpoint(request: Request, session_id: str = "default") -> JSONResponse:
    return await session_history(request, session_id)


def main() -> None:
    """Run the API server."""
    uvicorn.run("demo.api.server:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
