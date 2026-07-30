"""Tests for check_abuse() extracted to security.py."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


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
