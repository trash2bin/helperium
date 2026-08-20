"""Public/private API boundary regression tests."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


class _AgentStore:
    """Minimal in-memory agent store for route-boundary tests."""

    def list_agents(self) -> list[dict]:
        return []

    def get_agent(self, name: str) -> dict | None:
        if name == "default":
            return {"widget_config": {"title": "Public widget"}}
        return None


def _app_with_agent_store(monkeypatch):
    from api_service.server.routes import agents

    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: _AgentStore())
    return app_mod.app


def test_protected_routes_fail_closed_without_server_token(monkeypatch):
    """Missing API_BEARER_TOKEN never silently grants control-plane access."""

    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        for path in (
            "/api/agents",
            "/api/backlog",
            "/admin/abuse-config",
            "/api/voice-config",
            "/metrics",
        ):
            response = client.get(path)
            assert response.status_code == 503, (path, response.text)


def test_configured_bearer_requires_exact_token(monkeypatch):
    """Configured control plane differentiates missing, wrong and valid bearer tokens."""

    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        missing = client.get("/api/agents")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"

        wrong = client.get("/api/agents", headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 403

        accepted = client.get(
            "/api/agents", headers={"Authorization": "Bearer test123"}
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"agents": []}


def test_widget_config_remains_public_without_bearer(monkeypatch):
    """Embed bootstrap configuration remains available to a public widget."""

    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/agents/default/widget-config")

    assert response.status_code == 200
    assert response.json()["title"] == "Public widget"


def test_public_chat_route_does_not_require_control_plane_bearer(monkeypatch):
    """Public chat keeps its existing anonymous boundary and validation behavior."""

    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "", "session_id": "public-boundary-test"},
        )

    assert response.status_code == 200
    assert "Invalid request body." in response.text


def test_automatic_docs_and_openapi_routes_are_not_public(monkeypatch):
    """Swagger, ReDoc and the JSON schema are not implicit public API routes."""

    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 404, (path, response.text)
