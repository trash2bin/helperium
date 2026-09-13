"""Pentest H2: session_id rotation must not defeat the per-IP budget.

The per-session token bucket is keyed on (session_id, ip, user_agent), so a
client that mints a fresh session_id per request gets a fresh bucket every
time. The fix adds a GLOBAL per-IP token bucket (allow_ip) that is independent
of session_id/user-agent and survives rotation.

Route-level behaviour: check_abuse rejects with 429 once the IP budget is
exhausted, no matter how many session_ids are tried.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.requests import Request

from api_service.abuse_live import LiveAbuseProvider
from api_service.anti_abuse import AbuseConfig, TokenBucket
from api_service.server.security import check_abuse


async def _receive() -> dict:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": headers or [(b"user-agent", b"Browser/1.0")],
            "client": ("203.0.113.7", 4321),
            "server": ("testserver", 80),
        },
        receive=_receive,
    )


# ── TokenBucket.allow_ip ──


class TestIpBudget:
    def test_ip_burst_is_finite(self):
        """The IP bucket has its own burst; excess is denied."""
        tb = TokenBucket(AbuseConfig(rps=0, burst=3))
        for i in range(3):
            ok, _ = tb.allow_ip("203.0.113.7")
            assert ok, f"request {i + 1} within IP burst"
        ok, ctx = tb.allow_ip("203.0.113.7")
        assert not ok
        assert "retry_after" in ctx

    def test_ip_bucket_survives_session_rotation(self):
        """Rotating session_id/UA gives a fresh session bucket but NOT a fresh IP bucket."""
        tb = TokenBucket(AbuseConfig(rps=0, burst=3))
        for i in range(3):
            # every request is a brand-new session with a new UA
            ok_sess, _ = tb.allow(f"sess-rotate-{i}", "203.0.113.7", f"UA-{i}")
            assert ok_sess
            ok_ip, _ = tb.allow_ip("203.0.113.7")
            assert ok_ip, f"request {i + 1} exceeds IP budget"
        # 4th session would be allowed per-session, but the IP budget is spent
        ok_sess, _ = tb.allow("sess-rotate-99", "203.0.113.7", "UA-99")
        assert ok_sess
        ok_ip, _ = tb.allow_ip("203.0.113.7")
        assert not ok_ip, "IP budget must survive session_id rotation"

    def test_ip_bucket_is_isolated_per_ip(self):
        tb = TokenBucket(AbuseConfig(rps=0, burst=2))
        tb.allow_ip("203.0.113.7")
        tb.allow_ip("203.0.113.7")
        assert not tb.allow_ip("203.0.113.7")[0]
        assert tb.allow_ip("203.0.113.8")[0], "different IP gets its own bucket"

    def test_ip_bucket_replenishes(self):
        tb = TokenBucket(AbuseConfig(rps=1, burst=1))
        assert tb.allow_ip("203.0.113.7")[0]
        assert not tb.allow_ip("203.0.113.7")[0]
        tb._advance_time("ip:203.0.113.7", 1500)  # +1.5s → ~1.5 tokens
        assert tb.allow_ip("203.0.113.7")[0]
        assert not tb.allow_ip("203.0.113.7")[0], "replenished only 1 token"


# ── Route-level: check_abuse rejects rotated sessions after IP burst ──


class _FakeSessionStore:
    def abuse_state(self, session_id: str):  # noqa: ARG002
        return SimpleNamespace(last_user_turn_at=time.time() - 60, user_turn_count=0)

    def accept_user_turn(self, session_id: str, at: float):  # noqa: ARG002
        return None


@pytest.fixture
def tiny_ip_budget(monkeypatch, tmp_path):
    """Fresh provider with an IP budget of 2 (no replenish) and no JSON overlay."""
    monkeypatch.setenv("ABUSE_IP_RPS", "0")
    monkeypatch.setenv("ABUSE_IP_BURST", "2")
    monkeypatch.setenv("ABUSE_CONFIG_PATH", str(tmp_path / "nope.json"))
    old = LiveAbuseProvider._instance
    LiveAbuseProvider._instance = None
    yield
    LiveAbuseProvider._instance = old


@pytest.mark.asyncio
async def test_check_abuse_denies_session_rotation_after_ip_burst(
    tiny_ip_budget, monkeypatch
):
    """8 rotated session_ids on one IP: after the IP burst the rest are 429."""
    provider = LiveAbuseProvider.get_instance()
    assert provider.get_ip_bucket().config.burst == 2

    with patch("api_service.server.security.session_store", _FakeSessionStore()):
        for i in range(2):
            resp = await check_abuse(_request(), f"sess-rot-{i}", "hello")
            assert resp is None, f"request {i} should pass within IP burst"

        resp = await check_abuse(_request(), "sess-rot-99", "hello")
        assert resp is not None
        assert resp.status_code == 429, "IP budget must block a fresh session_id"


@pytest.mark.asyncio
async def test_check_abuse_rotation_blocked_despite_fresh_sessions(
    tiny_ip_budget, monkeypatch
):
    """Prove the block is keyed on IP: another IP is unaffected."""
    with patch("api_service.server.security.session_store", _FakeSessionStore()):
        # Exhaust the budget for the first IP
        for i in range(2):
            await check_abuse(_request(), f"sess-rot-{i}", "hello")

        blocked = await check_abuse(_request(), "sess-rot-99", "hello")
        assert blocked is not None and blocked.status_code == 429

        # A different client IP with a fresh session is still allowed
        other = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/chat",
                "raw_path": b"/api/chat",
                "query_string": b"",
                "headers": [(b"user-agent", b"Browser/1.0")],
                "client": ("198.51.100.9", 4321),
                "server": ("testserver", 80),
            },
            receive=_receive,
        )
        resp = await check_abuse(other, "sess-other-ip", "hello")
        assert resp is None, "other IP with fresh session must be allowed"
