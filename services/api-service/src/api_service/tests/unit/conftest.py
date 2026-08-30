"""Shared fixtures and test data for unit tests."""

import os
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


@pytest.fixture(autouse=True, scope="session")
def _isolate_runtime_artifacts(tmp_path_factory):
    """Keep the whole unit suite away from the live agent database.

    Several app-level tests (TestClient + lifespan) construct the agent
    store lazily via ``get_agent_store()``, which falls back to
    ``<session_db_dir>/agents.sqlite`` next to the developer's real
    ``demo_sessions.sqlite``. With an ``ENCRYPTION_KEY`` set, the repository
    constructor migrates any plaintext ``llm_config`` rows to ciphertext
    encrypted with the test key, silently corrupting the live dev database.

    Both mutated paths below are read lazily on first use (unlike the
    import-time ``settings.session_db_path`` and the module-level
    ``provider_store.DEFAULT_PROVIDERS_PATH`` constants, which cannot be
    redirected from a fixture), so setting the env vars here is effective.
    """
    runtime_dir = tmp_path_factory.mktemp("runtime-artifacts")
    agents_db = runtime_dir / "agents.sqlite"
    spending_store = runtime_dir / "spending.json"

    monkeypatched = [
        ("AGENT_DB_PATH", str(agents_db)),
        ("SPENDING_PERSISTENCE_PATH", str(spending_store)),
    ]
    previous = {key: os.environ.get(key) for key, _ in monkeypatched}
    for key, value in monkeypatched:
        os.environ[key] = value
    # Reset lazy singletons so repositories re-resolve the throwaway paths.
    import api_service.server.deps as deps

    deps._agent_store = None

    yield

    import api_service.server.deps as deps_teardown

    deps_teardown._agent_store = None
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
