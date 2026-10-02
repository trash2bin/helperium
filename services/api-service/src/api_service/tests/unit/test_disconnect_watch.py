"""_DisconnectWatch must be event-driven, not a busy poll.

The watch runs once per open SSE connection. The previous implementation woke
every 250 ms and called ``request.is_disconnected()``, which funnels through
Starlette's BaseHTTPMiddleware ``receive_or_disconnect`` wrapper — that path
creates an anyio task group (two spawned tasks + a cancel scope) on every
single probe. At 40 rps there are ~60 concurrent SSE connections, so the
watch alone burned ~60% of the GIL (measured via py-spy: ``_watch``,
``is_disconnected`` and ``receive_or_disconnect`` at the top of the stack).
The fix is a single blocking receive on the transport channel: uvicorn posts
``http.disconnect`` there exactly when the client leaves, so no polling is
needed.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Request

from api_service.server.routes.chat import _DisconnectWatch


def _request(receive) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_watch_detects_disconnect_without_polling() -> None:
    """A disconnect on the transport channel latches the event, no polling."""
    calls = 0

    async def receive() -> dict:
        nonlocal calls
        calls += 1
        return {"type": "http.disconnect"}

    request = _request(receive)
    watch = _DisconnectWatch(request)
    watch.start()
    # Give an event-driven watcher time to observe the disconnect.
    await asyncio.sleep(0.1)
    await watch.stop()

    assert watch.disconnected is True
    assert calls == 1, (
        f"receive() called {calls} times; expected a single blocking probe"
    )


@pytest.mark.asyncio
async def test_watch_stays_unlatched_without_disconnect() -> None:
    """An alive connection produces no spurious latch while the stream runs."""

    async def receive() -> dict:
        # Block forever: a live client never produces a transport message here.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    request = _request(receive)
    watch = _DisconnectWatch(request)
    watch.start()
    # Even across several poll intervals of the old implementation, the watch
    # must not have probed the transport more than once.
    await asyncio.sleep(0.6)
    await watch.stop()

    assert watch.disconnected is False


@pytest.mark.asyncio
async def test_watch_unlatched_receive_not_busy_looped() -> None:
    """The receive channel is consumed once, never in a hot loop."""
    calls = 0

    async def receive() -> dict:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()  # never resolves
        return {"type": "http.disconnect"}

    request = _request(receive)
    watch = _DisconnectWatch(request)
    watch.start()
    await asyncio.sleep(0.55)
    await watch.stop()

    assert calls == 1, (
        f"receive() called {calls} times; the watch is polling instead of "
        "waiting on one blocking transport read"
    )
    assert watch.disconnected is False


@pytest.mark.asyncio
async def test_check_probe_still_available() -> None:
    """The zero-arg ``check`` still reflects the latch state."""

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    request = _request(receive)
    watch = _DisconnectWatch(request)
    # Before starting, check() reports latched only on a set event.
    assert watch.check() is False
    watch.start()
    await asyncio.sleep(0.05)
    await watch.stop()
    assert watch.check() is True
