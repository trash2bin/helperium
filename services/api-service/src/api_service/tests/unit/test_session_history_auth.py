"""Pentest F4: /api/session/history must honour the session capability token.

The route previously sat behind the control-plane bearer only. demo/web
injects its server bearer on every proxied call, which turned the route into
"read any transcript by session_id" on the public demo edge. The fix extends
the existing session capability contract (chat M1/b878bac): the session owner
presents ``X-Session-Token``; the control-plane bearer stays valid for the
operator/dashboard path.

Tokens are bound to the session key the chat routes mint and store under:
``agent:{name}:{sid}`` for named agents, ``direct:{sid}`` for direct
(agent-less) sessions. The expected keys are written out literally below so a
change to the mint/verify formula cannot satisfy the test by moving both
sides together.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from api_service.server.session_capability import hash_session_token


class _AgentStore:
    def list_agents(self) -> list[dict]:
        return []

    def get_agent(self, name: str) -> dict | None:
        return None


class _FakeHistorySessionStore:
    """Hermetic stand-in covering the token and history seams of the route.

    History is keyed by session id exactly like the real store, so a test can
    write a transcript under the key the chat routes use and assert that the
    read path finds it — an unkeyed fake would hide key drift between mint and
    read.
    """

    def __init__(self) -> None:
        self._token_hashes: dict[str, str] = {}
        self._messages: dict[str, list[dict]] = {}

    def session_token_hash(self, session_id: str) -> str | None:
        return self._token_hashes.get(session_id)

    def bind_session_token(self, session_id: str, token_hash: str) -> str:
        return self._token_hashes.setdefault(session_id, token_hash)

    def remember(self, session_id: str, messages: list[dict]) -> None:
        """Store a transcript under the given session key (chat-side write)."""
        self._messages[session_id] = messages

    def history_messages(self, session_id: str) -> list[dict]:
        return list(self._messages.get(session_id, []))


@pytest.fixture
def history_store(monkeypatch):
    fake = _FakeHistorySessionStore()
    # Every module that imported the store keeps its own reference, so the
    # fake must be injected at each of them (auth does the token check,
    # backlog reads the transcript).
    monkeypatch.setattr("api_service.sessions.session_store", fake)
    monkeypatch.setattr("api_service.server.auth.session_store", fake)
    monkeypatch.setattr("api_service.server.routes.backlog.session_store", fake)
    return fake


def _app(monkeypatch):
    from api_service.server.routes import agents

    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: _AgentStore())
    return app_mod.app


def _bind(store, effective_id: str) -> str:
    token = "cap-token-" + effective_id
    store.bind_session_token(effective_id, hash_session_token(token))
    return token


def test_history_rejects_anonymous_without_token(monkeypatch, history_store):
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/session/history?session_id=sess-x")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_history_rejects_wrong_bearer(monkeypatch, history_store):
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x",
            headers={"Authorization": "Bearer wrong"},
        )
    assert response.status_code == 403


def test_history_accepts_control_plane_bearer(monkeypatch, history_store):
    """Operator/dashboard path keeps working through the bearer."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x",
            headers={"Authorization": "Bearer test123"},
        )
    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_history_accepts_bound_session_token(monkeypatch, history_store):
    """The session owner reads their own transcript with X-Session-Token."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    token = _bind(history_store, "agent:demo:sess-x")
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x&agent_name=demo",
            headers={"X-Session-Token": token},
        )
    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_history_accepts_direct_chat_session_token(monkeypatch, history_store):
    """A direct (agent-less) chat session is readable by its own token.

    Pin for the key-drift defect: chat mints and stores direct sessions under
    ``direct:{session_id}`` (chat.py), while history/auth used to verify the
    bare id — the minted token could never match, so every direct transcript
    read answered 401 (and the stored transcript was looked up under the wrong
    key). Both sides now go through ``effective_session_id``.
    """
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    # Key as minted/stored by the direct chat route (chat.py: ``direct:{sid}``).
    session_key = "direct:sess-x"
    token = _bind(history_store, session_key)
    # The transcript is stored where the direct chat route writes it.
    history_store.remember(session_key, [{"role": "user", "content": "hi"}])
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x",
            headers={"X-Session-Token": token},
        )
    assert response.status_code == 200, (
        "a token minted for a direct session must authenticate its own "
        "transcript read (effective key drift regression)"
    )
    messages = response.json()["messages"]
    assert [m["content"] for m in messages] == ["hi"], (
        "history must read the same key the chat route stores under, "
        f"expected {session_key!r}"
    )
    assert messages[0]["role"] == "user"


def test_history_accepts_session_token_without_bearer_config(
    monkeypatch, history_store
):
    """Capability path works even when the control plane bearer is unset."""
    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app(monkeypatch)
    token = _bind(history_store, "direct:sess-x")
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x",
            headers={"X-Session-Token": token},
        )
    assert response.status_code == 200


def test_history_rejects_foreign_session_token(monkeypatch, history_store):
    """A token bound to another session must not read this transcript."""
    monkeypatch.setenv("API_BEARER_TOKEN", "test123")
    app = _app(monkeypatch)
    foreign = _bind(history_store, "agent:demo:sess-other")
    with TestClient(app) as client:
        response = client.get(
            "/api/session/history?session_id=sess-x&agent_name=demo",
            headers={"X-Session-Token": foreign},
        )
    assert response.status_code == 401


def test_history_fail_closed_without_any_configuration(monkeypatch, history_store):
    monkeypatch.delenv("API_BEARER_TOKEN", raising=False)
    app = _app(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/session/history?session_id=sess-x")
    assert response.status_code == 503
