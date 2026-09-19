"""Root test isolation: strip dotenv-sourced secrets before tests run.

litellm calls ``load_dotenv()`` at import time (litellm/__init__.py), which
loads the developer's root ``.env`` — real tokens included — into
``os.environ`` during pytest collection. When multiple suites share one
pytest process (ad-hoc api + demo combined runs), the demo suites' env
asserts then see real secrets. ``make ci`` runs suites as separate
processes, so CI is unaffected; this conftest makes combined runs hermetic
too.

Only ``.env``-sourced keys are stripped, and only when the developer did not
export them before the run: an explicitly exported var is a deliberate test
input and survives. Non-.env additions (OTEL silencing, test runtime-artifact
paths set by suite conftests) are legitimate and stay untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Snapshot taken at conftest import — before any test module (and thus
# litellm) is imported by collection.
_PREEXISTING_ENV_KEYS = frozenset(os.environ)


def _dotenv_keys() -> frozenset[str]:
    """Keys defined in the root .env — the only ones litellm's import-time
    load_dotenv() can inject into the process environment."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return frozenset()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return frozenset(keys)


_DOTENV_KEYS = _dotenv_keys()


@pytest.fixture(scope="session", autouse=True)
def _strip_litellm_dotenv_leak():
    """Remove .env-sourced keys that dotenv injected during collection."""
    for key in _DOTENV_KEYS:
        if key not in _PREEXISTING_ENV_KEYS and key in os.environ:
            del os.environ[key]
    yield
