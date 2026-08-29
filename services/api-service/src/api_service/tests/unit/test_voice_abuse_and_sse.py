"""Regression tests: voice endpoint anti-abuse, SSE parity, unknown-agent 404.

Covers audit findings (doc/archive/product-demo-readiness-audit-2026-08-28,
verified against head 53a3172, tracked in repo-root todo.md R1-R3):

- R1: ``chat_voice_endpoint`` never called ``check_abuse`` — the text and
  named-agent paths call it before streaming; voice bypassed session quota,
  duplicate-message and UA checks entirely.
- R2: voice streamed raw ``stream_events`` instead of the buffered producer
  used by the other chat paths, so a producer crash yields an error event
  without the terminal ``done`` event and diverges in error semantics.
- R3: an unknown agent name silently fell back to the direct-chat scope and
  kept processing (and paying for STT) instead of returning 404 like the
  text path.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_vc():
    vc = MagicMock()
    vc.enabled = True
    vc.max_voice_message_size = 10485760
    vc.stt_fallback_enabled = True
    vc.stt_providers = []
    return vc


def _make_req():
    from fastapi import Request

    req = MagicMock(spec=Request)
    req.headers.get.return_value = ""
    # spec'd async methods become AsyncMock; make sure the disconnect watcher
    # sees a connected client instead of a truthy MagicMock.
    req.is_disconnected = AsyncMock(return_value=False)
    return req


async def _collect(response) -> str:
    """Return the full SSE body as one string (forces events() to run)."""
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _make_stream_agent(recorder=None, error=None):
    """Mock agent whose stream_events records kwargs and optionally raises.

    ``stream_events`` is a plain function on the agent protocol (not an
    AsyncMock), so call-counting goes through ``recorder``.
    """

    async def _stream(**kwargs):
        if recorder is not None:
            recorder.update(kwargs)
        if error is not None:
            raise error
        # AgentEventData is attribute-based (event.type / event.data).
        yield MagicMock(type="final", data={"content": "OK"})

    mock_agent = MagicMock()
    mock_agent.stream_events = _stream
    return mock_agent


def _agent_store(agent_record):
    store = MagicMock()
    store.get_agent.return_value = agent_record
    return store


_AGENT_RECORD = {
    "name": "test-agent",
    "tenant_ids": ["autoparts"],
    "provider_priority": [],
    "llm_config": None,
    "system_prompt": None,
    "voice_config": None,
    "abuse_config": {"max_user_turns_per_session": 3},
}


def _voice_patches(mock_agent, *, store=None):
    """Patch context manager args covering the whole STT/store seam."""
    return [
        patch(
            "api_service.server.routes.chat.get_agent_store",
            return_value=store if store is not None else _agent_store(_AGENT_RECORD),
        ),
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch(
            "api_service.server.routes.chat.load_voice_config", return_value=_make_vc()
        ),
        patch(
            "api_service.server.routes.chat.resolve_voice_config",
            return_value=_make_vc(),
        ),
        patch("api_service.server.routes.chat.STTEngine"),
    ]


async def _run_voice(agent="test-agent", *, stt_text="test", **extra):
    from api_service.server import chat_voice_endpoint

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(MagicMock(), **{"store": None} if False else {}):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text=stt_text, provider_name="stt")
        )
        return await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent=agent,
            lang="ru",
            **extra,
        )


# ── R1: check_abuse must run on the voice path ──────────────────────────


@pytest.mark.asyncio
async def test_voice_calls_check_abuse_with_transcribed_text():
    from api_service.server import chat_voice_endpoint

    recorder: dict = {}
    mock_agent = _make_stream_agent(recorder=recorder)
    abuse = AsyncMock(return_value=None)

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(mock_agent):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="какой у нас ассортимент", provider_name="stt")
        )
        stack.enter_context(patch("api_service.server.routes.chat.check_abuse", abuse))

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        # Drain the stream so the handler pipeline completes.
        async for _ in result.body_iterator:
            pass

    assert result.status_code == 200
    abuse.assert_awaited_once()
    args, kwargs = abuse.await_args
    # Parity with the text/named-agent paths: (request, session_id, message).
    assert args[1] == "s1"
    assert args[2] == "какой у нас ассортимент"
    # A named voice agent passes its own abuse config, like chat_agent_handler.
    passed_abuse_config = kwargs.get("agent_abuse_config")
    if passed_abuse_config is None and len(args) > 3:
        passed_abuse_config = args[3]
    assert passed_abuse_config == {"max_user_turns_per_session": 3}


@pytest.mark.asyncio
async def test_voice_check_abuse_rejection_blocks_pipeline():
    from fastapi.responses import StreamingResponse

    from api_service.server import chat_voice_endpoint
    from api_service.server.sse import _single_error

    recorder: dict = {}
    mock_agent = _make_stream_agent(recorder=recorder)

    async def _reject(request, session_id, message, agent_abuse_config=None):
        return StreamingResponse(
            _single_error("Quota exceeded"),
            media_type="text/event-stream",
            status_code=429,
        )

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(mock_agent):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        # STT runs before the abuse gate (it produces the checked text).
        stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="какой у нас ассортимент", provider_name="stt")
        )
        stack.enter_context(
            patch(
                "api_service.server.routes.chat.check_abuse",
                AsyncMock(side_effect=_reject),
            )
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        chunks = [chunk async for chunk in result.body_iterator]

    body = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    assert result.status_code == 429
    assert "Quota exceeded" in body
    # STT runs first (the abuse check needs the transcribed text), but the
    # rejected request must never reach the agent pipeline.
    stt_cls.from_config.return_value.transcribe.assert_called_once()
    assert recorder == {}


# ── R3: unknown agent name must 404, not fall back to direct scope ──────


@pytest.mark.asyncio
async def test_voice_unknown_agent_returns_404():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_stream_agent()
    recorder: dict = {}
    mock_agent = _make_stream_agent(recorder=recorder)

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(mock_agent, store=_agent_store(None)):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        stt_cls.from_config.return_value.transcribe = AsyncMock()

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="ghost-agent",
            lang="ru",
        )
        chunks = [chunk async for chunk in result.body_iterator]

    body = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    assert result.status_code == 404
    assert "ghost-agent" in body
    # No transcription cost and no agent run for an invalid agent name.
    stt_cls.from_config.return_value.transcribe.assert_not_called()
    assert recorder == {}


# ── R2: voice must stream through the buffered SSE producer ─────────────


@pytest.mark.asyncio
async def test_voice_stream_success_emits_final_and_done():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_stream_agent()

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(mock_agent):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )
        stack.enter_context(
            patch(
                "api_service.server.routes.chat.check_abuse",
                AsyncMock(return_value=None),
            )
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        chunks = [chunk async for chunk in result.body_iterator]

    body = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    assert '"type": "final"' in body
    assert body.count('"type": "done"') == 1


@pytest.mark.asyncio
async def test_voice_stream_producer_error_is_buffered_with_terminal_done():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_stream_agent(error=RuntimeError("backend exploded"))

    with contextlib.ExitStack() as stack:
        for p in _voice_patches(mock_agent):
            stack.enter_context(p)
        stt_cls = stack.enter_context(patch("api_service.server.routes.chat.STTEngine"))
        stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )
        stack.enter_context(
            patch(
                "api_service.server.routes.chat.check_abuse",
                AsyncMock(return_value=None),
            )
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        chunks = [chunk async for chunk in result.body_iterator]

    body = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    # Producer failure is classified and always followed by a terminal done,
    # matching the buffered behavior of the text and named-agent paths.
    assert '"type": "error"' in body
    assert '"type": "done"' in body
