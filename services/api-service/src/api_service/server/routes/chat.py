"""Chat endpoints — text, agent-scoped, and voice."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from helperium_sdk.settings import settings
from pydantic import ValidationError

from ..rate_limit import rate_limit, limiter
from api_service.http_models import ChatRequest, VoiceAgentConfig
from api_service.error_messages import classify_error
from api_service.prometheus_metrics import chat_sessions_total, chat_messages_total
from api_service.audio.voice_config import (
    load_voice_config,
    resolve_voice_config,
)
from api_service.audio.stt_engine import STTEngine
from api_service.sessions import session_store
from ..deps import get_agent, get_agent_store
from api_service.log_config import correlation_id_var
from ..sse import _sse, _single_error, _event_payload, _get_lang
from ..security import check_abuse
from ..session_capability import (
    SESSION_TOKEN_HEADER,
    generate_session_token,
    hash_session_token,
    token_matches,
)
from ..tenant_authority import direct_chat_profile, direct_chat_scope, named_agent_scope

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


async def _reject_session_hijack(
    request: Request, effective_session_id: str, correlation_id: str
) -> tuple[str | None, StreamingResponse | None]:
    """Reject a request that reuses a token-protected session without it.

    Returns ``(stored_hash, error_response)``. ``error_response`` is set when
    the session is token-protected and the request does not present a valid
    token. Runs BEFORE the anti-abuse gate: an unauthenticated request must
    neither burn the victim's rate-limit budget nor mutate accepted-turn
    state. Sessions without a stored hash (new, or created before capability
    tokens existed) pass here and receive a minted token after the abuse gate.
    """
    stored_hash = await asyncio.to_thread(
        session_store.session_token_hash, effective_session_id
    )
    if stored_hash is None:
        return None, None
    presented = request.headers.get(SESSION_TOKEN_HEADER)
    if token_matches(stored_hash, presented):
        return stored_hash, None
    logger.warning(
        "[CHAT] session capability rejected (correlation_id=%s)", correlation_id
    )
    return stored_hash, StreamingResponse(
        _single_error(
            "Invalid or missing session token for this session.", correlation_id
        ),
        media_type="text/event-stream",
        status_code=401,
    )


async def _mint_session_token(effective_session_id: str) -> str | None:
    """Bind a fresh capability token to a session that has none yet.

    Returns the plaintext token for the client, or None when a concurrent
    request won the bind race — a token that is not the stored one must never
    be handed out, because it would be rejected on the next turn.
    """
    token = generate_session_token()
    stored_hash = await asyncio.to_thread(
        session_store.bind_session_token,
        effective_session_id,
        hash_session_token(token),
    )
    if stored_hash != hash_session_token(token):
        return None
    return token


def _missing_session_error(correlation_id: str) -> StreamingResponse:
    return StreamingResponse(
        _single_error("Missing session_id.", correlation_id),
        media_type="text/event-stream",
        status_code=400,
    )


async def _capability_sse_response(
    source_factory: Callable[[], AsyncIterator[Any]],
    *,
    lang: str,
    correlation_id: str,
    session_id: str,
    stored_hash: str | None,
    effective_session_id: str,
    watcher: _DisconnectWatch,
) -> StreamingResponse:
    """Build the chat SSE response with the session capability handshake.

    Mints the capability token only after the abuse gate (a rate-limited
    client must not lose a token it never received), emits it as the first
    ``session`` event plus the ``X-Session-Token`` header, then streams the
    buffered agent producer with the terminal-event semantics shared by all
    chat paths.
    """
    minted_token = (
        None
        if stored_hash is not None
        else await _mint_session_token(effective_session_id)
    )
    chat_sessions_total.inc()

    async def events():
        correlation_id_var.set(correlation_id)
        if minted_token:
            yield _sse(
                {
                    "type": "session",
                    "session_id": session_id,
                    "session_token": minted_token,
                }
            )
        try:
            async for payload in _buffered_agent_sse_events(
                source_factory(), lang, correlation_id, disconnect_check=watcher.check
            ):
                yield payload
            chat_messages_total.labels(status="sent").inc()
        finally:
            await watcher.stop()

    headers = {"X-Session-Token": minted_token} if minted_token else None
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


def _parse_chat_request(
    body: Any, correlation_id: str
) -> ChatRequest | StreamingResponse:
    """Parse the chat body, naming session_id problems explicitly.

    ``session_id`` is a required model field: a request without one must be
    rejected at the door instead of silently joining a shared transcript.
    """
    try:
        return ChatRequest(**body)
    except ValidationError as exc:
        if any(error.get("loc") == ("session_id",) for error in exc.errors()):
            return _missing_session_error(correlation_id)
        return StreamingResponse(
            _single_error("Invalid request body.", correlation_id),
            media_type="text/event-stream",
        )


class _DisconnectWatch:
    """Polls ``request.is_disconnected()`` in the background and latches True.

    StreamingResponse cancels the writer task on client disconnect, but the
    agent loop needs a cancellation-independent signal: SDK transport blips can
    surface as CancelledError inside tool calls even though the client is alive,
    while genuine disconnects may be swallowed by the time the producer checks.
    The latch is also the discriminator between those two cases.
    """

    def __init__(self, request: Request) -> None:
        self._request = request
        self._event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def disconnected(self) -> bool:
        return self._event.is_set()

    def check(self) -> bool:
        """Zero-arg probe passed down to stream_events/call_tool."""
        return self._event.is_set()

    async def _watch(self) -> None:
        try:
            while not self._event.is_set():
                if await self._request.is_disconnected():
                    self._event.set()
                    logger.warning(
                        "[SSE] client disconnected, latching stream cancel "
                        "(correlation_id=%s)",
                        correlation_id_var.get() or "unknown",
                    )
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            # Writer task is being torn down — that itself means the response
            # is finished; treat as potential disconnect only if cancelled from
            # outside. Conservative: writer teardown happens on normal finish
            # too. Do NOT latch here.
            raise

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._watch(), name=f"sse-disconnect-watch-{id(self):x}"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


async def _buffered_agent_sse_events(
    source: AsyncIterator[Any],
    lang: str,
    correlation_id: str,
    disconnect_check: Callable[[], bool] | None = None,
) -> AsyncIterator[str]:
    """Keep MCP transport cancellation inside a producer task, not the SSE writer.

    Streamable MCP uses a cancellation scope for its background GET receiver.
    When that receiver dies, it can cancel the task driving the agent iterator.
    A separate producer with an empty context turns that failure into a queued
    terminal SSE error while the request's writer task remains able to flush it.
    """

    queue: asyncio.Queue[tuple[str, Any | None]] = asyncio.Queue()

    async def produce() -> None:
        # The producer task runs in an empty copied context; restore the
        # correlation id so MCP/agent log lines below stay attributable.
        correlation_id_var.set(correlation_id)
        try:
            async for event in source:
                # Bail out early once the client is gone: the agent loop also
                # checks this between iterations, but a long provider stream
                # would otherwise keep running until its next await.
                if disconnect_check is not None and disconnect_check():
                    logger.warning(
                        "[SSE] producer stopping after disconnect latch "
                        "(correlation_id=%s), emitting terminal done",
                        correlation_id,
                    )
                    # Always emit a terminal event before exiting: the consumer
                    # blocks on queue.get() and has no other way to observe a
                    # silent producer return.
                    await queue.put(("done", None))
                    return
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
                payload = _event_payload(value.type, value.data, correlation_id)
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
    except Exception:
        return StreamingResponse(
            _single_error("Invalid request body.", correlation_id),
            media_type="text/event-stream",
        )
    parsed = _parse_chat_request(body, correlation_id)
    if isinstance(parsed, StreamingResponse):
        return parsed
    chat_req = parsed

    message = chat_req.message
    session_id = chat_req.session_id
    tenant_ids = direct_chat_scope()
    # Optional admin-managed quality profile (prompt/provider pin) for direct
    # chat. Tenant scope stays server-configured; profile only affects the
    # system prompt and provider selection.
    profile = direct_chat_profile()

    if not message:
        return StreamingResponse(
            _single_error("Empty message.", correlation_id),
            media_type="text/event-stream",
        )

    effective_session_id = f"direct:{session_id}"

    # Session capability first: no anti-abuse accounting for unauthenticated
    # reuse of someone else's session id.
    stored_hash, hijack_error = await _reject_session_hijack(
        request, effective_session_id, correlation_id
    )
    if hijack_error is not None:
        return hijack_error

    abuse_result = await check_abuse(request, session_id, message)
    if abuse_result is not None:
        return abuse_result

    lang = _get_lang(request)
    watcher = _DisconnectWatch(request)
    watcher.start()

    return await _capability_sse_response(
        lambda: get_agent().stream_events(
            message,
            session_id=effective_session_id,
            tenant_ids=tenant_ids,
            llm_config=profile.llm_config if profile else None,
            provider_priority=profile.provider_priority if profile else None,
            system_prompt=profile.system_prompt if profile else None,
            lang=lang,
            correlation_id=correlation_id,
            disconnect_check=watcher.check,
            principal_id=(
                f"agent:{settings.direct_chat_agent}"
                if settings.direct_chat_agent
                else "account:default"
            ),
        ),
        lang=lang,
        correlation_id=correlation_id,
        session_id=session_id,
        stored_hash=stored_hash,
        effective_session_id=effective_session_id,
        watcher=watcher,
    )


# ── Voice Chat ──────────────────────────────────────────────────────────


@router.post("/api/chat/voice")
@limiter.limit(rate_limit)
async def chat_voice_endpoint(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form(...),
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

    # An explicitly requested agent must exist. Silence here would silently
    # downgrade a tenant-scoped voice request to the direct-chat scope.
    agent_data = None
    if agent:
        agent_data = await asyncio.to_thread(get_agent_store().get_agent, agent)
        if not agent_data:
            return StreamingResponse(
                _single_error(f"Agent '{agent}' not found", correlation_id),
                media_type="text/event-stream",
                status_code=404,
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

    tenant_ids = named_agent_scope(agent_data) if agent_data else direct_chat_scope()
    # Optional admin-managed quality profile for agent-less voice requests;
    # tenant scope stays server-configured either way.
    profile = None if agent_data else direct_chat_profile()

    if agent:
        effective_session_id = f"agent:{agent}:{session_id}"
    else:
        effective_session_id = f"direct:{session_id}"

    request_lang = lang or _get_lang(request)

    # Session capability gate, same contract as the text endpoints.
    stored_hash, hijack_error = await _reject_session_hijack(
        request, effective_session_id, correlation_id
    )
    if hijack_error is not None:
        return hijack_error

    # Same anti-abuse gate as the text and named-agent paths: session quota,
    # duplicate-message and UA checks all apply to transcribed voice input.
    abuse_result = await check_abuse(
        request,
        session_id,
        text,
        agent_data.get("abuse_config") if agent_data else None,
    )
    if abuse_result is not None:
        return abuse_result

    resolved_llm_config = None
    provider_priority = None
    system_prompt = None
    if agent_data:
        provider_priority = agent_data.get("provider_priority") or None
        resolved_llm_config = agent_data.get("llm_config")
        system_prompt = agent_data.get("system_prompt") or (
            resolved_llm_config.get("system_prompt") if resolved_llm_config else None
        )
    elif profile:
        provider_priority = profile.provider_priority
        resolved_llm_config = profile.llm_config
        system_prompt = profile.system_prompt

    watcher = _DisconnectWatch(request)
    watcher.start()

    # Buffered producer, same as the text and named-agent paths: MCP transport
    # cancellation must not kill the SSE writer, and every producer failure is
    # classified into a terminal error + done pair.
    return await _capability_sse_response(
        lambda: get_agent().stream_events(
            user_message=text,
            session_id=effective_session_id,
            tenant_ids=tenant_ids,
            system_prompt=system_prompt,
            lang=request_lang,
            llm_config=resolved_llm_config,
            provider_priority=provider_priority,
            correlation_id=correlation_id,
            disconnect_check=watcher.check,
        ),
        lang=request_lang,
        correlation_id=correlation_id,
        session_id=session_id,
        stored_hash=stored_hash,
        effective_session_id=effective_session_id,
        watcher=watcher,
    )


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
    except Exception:
        return StreamingResponse(
            _single_error("Invalid request body.", correlation_id),
            media_type="text/event-stream",
        )
    parsed = _parse_chat_request(body, correlation_id)
    if isinstance(parsed, StreamingResponse):
        return parsed
    chat_req = parsed

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

    effective_session_id = f"agent:{name}:{session_id}"

    # Session capability first: no anti-abuse accounting for unauthenticated
    # reuse of someone else's session id.
    stored_hash, hijack_error = await _reject_session_hijack(
        request, effective_session_id, correlation_id
    )
    if hijack_error is not None:
        return hijack_error

    agent_abuse_config = agent.get("abuse_config")
    abuse_result = await check_abuse(request, session_id, message, agent_abuse_config)
    if abuse_result is not None:
        return abuse_result

    provider_priority = agent.get("provider_priority") or None
    resolved_llm_config = agent.get("llm_config")

    lang = _get_lang(request)
    watcher = _DisconnectWatch(request)
    watcher.start()

    return await _capability_sse_response(
        lambda: get_agent().stream_events(
            user_message=message,
            session_id=effective_session_id,
            tenant_ids=tenant_ids,
            system_prompt=system_prompt,
            lang=lang,
            llm_config=resolved_llm_config,
            provider_priority=provider_priority,
            correlation_id=correlation_id,
            disconnect_check=watcher.check,
            principal_id=f"agent:{name}",
        ),
        lang=lang,
        correlation_id=correlation_id,
        session_id=session_id,
        stored_hash=stored_hash,
        effective_session_id=effective_session_id,
        watcher=watcher,
    )
