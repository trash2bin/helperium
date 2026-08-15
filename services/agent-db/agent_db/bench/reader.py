"""Reader for backlog JSONL files — parse recorded interactions."""

import json
import os
from pathlib import Path
from typing import Any

RECORD_SEPARATOR = "\n---===---\n"


def read_backlog_file(path: Path) -> list[dict[str, Any]]:
    """Read a backlog JSONL file, handling both separator and legacy formats.

    The primary format uses ``---===---`` separators between pretty-printed JSON
    objects (written by ``ModelBacklog``).  Falls back to one-JSON-per-line when
    a part fails to parse.
    """
    content = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []

    for part in content.split(RECORD_SEPARATOR):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            # Legacy format: one JSON object per line
            for line in stripped.splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    return records


def find_backlog_dir(backlog_dir: str | None = None) -> Path:
    """Find backlog directory with this priority:

    1. Explicit ``backlog_dir`` argument
    2. ``BACKLOG_DIR`` environment variable
    3. Project root ``services/api-service/backlog``
    4. Project root ``.data/backlog``
    5. Project root ``backlog``
    6. Project root ``services/api-service/src/backlog``

    The first existing directory containing ``*.jsonl`` files wins.
    If none exist, returns ``.data/backlog`` as default.
    """
    if backlog_dir:
        return Path(backlog_dir).resolve()

    env_dir = os.environ.get("BACKLOG_DIR")
    if env_dir:
        return Path(env_dir).resolve()

    # Walk up from cwd to find project root
    cwd = Path.cwd()
    root: Path = cwd
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            root = parent
            break

    candidates = [
        root / "services" / "api-service" / "backlog",
        root / ".data" / "backlog",
        root / "backlog",
        root / "services" / "api-service" / "src" / "backlog",
    ]
    for c in candidates:
        if c.exists() and list(c.glob("*.jsonl")):
            return c.resolve()

    # Default fallback
    return (root / ".data" / "backlog").resolve()
