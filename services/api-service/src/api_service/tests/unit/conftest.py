"""Shared fixtures and test data for unit tests."""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from api_service.agent_repository import SqliteAgentRepository

# Silence OpenTelemetry export noise: no OTLP collector runs during unit tests.
# Must be set before api_service/helperium_sdk import tracing.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("OTEL_ENABLED", "false")

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


class FakeChatSessionStore:
    """Hermetic stand-in for the SQLite-backed chat session store.

    Chat routes read/bind a session capability token on every request; unit
    tests must neither touch the developer's real session DB nor depend on
    its state. The fake patches only ``api_service.server.routes.chat`` —
    transcript writes go through the orchestrator's own session_store
    reference and anti-abuse tests patch ``security.session_store`` at their
    own seam.
    """

    def __init__(self) -> None:
        self._token_hashes: dict[str, str] = {}

    def session_token_hash(self, session_id: str) -> str | None:
        return self._token_hashes.get(session_id)

    def bind_session_token(self, session_id: str, token_hash: str) -> str:
        return self._token_hashes.setdefault(session_id, token_hash)

    def abuse_state(self, session_id: str) -> SimpleNamespace:  # noqa: ARG002
        return SimpleNamespace(user_turn_count=1, last_user_turn_at=None)

    def accept_user_turn(self, session_id: str, accepted_at: float) -> None:  # noqa: ARG002
        return None


@pytest.fixture(autouse=True)
def hermetic_chat_session_store(monkeypatch):
    """Route every chat-route session call to :class:`FakeChatSessionStore`."""
    monkeypatch.setattr(
        "api_service.server.routes.chat.session_store", FakeChatSessionStore()
    )


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
    spending_ledger = runtime_dir / "spending-ledger.sqlite3"
    reports_db = runtime_dir / "reports.sqlite3"

    monkeypatched = [
        ("AGENT_DB_PATH", str(agents_db)),
        ("SPENDING_PERSISTENCE_PATH", str(spending_store)),
        ("SPENDING_LEDGER_PATH", str(spending_ledger)),
        ("REPORTS_DB_PATH", str(reports_db)),
    ]
    previous = {key: os.environ.get(key) for key, _ in monkeypatched}
    for key, value in monkeypatched:
        os.environ[key] = value
    # Reset lazy singletons so repositories re-resolve the throwaway paths.
    from helperium_sdk.settings import settings

    from api_service.reports import reset_report_store
    from api_service.server import deps
    from api_service.spending import reset_spending_singletons

    deps._agent_store = None
    settings.spending_ledger_path = str(spending_ledger)
    settings.reports_db_path = str(reports_db)
    reset_spending_singletons()
    reset_report_store()

    yield

    import api_service.server.deps as deps_teardown

    reset_spending_singletons()
    reset_report_store()

    deps_teardown._agent_store = None
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
