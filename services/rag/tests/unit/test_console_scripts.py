"""Регрессия: `rag` импортируется нормально — без PYTHONPATH, шимов и pytest-подкладки.

Симптом из жизни: `uv run agent-seedgen --students 3 --grades 3` падал с
`ModuleNotFoundError: No module named 'rag'`. Причина — плоский layout: `services/rag`
сам был пакетом, установка выкладывала его содержимое как верхнеуровневые модули
(`config`, `service`, `fixtures`…), и ни console script, ни обычный
`python -c "import rag"` пакет не находили. В тестах это маскировалось (pytest
подкладывал `services/`), в контейнере — `PYTHONPATH=/app/services`.

Держится `[tool.setuptools.package-dir]` в `services/rag/pyproject.toml`: директория
сервиса (плоский layout) маппится в пакет `rag`, поэтому ни переносить файлы,
ни подкладывать `sys.path` не нужно.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

RAG_PROJECT_DIR = Path(__file__).resolve().parents[2]
EXPECTED_SUFFIX = os.path.join("services", "rag", "__init__.py")
EXPECTED_SCRIPTS = {
    "agent-rag": "rag.service:main",
    "agent-rag-docgen": "rag.fixtures.cli_docgen:main",
    "agent-rag-ingest": "rag.fixtures.cli_ingest:main",
    "agent-seedgen": "rag.fixtures.seedgen:main",
}


def test_rag_imports_without_pythonpath_in_clean_subprocess() -> None:
    """В чистом окружении (cwd=/tmp, без PYTHONPATH) `import rag` работает как обычно."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, "-c", "import rag; print(rag.__file__)"],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        f"`import rag` без PYTHONPATH сломан (значит layout/упаковка уехали): {proc.stderr}"
    )
    assert proc.stdout.strip().endswith(EXPECTED_SUFFIX), proc.stdout


def test_entry_points_are_real_modules_without_shims() -> None:
    """`[project.scripts]` смотрит прямо на `rag.*` — без обёрток, которые правят sys.path."""
    scripts = tomllib.loads(
        (RAG_PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["scripts"]

    assert scripts == EXPECTED_SCRIPTS, (
        f"entry points изменились: {scripts}. Любая обёртка вместо rag.*:main означает, "
        f"что пакет снова не импортируется нормально"
    )
