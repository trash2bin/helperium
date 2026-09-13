"""Pentest H1: /api/agents must not leak plaintext llm_config.api_key.

demo/web proxies /api/agents with the server bearer token, so anyone who can
reach the demo proxy gets the full admin surface. The control-plane fix is to
mask secrets in API responses by default and only return the full value to
explicit, authenticated callers (X-Full-Keys: 1 + valid bearer).
"""

from __future__ import annotations

import importlib
import json

from fastapi.testclient import TestClient

MASKED_SENTINEL = "…"

LIVE_KEY = "nvapi-live-super-secret-key-1234567890"


class _AgentStore:
    """In-memory agent store returning agents with sensitive llm_config."""

    def list_agents(self) -> list[dict]:
        return [self._agent()]

    def get_agent(self, name: str) -> dict | None:
        if name == "autoparts-assistant":
            return self._agent()
        return None

    @staticmethod
    def _agent() -> dict:
        return {
            "name": "autoparts-assistant",
            "description": "Test agent",
            "tenant_ids": ["autoparts"],
            "widget_config": None,
            "llm_config": {
                "provider": "nvidia",
                "model": "meta/llama-3.3-70b-instruct",
                "api_key": LIVE_KEY,
            },
            "provider_priority": ["nvidia"],
            "abuse_config": None,
            "system_prompt": None,
            "voice_config": {
                "enabled": True,
                "stt_providers": [
                    {
                        "name": "openai",
                        "provider": "litellm",
                        "model": "whisper-1",
                        "api_key": "sk-voice-live-key",
                    }
                ],
            },
            "created_at": "2026-09-12T00:00:00Z",
            "updated_at": "2026-09-12T00:00:00Z",
        }


def _app_with_agent_store(monkeypatch):
    from api_service.server.routes import agents

    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: _AgentStore())
    return app_mod.app


def _agent_bodies(response) -> list[dict]:
    data = response.json()
    return data["agents"] if isinstance(data, dict) and "agents" in data else [data]


def _assert_masked(body: str, path: str) -> None:
    assert LIVE_KEY not in body, f"{path} leaked full llm api_key"
    assert "sk-voice-live-key" not in body, f"{path} leaked voice STT api_key"


def test_list_agents_masks_api_keys_by_default(monkeypatch):
    """GET /api/agents with a valid bearer never returns full key values."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents", headers={"Authorization": "Bearer test123"}
        )
    assert response.status_code == 200
    _assert_masked(response.text, "/api/agents")

    agents = _agent_bodies(response)
    assert len(agents) == 1
    llm_key = agents[0]["llm_config"]["api_key"]
    assert llm_key is not None
    assert llm_key != LIVE_KEY
    assert MASKED_SENTINEL in llm_key or llm_key.startswith("*")


def test_get_agent_masks_api_keys_by_default(monkeypatch):
    """GET /api/agents/{name} with a valid bearer never returns the full key."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/autoparts-assistant",
            headers={"Authorization": "Bearer test123"},
        )
    assert response.status_code == 200
    _assert_masked(response.text, "/api/agents/{name}")


def test_explicit_full_keys_header_returns_plaintext(monkeypatch):
    """Admin tools (dashboard round-trip) opt in with X-Full-Keys: 1."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents",
            headers={"Authorization": "Bearer test123", "X-Full-Keys": "1"},
        )
    assert response.status_code == 200
    assert LIVE_KEY in response.text, "X-Full-Keys: 1 + bearer must return full key"


def test_full_keys_header_without_bearer_is_rejected(monkeypatch):
    """X-Full-Keys alone must not bypass the control-plane auth."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/agents", headers={"X-Full-Keys": "1"})
    assert response.status_code == 401, response.text


