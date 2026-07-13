"""Shared e2e test configuration and fixtures.

Usage:
    # With full traceback (default)
    uv run pytest tests/e2e/ -v

    # Minimal output (only pass/fail, no traceback on errors)
    uv run pytest tests/e2e/ -v --tb=short

    # Completely silent on pass (only show failures)
    uv run pytest tests/e2e/ -q --tb=line

    # No traceback at all (just error line)
    uv run pytest tests/e2e/ -q --no-traceback
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _load_dotenv(path: Path | None = None) -> None:
    """Load .env file into os.environ if not already set.

    Doesn't override existing env vars. Simple key=value parser,
    no dependency on python-dotenv.
    """
    if path is None:
        path = Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val


# Load .env at import time — before any test collects
_load_dotenv()


def pytest_addoption(parser: pytest.Parser) -> None:
    """Custom CLI flags for e2e tests."""
    parser.addoption(
        "--no-traceback",
        action="store_true",
        default=False,
        help="Hide traceback, show only error summary (like --tb=line but cleaner)",
    )
    parser.addoption(
        "--llm-key",
        type=str,
        default=None,
        help="LLM API key for chat tests (reads LLM_API_KEY env if not set)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Apply --no-traceback flag."""
    if config.getoption("--no-traceback"):
        config.option.tbstyle = "line"


# ── Project paths ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def project_root() -> Path:
    """Project root directory."""
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def scenarios_dir(project_root: Path) -> Path:
    """Scenario data directory."""
    return project_root / "data-service" / "testdata" / "scenarios"


@pytest.fixture(scope="session")
def tenants_data_dir(project_root: Path) -> Path:
    """Tenant config persistence directory."""
    return project_root / ".data" / "tenants"


# ── Service URLs ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def data_service_url() -> str:
    return os.environ.get("DATA_SERVICE_URL", "http://127.0.0.1:8084")


@pytest.fixture(scope="session")
def mcp_gateway_url() -> str:
    return os.environ.get("MCP_SERVICE_URL", "http://127.0.0.1:8083")


@pytest.fixture(scope="session")
def api_service_url() -> str:
    host = os.environ.get("DEMO_API_HOST", "127.0.0.1")
    port = os.environ.get("DEMO_API_PORT", "8081")
    return os.environ.get("API_SERVICE_URL", f"http://{host}:{port}")


@pytest.fixture(scope="session")
def demo_web_url() -> str:
    host = os.environ.get("DEMO_WEB_HOST", "127.0.0.1")
    port = os.environ.get("DEMO_WEB_PORT", "8080")
    return os.environ.get("DEMO_WEB_URL", f"http://{host}:{port}")


@pytest.fixture(scope="session")
def rag_url() -> str:
    return os.environ.get("RAG_SERVICE_URL", "http://127.0.0.1:8082")


# ── Auth ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def admin_token() -> str | None:
    return os.environ.get("ADMIN_TOKEN") or os.environ.get("ADMIN_API_TOKEN")


# ── LLM ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def llm_api_key() -> str | None:
    """Read from --llm-key CLI arg, env, or .env."""
    return os.environ.get("MISTRAL_API_KEY") or os.environ.get("LLM_API_KEY")


@pytest.fixture(scope="session")
def llm_model() -> str:
    return os.environ.get("MISTRAL_MODEL", "mistral/mistral-medium-latest")


# ── Health check (optional — doesn't block collection) ─────────────────────

@pytest.fixture(autouse=True, scope="session")
def _check_services():
    """Quick check that required services are reachable.

    This runs once per session, before any test. Doesn't block collection.
    Skips tests if data-service or mcp-gateway is unreachable.
    """
    import requests

    services = {
        "data-service": ("http://127.0.0.1:8084/health", False),
        "mcp-gateway": ("http://127.0.0.1:8083/health", False),
    }
    fatal = []
    for name, (url, optional) in services.items():
        try:
            r = requests.get(url, timeout=3)
            if r.status_code >= 500:
                fatal.append(f"{name} at {url} returned {r.status_code}")
        except requests.ConnectionError:
            if not optional:
                fatal.append(f"{name} at {url} — connection refused")
        except Exception as e:
            if not optional:
                fatal.append(f"{name} at {url} — {e}")

    if fatal:
        pytest.skip("Required services unavailable:\n" + "\n".join(f"  {f}" for f in fatal))
