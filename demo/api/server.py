from __future__ import annotations

import json
import logging
import os
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from demo.api.agent import agent
from demo.api.backlog import backlog
from demo.api.data import data_repository
from demo.settings import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

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


async def backlog_detail(request: Request) -> JSONResponse:
    """Read records of a specific backlog session."""
    session_id = request.path_params.get("session_id", "")
    limit = int(request.query_params.get("limit", "500"))
    offset = int(request.query_params.get("offset", "0"))
    return JSONResponse(backlog.read_session(session_id, limit=limit, offset=offset))


async def chat(request: Request) -> StreamingResponse:
    """Streaming chat endpoint that handles user messages."""
    body = await request.json()
    message = str(body.get("message", "")).strip()
    session_id = str(body.get("session_id", "")).strip() or "default"
    if not message:
        return StreamingResponse(_single_error("Введите вопрос."), media_type="text/event-stream")

    async def events():
        try:
            async for token in agent.stream_answer(message, session_id=session_id):
                yield _sse({"type": "token", "text": token})
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


def _format_error(exc: BaseException) -> str:
    """Format an exception for error reporting."""
    if isinstance(exc, ExceptionGroup):
        return "; ".join(_format_error(item) for item in exc.exceptions)
    return str(exc)


# Create the API application
app = Starlette(
    routes=[
        Route("/health", health),
        Route("/api/data", data),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/backlog", backlog_list),
        Route("/api/backlog/{session_id}", backlog_detail),
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def main() -> None:
    """Run the API server."""
    uvicorn.run("demo.api.server:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