def test_create_response_is_masked_by_default(monkeypatch):
    """POST /api/agents echoes the created agent; keys stay masked by default."""
    from api_service.server.routes import agents

    store = _AgentStore()

    class _CreatingStore(_AgentStore):
        def create_agent(self, name, description, tenant_ids, **kwargs) -> dict:
            return store.get_agent(name)  # type: ignore[return-value]

    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: _CreatingStore())

    with TestClient(app_mod.app) as client:
        response = client.post(
            "/api/agents",
            headers={"Authorization": "Bearer test123"},
            json={
                "name": "autoparts-assistant",
                "description": "Test agent",
                "tenant_ids": ["autoparts"],
                "llm_config": {
                    "provider": "nvidia",
                    "model": "meta/llama-3.3-70b-instruct",
                    "api_key": LIVE_KEY,
                },
            },
        )
    assert response.status_code == 201
    _assert_masked(response.text, "POST /api/agents")


def test_widget_config_stays_public_and_never_carries_key(monkeypatch):
    """The public widget-config route must not regress or leak llm_config."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app_with_agent_store(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/agents/autoparts-assistant/widget-config")
    assert response.status_code == 200
    body = json.loads(response.text)
    assert "name" not in body  # widget config is a tiny public dict
    assert "api_key" not in body


# ── Pentest H1 follow-up: masked write-back guard ───────────────────


class _RecordingStore(_AgentStore):
    """Store that records update_agent calls so tests can assert rejection."""

    def __init__(self):
        self.updates: list[tuple[str, dict]] = []

    def update_agent(self, name, **kwargs):
        self.updates.append((name, kwargs))
        return self.get_agent(name)


def _app_with_recording_store(monkeypatch):
    from api_service.server.routes import agents

    store = _RecordingStore()
    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: store)
    return app_mod.app, store


def _put_llm_config(app, api_key: str, headers: dict | None = None):
    payload_headers = {"Authorization": "Bearer test123"}
    if headers:
        payload_headers.update(headers)
    with TestClient(app) as client:
        return client.put(
            "/api/agents/autoparts-assistant",
            headers=payload_headers,
            json={
                "description": "Updated",
                "llm_config": {
                    "provider": "nvidia",
                    "model": "meta/llama-3.3-70b-instruct",
                    "api_key": api_key,
                },
            },
        )


def test_update_with_masked_api_key_is_rejected(monkeypatch):
    """PUT с ключом в форме маски (abcd…wxyz) не должен дойти до хранилища —
    иначе round-trip GET-без-X-Full-Keys → PUT перезаписал бы секрет маской."""
    app, store = _app_with_recording_store(monkeypatch)
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")

    masked = "nvap…7890"
    response = _put_llm_config(app, masked)
    assert response.status_code == 400, response.text
    assert LIVE_KEY not in response.text
    assert store.updates == [], "masked api_key must not reach the store"


def test_update_with_star_masked_api_key_is_rejected(monkeypatch):
    """Короткие ключи маскируются в ******** — такой write-back тоже отклоняется."""
    app, store = _app_with_recording_store(monkeypatch)
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")

    response = _put_llm_config(app, "********")
    assert response.status_code == 400, response.text
    assert store.updates == []


def test_update_with_real_api_key_still_round_trips(monkeypatch):
    """X-Full-Keys: 1 → GET → PUT с настоящим ключом продолжает работать."""
    app, store = _app_with_recording_store(monkeypatch)
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")

    response = _put_llm_config(app, LIVE_KEY, headers={"X-Full-Keys": "1"})
    assert response.status_code == 200, response.text
    assert len(store.updates) == 1


def test_create_with_masked_api_key_is_rejected(monkeypatch):
    """POST с маской вместо ключа — всегда ошибка клиента, не молчаливый секрет."""
    app, store = _app_with_recording_store(monkeypatch)
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")

    with TestClient(app) as client:
        response = client.post(
            "/api/agents",
            headers={"Authorization": "Bearer test123"},
            json={
                "name": "masked-agent",
                "llm_config": {"api_key": "sk-s…wxyz"},
            },
        )
    assert response.status_code == 400, response.text
    assert store.updates == []
