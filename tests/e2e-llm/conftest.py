"""Shared fixtures for LLM e2e tests (tests/e2e-llm).

Загружает .env (ADMIN_TOKEN, LLM-ключи) и переиспользует общие фикстуры
из tests/e2e/conftest.py через pytest_plugins.
"""

from __future__ import annotations

import os
from pathlib import Path

# Load .env at import time — before any test collects
_env = Path.cwd() / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val

# Переиспользуем fixtures/helpers из tests/e2e (project_root, health-check и т.д.)
pytest_plugins = ["tests.e2e.conftest"]
