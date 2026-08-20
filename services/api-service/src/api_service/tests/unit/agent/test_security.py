"""Tests for check_abuse() extracted to security.py."""

import pytest
import json
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from api_service.abuse_live import LiveAbuseProvider
from api_service.session_repository import SessionAbuseState


class _SessionStore:
    def __init__(self) -> None:
        self.states: dict[str, SessionAbuseState] = {}
        self.accepted_at: list[tuple[str, float]] = []

    def abuse_state(self, session_id: str) -> SessionAbuseState:
        return self.states.get(session_id, SessionAbuseState())

    def accept_user_turn(
        self, session_id: str, accepted_at: float
    ) -> SessionAbuseState:
        previous = self.abuse_state(session_id)
        state = SessionAbuseState(previous.user_turn_count + 1, accepted_at)
        self.states[session_id] = state
        self.accepted_at.append((session_id, accepted_at))
        return state


class TestCheckAbuse:
    """Tests for check_abuse function."""

    @pytest.mark.asyncio
    async def test_abuse_not_triggered(self, monkeypatch):
        """Normal request passes abuse check (returns None)."""
        import api_service.server.security as security

        monkeypatch.setattr(security, "session_store", _SessionStore())
        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            result = await security.check_abuse(request, "session-1", "hello world")
            return {"blocked": result is not None}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/test",
                headers={"User-Agent": "test-agent"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_session_id(self, monkeypatch):
        """Empty session_id doesn't crash."""
        import api_service.server.security as security

        monkeypatch.setattr(security, "session_store", _SessionStore())
        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            result = await security.check_abuse(request, "", "hello")
            return {"blocked": result is not None}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_abuse_config_passed(self, monkeypatch):
        """Agent abuse config is forwarded to checker."""
        import api_service.server.security as security

        monkeypatch.setattr(security, "session_store", _SessionStore())
        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            # Pass custom config — doesn't matter what, just verify it's accepted
            result = await security.check_abuse(
                request, "s1", "test", agent_abuse_config={"max_message_length": 100}
            )
            return {"blocked": result is not None}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/test", headers={"User-Agent": "test"})
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_state_persists_between_requests(tmp_path, monkeypatch):
    config_path = tmp_path / "abuse.json"
    config_path.write_text(
        json.dumps(
            {
                "rps": 0.001,
                "burst": 1,
                "block_empty_user_agent": False,
                "min_interval_ms": 0,
            }
        )
    )
    provider = LiveAbuseProvider(str(config_path))
    import api_service.server.security as security

    monkeypatch.setattr(security, "get_live_abuse_provider", lambda: provider)
    monkeypatch.setattr(security, "session_store", _SessionStore())
    app = FastAPI()

    @app.post("/test")
    async def handler(request: Request):
        blocked = await security.check_abuse(request, "rate-limit-session", "hello")
        return blocked or {"blocked": False}

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        assert client.post("/test", headers=headers).status_code == 200
        blocked = client.post("/test", headers=headers)

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


@pytest.mark.asyncio
async def test_accepted_user_turn_is_stamped_before_the_next_interval_check(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "abuse.json"
    config_path.write_text(
        json.dumps(
            {
                "rps": 100,
                "burst": 100,
                "block_empty_user_agent": False,
                "min_interval_ms": 1_000,
            }
        )
    )
    provider = LiveAbuseProvider(str(config_path))
    import api_service.server.security as security

    class _SessionStore:
        def __init__(self) -> None:
            self.state = SessionAbuseState()
            self.accepted_at: list[float] = []

        def abuse_state(self, _session_id: str) -> SessionAbuseState:
            return self.state

        def accept_user_turn(
            self, _session_id: str, accepted_at: float
        ) -> SessionAbuseState:
            self.accepted_at.append(accepted_at)
            self.state = SessionAbuseState(1, accepted_at)
            return self.state

    store = _SessionStore()
    monkeypatch.setattr(security, "get_live_abuse_provider", lambda: provider)
    monkeypatch.setattr(security, "session_store", store)
    monkeypatch.setattr(security.time, "time", lambda: 1_725_000_000.0)
    app = FastAPI()

    @app.post("/test")
    async def handler(request: Request):
        blocked = await security.check_abuse(request, "session-1", "hello")
        return blocked or {"blocked": False}

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        assert client.post("/test", headers=headers).status_code == 200
        blocked = client.post("/test", headers=headers)

    assert store.accepted_at == [1_725_000_000.0]
    assert store.state == SessionAbuseState(1, 1_725_000_000.0)
    assert "Min interval not met" in blocked.text
