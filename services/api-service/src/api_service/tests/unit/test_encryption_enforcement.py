"""C5: ENCRYPTION_KEY enforcement and legacy-plaintext migration.

Policy contract for SqliteAgentRepository:

1. If the database already contains agent rows with llm_config, constructing
   the repository without a valid ENCRYPTION_KEY must fail fast instead of
   silently storing new secrets as plaintext.
2. If there are no llm_config rows, the repository may start without a key
   (backward compat for dev/demo).
3. When a key is available and legacy plaintext llm_config rows exist, the
   constructor must migrate them to ciphertext (previously: mixed storage,
   and reads of plaintext rows with a configured key returned None which
   silently wiped the config on the next update).
4. When the configured key cannot decrypt a stored llm_config value, an
   update that would carry the undecryptable value forward must raise
   instead of persisting llm_config = NULL (silent wipe protection).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from cryptography.fernet import Fernet

import api_service.agent_repository as agent_repo_mod
from api_service.agent_repository import SqliteAgentRepository
from api_service.tests.unit.conftest import SAMPLE_LLM

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def _use_fernet(monkeypatch, key: str | None) -> None:
    """Point the module-level cipher at a key (or None), like the existing
    encryption tests do: _FERNET is an import-time singleton, so env changes
    alone do not affect it."""
    monkeypatch.setattr(
        agent_repo_mod,
        "_FERNET",
        Fernet(key.encode()) if key else None,
    )


def _raw_llm_configs(db_path: str) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, llm_config FROM agents WHERE llm_config IS NOT NULL"
        ).fetchall()
        return {name: value for name, value in rows}
    finally:
        conn.close()


def _insert_legacy_plaintext_agent(db_path: str, name: str) -> None:
    """Simulate a pre-key deployment row directly in SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agents (name, tenant_ids, llm_config, provider_priority, created_at, updated_at) "
            "VALUES (?, '[]', ?, '[]', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (name, json.dumps(SAMPLE_LLM, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


# ── Policy decision function ──


class TestLLMKeyRequirement:
    def test_key_ready_with_agents_allowed(self):
        assert agent_repo_mod._resolve_llm_key_requirement(True, True) is None

    def test_key_ready_without_agents_allowed(self):
        assert agent_repo_mod._resolve_llm_key_requirement(True, False) is None

    def test_no_key_without_agents_allowed(self):
        assert agent_repo_mod._resolve_llm_key_requirement(False, False) is None

    def test_missing_key_with_agents_refused(self):
        reason = agent_repo_mod._resolve_llm_key_requirement(False, True)
        assert reason is not None
        assert "ENCRYPTION_KEY" in reason


# ── Constructor enforcement ──


class TestConstructorPolicy:
    def test_missing_key_with_agent_llm_rows_raises(self, tmp_path, monkeypatch):
        """Existing llm_config rows + no key (or an invalid one, which also
        yields _FERNET = None): refuse to start (previously just warned)."""
        _use_fernet(monkeypatch, None)
        db = str(tmp_path / "agents.sqlite")
        store = SqliteAgentRepository(db)  # no llm rows yet: construction ok
        store.create_agent("legacy", llm_config=SAMPLE_LLM)

        with pytest.raises(agent_repo_mod.LLMEncryptionKeyRequiredError):
            SqliteAgentRepository(db)

    def test_missing_key_without_agent_llm_rows_starts(self, tmp_path, monkeypatch):
        """No llm_config rows: dev/demo without a key still boots."""
        _use_fernet(monkeypatch, None)
        db = str(tmp_path / "agents.sqlite")
        store = SqliteAgentRepository(db)
        store.create_agent("plain-agent")  # no llm_config
        assert store.get_agent("plain-agent") is not None


# ── Legacy plaintext migration ──


class TestLegacyPlaintextMigration:
    def test_constructor_migrates_plaintext_rows(self, tmp_path, monkeypatch):
        """With a key, legacy plaintext llm_config rows are encrypted at startup."""
        _use_fernet(monkeypatch, KEY_A)
        db = str(tmp_path / "agents.sqlite")
        SqliteAgentRepository(db)  # creates schema with the key set
        # Simulate a pre-key era row (plaintext JSON) directly:
        _insert_legacy_plaintext_agent(db, "old-agent")

        # Re-open: policy passes, migration must run.
        SqliteAgentRepository(db)

        raw = _raw_llm_configs(db)
        assert raw["old-agent"].startswith("gAAAAA"), (
            "legacy plaintext llm_config was not migrated to ciphertext"
        )
        # And the value is still readable through the repository:
        reopened = SqliteAgentRepository(db)
        got = reopened.get_agent("old-agent")
        assert got is not None
        assert got["llm_config"] == SAMPLE_LLM

    def test_migration_skips_ciphertext_rows(self, tmp_path, monkeypatch):
        """Rows already encrypted with the same key must not be re-encrypted."""
        _use_fernet(monkeypatch, KEY_A)
        db = str(tmp_path / "agents.sqlite")
        store = SqliteAgentRepository(db)
        store.create_agent("enc-agent", llm_config=SAMPLE_LLM)
        before = _raw_llm_configs(db)["enc-agent"]
        assert before.startswith("gAAAAA")

        SqliteAgentRepository(db)  # reopen, migration must be a no-op

        after = _raw_llm_configs(db)["enc-agent"]
        assert after == before


# ── Key mismatch: fail fast at construction (C5 follow-up) ──


class TestKeyMismatchProtection:
    def test_wrong_key_raises_at_construction(self, tmp_path, monkeypatch):
        """Ciphertext written with KEY_A + repo constructed with KEY_B must
        raise at construction. Failing only on update would let the service
        start while every read silently maps llm_config to None."""
        db = str(tmp_path / "agents.sqlite")
        _use_fernet(monkeypatch, KEY_A)
        store = SqliteAgentRepository(db)
        store.create_agent("victim", llm_config=SAMPLE_LLM)

        _use_fernet(monkeypatch, KEY_B)
        with pytest.raises(agent_repo_mod.LLMConfigUnavailableError) as excinfo:
            SqliteAgentRepository(db)
        assert "ENCRYPTION_KEY" in str(excinfo.value)

    def test_correct_key_and_ciphertext_constructs_cleanly(
        self, tmp_path, monkeypatch
    ):
        """Baseline: ciphertext + the matching key constructs fine."""
        db = str(tmp_path / "agents.sqlite")
        _use_fernet(monkeypatch, KEY_A)
        store = SqliteAgentRepository(db)
        store.create_agent("victim", llm_config=SAMPLE_LLM)

        # Reopen with the same key: no error, value readable.
        reopened = SqliteAgentRepository(db)
        got = reopened.get_agent("victim")
        assert got is not None
        assert got["llm_config"] == SAMPLE_LLM
