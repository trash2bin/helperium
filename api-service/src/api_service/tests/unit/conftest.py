"""Shared fixtures and test data for unit tests."""

import tempfile
from pathlib import Path

import pytest

from api_service.agent_repository import SqliteAgentRepository


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
    store = SqliteAgentRepository(path)
    yield store
    Path(path).unlink(missing_ok=True)
