"""Chat endpoints — text, agent-scoped, and voice."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from ..rate_limit import rate_limit, limiter
from api_service.http_models import ChatRequest, VoiceAgentConfig
from api_service.error_messages import classify_error
from api_service.prometheus_metrics import chat_sessions_total, chat_messages_total
from api_service.audio.voice_config import (
    load_voice_config,
    resolve_voice_config,
)
from api_service.audio.stt_engine import STTEngine
from ..deps import get_agent, get_agent_store
from ..sse import _sse, _single_error, _event_payload, _get_lang
from ..security import check_abuse
from ..tenant_authority import direct_chat_scope, named_agent_scope

logger = logging.getLogger("api_service.server")
router = APIRouter()


def _correlation_id(request: Request) -> str:
    """Extract correlation id from request state, falling back to a fresh UUID."""
    cid = getattr(request.state, "correlation_id", None)
    return cid if isinstance(cid, str) and cid else str(uuid4())


def _backend_stream_interrupted_message(lang: str) -> str:
    """Stable, non-sensitive message for an interrupted internal dependency."""
    if lang == "ru":
        return "Соединение с сервисом данных прервано. Пожалуйста, попробуйте ещё раз."
    return "The data service connection was interrupted. Please try again."


async def _buffered_agent_sse_events(
    source: AsyncIterator[Any], lang: str, correlation_id: str
) -> AsyncIterator[str]:
    """Keep MCP transport cancellation inside a producer task, not the SSE writer.

    Streamable MCP uses a cancellation scope for its background GET receiver.
    When that receiver dies, it can cancel the task driving the agent iterator.
    A separate producer with an empty context turns that failure into a queued
    terminal SSE error while the request's writer task remains able to flush it.
    """

    queue: asyncio.Queue[tuple[str, Any | None]] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in source:
                await queue.put(("event", event))
        except asyncio.CancelledError:
            await queue.put(("interrupted", None))
        except Exception as exc:
            await queue.put(("error", exc))
        else:
            await queue.put(("done", None))

    producer = asyncio.create_task(produce(), context=contextvars.Context())
    try:
        while True:
            kind, value = await queue.get()
            if kind == "event":
                assert value is not None
                payload = _event_payload(value.type, value.data)
                if payload is not None:
                    yield _sse(payload)
                continue
            if kind == "done":
                yield _sse({"type": "done"})
                return
            if kind == "interrupted":
                logger.warning("chat agent producer interrupted by backend dependency")
                yield _sse(
                    {
                        "type": "error",
                        "text": _backend_stream_interrupted_message(lang),
                        "correlation_id": correlation_id,
                    }
                )
                yield _sse({"type": "done"})
                return

            assert isinstance(value, Exception)
            logger.warning("chat agent producer failed", exc_info=value)
            yield _sse(
                {
                    "type": "error",
                    "text": classify_error(value, lang),
                    "correlation_id": correlation_id,
                }
            )
            yield _sse({"type": "done"})
            return
    finally:
        if not producer.done():
            producer.cancel()


# ── Chat endpoints ──────────────────────────────────────────────────────


@router.post("/api/chat")
@limiter.limit(rate_limit)
async def chat_endpoint(request: Request) -> StreamingResponse:
    correlation_id = _correlation_id(request)

    try:
        body = await request.json()
        chat_req = ChatRequest(**body)
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Invalid request body: {exc}", correlation_id),
            media_type="text/event-stream",
        )

    message = chat_req.message
    session_id = chat_req.session_id
    tenant_ids = direct_chat_scope()

    if not message:
        return StreamingResponse(
            _single_error("Empty message.", correlation_id),
            media_type="text/event-stream",
        )
    if not session_id:
        return StreamingResponse(
            _single_error("Missing session_id.", correlation_id),
            media_type="text/event-stream",
        )

    abuse_result = await check_abuse(request, session_id, message)
    if abuse_result is not None:
        return abuse_result

    effective_session_id = f"direct:{session_id}"
    chat_sessions_total.inc()
    lang = _get_lang(request)

    async def events():
        source = get_agent().stream_events(
            message,
            session_id=effective_session_id,
            tenant_ids=tenant_ids,
            lang=lang,
            correlation_id=correlation_id,
        )
        async for payload in _buffered_agent_sse_events(source, lang, correlation_id):
            yield payload
        chat_messages_total.labels(status="sent").inc()

    return StreamingResponse(events(), media_type="text/event-stream")


# ── Voice Chat ──────────────────────────────────────────────────────────


@router.post("/api/chat/voice")
@limiter.limit(rate_limit)
async def chat_voice_endpoint(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
    agent: str | None = Form(None),
    lang: str | None = Form(None),
) -> StreamingResponse:
    correlation_id = _correlation_id(request)

    try:
        audio_bytes = await audio.read()
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Failed to read audio: {exc}", correlation_id),
            media_type="text/event-stream",
        )

    if not audio_bytes:
        return StreamingResponse(
            _single_error("Empty audio file", correlation_id),
            media_type="text/event-stream",
        )

    voice_config = load_voice_config()
    if not voice_config.enabled:
        return StreamingResponse(
            _single_error("Voice input is disabled", correlation_id),
            media_type="text/event-stream",
        )

    if len(audio_bytes) > voice_config.max_voice_message_size:
        size_mb = len(audio_bytes) / (1024 * 1024)
        max_mb = voice_config.max_voice_message_size / (1024 * 1024)
        return StreamingResponse(
            _single_error(
                f"Audio too large: {size_mb:.1f}MB > {max_mb:.0f}MB", correlation_id
            ),
            media_type="text/event-stream",
        )

    resolved_config = voice_config
    agent_data = None
    if agent:
        agent_data = await asyncio.to_thread(get_agent_store().get_agent, agent)
        if agent_data:
            agent_voice_config = agent_data.get("voice_config")
            if agent_voice_config:
                agent_voice_obj = VoiceAgentConfig(**agent_voice_config)
                resolved_config = resolve_voice_config(voice_config, agent_voice_obj)

    if not resolved_config.enabled:
        return StreamingResponse(
            _single_error("Voice input is disabled", correlation_id),
            media_type="text/event-stream",
        )

    stt_engine = STTEngine.from_config(resolved_config)
    try:
        stt_result = await stt_engine.transcribe(audio_bytes)
    except RuntimeError as exc:
        return StreamingResponse(
            _single_error(classify_error(exc, lang or "ru"), correlation_id),
            media_type="text/event-stream",
        )
    except Exception as exc:
        return StreamingResponse(
            _single_error(classify_error(exc, lang or "ru"), correlation_id),
            media_type="text/event-stream",
        )

    text = stt_result.text
    if not text.strip():
        return StreamingResponse(
            _single_error("No speech detected", correlation_id),
            media_type="text/event-stream",
        )

    tenant_ids = named_agent_scope(agent_data) if agent_data else None
    if not tenant_ids:
        tenant_ids = direct_chat_scope()

    if agent:
        effective_session_id = f"agent:{agent}:{session_id}"
    else:
        effective_session_id = f"direct:{session_id}"

    request_lang = lang or _get_lang(request)
    chat_sessions_total.inc()

    resolved_llm_config = None
    provider_priority = None
    system_prompt = None
    if agent_data:
        provider_priority = agent_data.get("provider_priority") or None
        resolved_llm_config = agent_data.get("llm_config")
        system_prompt = agent_data.get("system_prompt") or (
            resolved_llm_config.get("system_prompt") if resolved_llm_config else None
        )

    async def events():
        try:
            async for event in get_agent().stream_events(
                user_message=text,
                session_id=effective_session_id,
                tenant_ids=tenant_ids,
                system_prompt=system_prompt,
                lang=request_lang,
                llm_config=resolved_llm_config,
                provider_priority=provider_priority,
                correlation_id=correlation_id,
            ):
                payload = _event_payload(event.type, event.data)
                if payload is not None:
                    yield _sse(payload)
            chat_messages_total.labels(status="sent").inc()
            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse(
                {
                    "type": "error",
                    "text": classify_error(exc, request_lang),
                    "correlation_id": correlation_id,
                }
            )

    return StreamingResponse(events(), media_type="text/event-stream")


# ── Chat by agent name ─────────────────────────────────────────────────


@router.post("/api/chat/{name}")
@limiter.limit(rate_limit)
async def chat_agent_handler(request: Request, name: str) -> StreamingResponse:
    correlation_id = _correlation_id(request)

    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        return StreamingResponse(
            _single_error(f"Agent '{name}' not found", correlation_id),
            media_type="text/event-stream",
            status_code=404,
        )

    try:
        body = await request.json()
        chat_req = ChatRequest(**body)
    except Exception as exc:
        return StreamingResponse(
            _single_error(f"Invalid request body: {exc}", correlation_id),
            media_type="text/event-stream",
        )

    message = chat_req.message
    session_id = chat_req.session_id
    tenant_ids = named_agent_scope(agent)
    llm_config = agent.get("llm_config")
    system_prompt = agent.get("system_prompt") or (
        llm_config.get("system_prompt") if llm_config else None
    )

    if not message:
        return StreamingResponse(
            _single_error("Empty message.", correlation_id),
            media_type="text/event-stream",
        )
    if not session_id:
        return StreamingResponse(
            _single_error("Missing session_id.", correlation_id),
            media_type="text/event-stream",
        )

    agent_abuse_config = agent.get("abuse_config")
    abuse_result = await check_abuse(request, session_id, message, agent_abuse_config)
    if abuse_result is not None:
        return abuse_result

    provider_priority = agent.get("provider_priority") or None
    resolved_llm_config = agent.get("llm_config")

    effective_session_id = f"agent:{name}:{session_id}"
    chat_sessions_total.inc()
    lang = _get_lang(request)

    async def events():
        source = get_agent().stream_events(
            user_message=message,
            session_id=effective_session_id,
            tenant_ids=tenant_ids,
            system_prompt=system_prompt,
            lang=lang,
            llm_config=resolved_llm_config,
            provider_priority=provider_priority,
            correlation_id=correlation_id,
        )
        async for payload in _buffered_agent_sse_events(source, lang, correlation_id):
            yield payload
        chat_messages_total.labels(status="sent").inc()

    return StreamingResponse(events(), media_type="text/event-stream")
