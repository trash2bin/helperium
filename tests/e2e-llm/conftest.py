"""Shared fixtures for LLM e2e tests (tests/e2e-llm).

Грузит .env вручную (вместо pytest_plugins — он ломает совместный запуск
``pytest tests/e2e tests/e2e-llm``: плагин tests.e2e.conftest регистрируется
дважды). Сами тесты импортируют helpers-функции напрямую из
``tests.e2e.helpers`` — pytest-фикстуры из tests/e2e/conftest им не нужны.
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
