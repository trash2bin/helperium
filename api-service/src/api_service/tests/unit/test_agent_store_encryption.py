"""Tests for AgentStore encryption/decryption of llm_config.

These tests cover the Fernet-based encryption layer in agent_store.py,
including roundtrip, raw storage format, plaintext fallback, and
corrupted-value handling.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import api_service.agent_store as agent_store_mod
from api_service.agent_store import AgentStore

# For encryption patching — functions live in agent_repository module
import api_service.agent_repository as agent_repo_mod

# One-time Fernet key shared across all encryption tests
TEST_KEY = Fernet.generate_key().decode()
TEST_FERNET = Fernet(TEST_KEY.encode())


# ── Shared data ──

SAMPLE_LLM = {
    "provider": "ollama",
    "model": "qwen2.5:0.5b",
    "temperature": 0.3,
    "system_prompt": "You are a test assistant.",
}

UPDATED_LLM = {
    "provider": "mistral",
    "model": "mistral/mistral-small",
    "temperature": 0.7,
    "system_prompt": "You are an updated assistant.",
}


# ── Fixtures ──


@pytest.fixture
def agent_store():
    """AgentStore backed by a temporary SQLite file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = AgentStore(path)
    yield store
    Path(path).unlink(missing_ok=True)


# ── Low-level encryption helpers ──


class TestEncryptDecryptDirect:
    """Direct tests of _encrypt_value / _decrypt_value."""

    def test_encrypt_without_key_noop(self, monkeypatch):
        """Without ENCRYPTION_KEY, encrypt/decrypt pass through unchanged."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", None)
        assert agent_store_mod._encrypt_value("test") == "test"
        assert agent_store_mod._decrypt_value("test") == "test"

    def test_encrypt_with_key_changes_value(self, monkeypatch):
        """With a key, encrypted output differs from input."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        original = json.dumps(SAMPLE_LLM, ensure_ascii=False)
        encrypted = agent_store_mod._encrypt_value(original)
        assert encrypted != original
        # Fernet tokens always start with "gAAAAA"
        assert encrypted.startswith("gAAAAA")

    def test_roundtrip_with_key(self, monkeypatch):
        """With a key, encrypt then decrypt returns original."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        original = json.dumps(SAMPLE_LLM, ensure_ascii=False)
        encrypted = agent_store_mod._encrypt_value(original)
        decrypted = agent_store_mod._decrypt_value(encrypted)
        assert decrypted == original

    def test_encrypt_none(self, monkeypatch):
        """_encrypt_value(None) returns None regardless of key."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        assert agent_store_mod._encrypt_value(None) is None

    def test_decrypt_none(self, monkeypatch):
        """_decrypt_value(None) returns None regardless of key."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        assert agent_store_mod._decrypt_value(None) is None

    def test_decrypt_corrupted_value_returns_none(self, monkeypatch):
        """With a key, corrupted ciphertext returns None."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        result = agent_store_mod._decrypt_value("invalid!@#")
        assert result is None

    def test_decrypt_corrupted_value_warns(self, monkeypatch, caplog):
        """With a key, corrupted ciphertext logs a warning."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        with caplog.at_level("WARNING"):
            agent_store_mod._decrypt_value("invalid!@#")
        assert len(caplog.records) >= 1
        assert "Failed to decrypt" in caplog.text


# ── AgentStore integration with encryption ──


class TestEncryptionIntegration:
    """AgentStore create/update/read with encryption enabled."""

    def test_encrypt_decrypt_roundtrip(self, agent_store, monkeypatch):
        """With key, create agent with llm_config, read back — values match."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        agent_store.create_agent(
            "crypto-agent",
            description="Encrypted agent",
            llm_config=SAMPLE_LLM,
        )
        got = agent_store.get_agent("crypto-agent")
        assert got is not None
        assert got["llm_config"] == SAMPLE_LLM

    def test_encrypt_stored_differs_from_plaintext(self, agent_store, monkeypatch):
        """With key, the raw DB value is base64 ciphertext, not JSON."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        agent_store.create_agent(
            "raw-check",
            llm_config=SAMPLE_LLM,
        )
        # Read raw from SQLite directly to bypass decryption
        raw_conn = sqlite3.connect(agent_store._db_path)
        try:
            row = raw_conn.execute(
                "SELECT llm_config FROM agents WHERE name = ?", ("raw-check",)
            ).fetchone()
        finally:
            raw_conn.close()
        raw_value = row[0]
        # Should NOT be the original JSON
        expected_json = json.dumps(SAMPLE_LLM, ensure_ascii=False)
        assert raw_value != expected_json
        # Fernet tokens start with "gAAAAA" (version + timestamp marker)
        assert raw_value.startswith("gAAAAA")

    def test_create_and_update_with_encryption(self, agent_store, monkeypatch):
        """With key, create agent, update llm_config — new data, old not mixed."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", TEST_FERNET)
        agent_store.create_agent(
            "updatable-crypto",
            llm_config=SAMPLE_LLM,
        )
        updated = agent_store.update_agent(
            "updatable-crypto",
            llm_config=UPDATED_LLM,
        )
        assert updated is not None
        assert updated["llm_config"] == UPDATED_LLM
        # Verify persistence
        got = agent_store.get_agent("updatable-crypto")
        assert got["llm_config"] == UPDATED_LLM
        # Old value is gone
        assert got["llm_config"] != SAMPLE_LLM

    def test_plaintext_fallback(self, agent_store, monkeypatch):
        """Without key, llm_config stored as plaintext JSON and read back correctly."""
        monkeypatch.setattr(agent_repo_mod, "_FERNET", None)
        agent_store.create_agent(
            "plain-agent",
            llm_config=SAMPLE_LLM,
        )
        # Read raw from SQLite — should be plain JSON
        raw_conn = sqlite3.connect(agent_store._db_path)
        try:
            row = raw_conn.execute(
                "SELECT llm_config FROM agents WHERE name = ?", ("plain-agent",)
            ).fetchone()
        finally:
            raw_conn.close()
        raw_value = row[0]
        expected_json = json.dumps(SAMPLE_LLM, ensure_ascii=False)
        assert raw_value == expected_json
        # Also verify via get_agent
        got = agent_store.get_agent("plain-agent")
        assert got["llm_config"] == SAMPLE_LLM
