"""App-level (lifespan) encryption-policy startup behavior.

C5 audit follow-up. When the agent store raises a policy error at repository
construction, the API lifespan must propagate it and refuse startup; swallowing
it yields a process that answers /health yet fails every agent-dependent
request (silent degraded startup).

These tests cover:

1. Plaintext llm_config rows + no ENCRYPTION_KEY     -> startup must raise.
2. Ciphertext rows + a mismatched ENCRYPTION_KEY      -> startup must raise
   (otherwise llm_config silently becomes None on every read).
3. Ciphertext rows + the matching ENCRYPTION_KEY      -> startup proceeds.
4. llm_config-free database + no ENCRYPTION_KEY       -> startup proceeds
   (backward compat for fresh dev/demo installs).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def _make_agents_sqlite_with_llm_config(tmp_path, name: str = "legacy") -> str:
    """Seed a tmp agents.sqlite with one agent carrying plaintext llm_config.

    Mirrors the schema from SqliteAgentRepository._init_db (plus the ALTER
    columns), created directly so the repository constructor can be exercised
    against pre-existing rows.
    """
    db = tmp_path / "agents.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """CREATE TABLE agents (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT '',
                tenant_ids TEXT NOT NULL DEFAULT '[]',
                widget_config TEXT,
                llm_config TEXT,
                provider_priority TEXT NOT NULL DEFAULT '[]',
                voice_config TEXT,
                abuse_config TEXT,
                system_prompt TEXT,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO agents (name, tenant_ids, llm_config, provider_priority,"
            " created_at, updated_at) VALUES (?, '[]', ?, '[]',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (name, json.dumps({"provider": "openai", "model": "gpt-4o-mini"})),
        )
        conn.commit()
    finally:
        conn.close()
    return str(db)


def _isolate_store(monkeypatch, db_path: str) -> None:
    """Route the agent store at a temp DB and reset its module singleton.

    Mirrors `_isolated_agent_db` in test_routes_exist.py: get_agent_store()
    reads AGENT_DB_PATH lazily and caches the repository in
    deps._agent_store, so both must be reset per test.
    """
    monkeypatch.setenv("AGENT_DB_PATH", db_path)
    import api_service.server.deps as deps_module

    monkeypatch.setattr(deps_module, "_agent_store", None)


def _reload_app():
    """Reload the API app module (proven pattern from test_routes_exist.py).

    The agent_repository module (and its monkeypatched _FERNET) is NOT
    re-imported, so the key policy under test survives the reload.
    """
    import api_service.server as sv

    if hasattr(sv, "app"):
        del sv.app
    importlib.reload(sv)
    return sv.app


def _run_startup(app) -> None:
    """Drive the lifespan startup phase (up to yield) in a fresh event loop.

    Directly exercising lifespan.__aenter__ avoids TestClient's error-
    propagation semantics, so a policy failure surfaces deterministically.
    """
    from api_service.server.app import lifespan

    asyncio.run(lifespan(app).__aenter__())


class TestLifespanEncryptionPolicy:
    def test_lifespan_fails_fast_when_plaintext_and_no_key(
        self, monkeypatch, tmp_path
    ):
        """Plaintext llm_config + no key: startup must raise, not degrade.

        Confirmed by code: the lifespan used to wrap get_agent_store() in a
        broad except Exception and log a warning, leaving the API healthy yet
        broken on every agent-dependent request. It must now refuse startup.
        """
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        import api_service.agent_repository as repo_mod

        monkeypatch.setattr(repo_mod, "_FERNET", None)
        db = _make_agents_sqlite_with_llm_config(tmp_path)
        _isolate_store(monkeypatch, db)

        with pytest.raises(repo_mod.LLMEncryptionKeyRequiredError):
            _run_startup(FastAPI())

    def test_lifespan_fails_fast_on_mismatched_key_ciphertext(
        self, monkeypatch, tmp_path
    ):
        """Ciphertext stored with KEY_A + KEY_B configured: startup must raise.

        Without this, the service boots but every read returns llm_config=None
        (silent unavailable-config state, see C5 audit follow-up).
        """
        import api_service.agent_repository as repo_mod

        fernet_a = Fernet(KEY_A.encode())
        db = _make_agents_sqlite_with_llm_config(tmp_path)
        ciphertext = fernet_a.encrypt(
            json.dumps({"provider": "openai", "model": "gpt-4o-mini"}).encode()
        ).decode("ascii")
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE agents SET llm_config = ?", (ciphertext,))
            conn.commit()
        finally:
            conn.close()

        monkeypatch.setattr(repo_mod, "_FERNET", Fernet(KEY_B.encode()))
        monkeypatch.setenv("ENCRYPTION_KEY", KEY_B)
        _isolate_store(monkeypatch, db)

        with pytest.raises(repo_mod.LLMConfigUnavailableError):
            _run_startup(FastAPI())

    def test_lifespan_starts_with_matching_key_and_ciphertext(
        self, monkeypatch, tmp_path
    ):
        """Ciphertext + the matching key: startup proceeds (parity guard)."""
        import api_service.agent_repository as repo_mod

        fernet = Fernet(KEY_A.encode())
        monkeypatch.setattr(repo_mod, "_FERNET", fernet)
        monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)

        db = _make_agents_sqlite_with_llm_config(tmp_path)
        ciphertext = fernet.encrypt(
            json.dumps({"provider": "openai", "model": "gpt-4o-mini"}).encode()
        ).decode("ascii")
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE agents SET llm_config = ?", (ciphertext,))
            conn.commit()
        finally:
            conn.close()

        _isolate_store(monkeypatch, db)
        with TestClient(_reload_app()) as client:
            assert client.get("/health").status_code == 200

    def test_lifespan_starts_without_key_when_no_llm_config(
        self, monkeypatch, tmp_path
    ):
        """Fresh/empty agents DB without llm_config rows: key is not required
        (backward compat), the app must still boot."""
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        import api_service.agent_repository as repo_mod

        monkeypatch.setattr(repo_mod, "_FERNET", None)

        db = tmp_path / "agents.sqlite"
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """CREATE TABLE agents (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    tenant_ids TEXT NOT NULL DEFAULT '[]',
                    widget_config TEXT,
                    llm_config TEXT,
                    provider_priority TEXT NOT NULL DEFAULT '[]',
                    voice_config TEXT,
                    abuse_config TEXT,
                    system_prompt TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            conn.commit()
        finally:
            conn.close()

        _isolate_store(monkeypatch, str(db))
        with TestClient(_reload_app()) as client:
            assert client.get("/health").status_code == 200
