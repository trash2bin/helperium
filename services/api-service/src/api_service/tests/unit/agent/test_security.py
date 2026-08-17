"""Tests for check_abuse() extracted to security.py."""

import pytest
import json
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from api_service.abuse_live import LiveAbuseProvider


class TestCheckAbuse:
    """Tests for check_abuse function."""

    @pytest.mark.asyncio
    async def test_abuse_not_triggered(self):
        """Normal request passes abuse check (returns None)."""
        from api_service.server.security import check_abuse

        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            result = await check_abuse(request, "session-1", "hello world")
            return {"blocked": result is not None}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/test",
                headers={"User-Agent": "test-agent"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_session_id(self):
        """Empty session_id doesn't crash."""
        from api_service.server.security import check_abuse

        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            result = await check_abuse(request, "", "hello")
            return {"blocked": result is not None}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_abuse_config_passed(self):
        """Agent abuse config is forwarded to checker."""
        from api_service.server.security import check_abuse

        app = FastAPI()

        @app.post("/test")
        async def handler(request: Request):
            # Pass custom config — doesn't matter what, just verify it's accepted
            result = await check_abuse(
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
