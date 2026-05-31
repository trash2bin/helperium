from __future__ import annotations

import json
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from demo.data import DemoDataRepository
from demo.ollama_client import OllamaAssistant
from demo.settings import settings


assistant = OllamaAssistant()
data_repository = DemoDataRepository()


async def health(_: Request) -> JSONResponse:
    payload: dict[str, Any] = {"api": "ok"}
    try:
        payload["ollama"] = await assistant.health()
    except Exception as exc:
        payload["ollama"] = {"status": "error", "error": str(exc)}
    return JSONResponse(payload)


async def data(_: Request) -> JSONResponse:
    return JSONResponse(data_repository.overview())


async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        return StreamingResponse(_single_error("Введите вопрос."), media_type="text/event-stream")

    async def events():
        try:
            async for token in assistant.stream_answer(message):
                yield _sse({"type": "token", "text": token})
            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse({"type": "error", "text": _format_error(exc)})

    return StreamingResponse(events(), media_type="text/event-stream")


async def _single_error(text: str):
    yield _sse({"type": "error", "text": text})


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_error(exc: BaseException) -> str:
    if isinstance(exc, ExceptionGroup):
        return "; ".join(_format_error(item) for item in exc.exceptions)
    return str(exc)


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/api/data", data),
        Route("/api/chat", chat, methods=["POST"]),
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def main() -> None:
    uvicorn.run("demo.api:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
