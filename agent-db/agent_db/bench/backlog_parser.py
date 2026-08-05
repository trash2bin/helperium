"""Backlog reader — parse api-service backlog JSONL into BacklogData.

The backlog file name is ``{session_id}.jsonl`` where the server-side
``session_id`` is ``agent:{agent_name}:{client_session}`` — i.e. the raw
``bench-xxxx`` client session id is *embedded* in the filename.  So we
locate the file by substring match on the filename, then parse records
separated by ``---===---``.

Reuses ``read_backlog_file`` from :mod:`.reader` (handles both the
pretty-printed separator format and legacy one-JSON-per-line).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BacklogData
from .reader import read_backlog_file


def find_backlog_file(backlog_dir: Path | str, session_id: str) -> Path | None:
    """Locate the backlog file for a client session id.

    The file is named ``agent:{agent}:{client_session}.jsonl`` so we match
    by substring ``session_id`` anywhere in the filename.  Returns None if
    no file matches.

    Args:
        backlog_dir: Directory containing ``*.jsonl`` backlog files.
        session_id: Client session id (e.g. ``bench-abc123``).
    """
    d = Path(backlog_dir)
    if not d.exists():
        return None
    # Exact filename first (defensive — server may change naming)
    exact = d / f"{session_id}.jsonl"
    if exact.exists():
        return exact
    # Substring match on stem (handles agent:xxx:bench-xxx naming)
    for p in sorted(d.glob("*.jsonl")):
        if session_id in p.stem:
            return p
    return None


def parse_backlog_data(backlog_dir: Path | str, session_id: str) -> BacklogData | None:
    """Parse the turn_end record for one session into BacklogData.

    Returns None if the backlog file is missing or has no ``turn_end``.

    Args:
        backlog_dir: Directory with backlog ``*.jsonl`` files.
        session_id: Client session id (``bench-xxxx``).
    """
    path = find_backlog_file(backlog_dir, session_id)
    if path is None:
        return None

    records = read_backlog_file(path)
    for rec in records:
        if rec.get("type") == "turn_end":
            return BacklogData.from_turn_end(rec)
    return None


def read_all_records(backlog_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    """Read all records for one session (raw dicts), or [] if missing."""
    path = find_backlog_file(backlog_dir, session_id)
    if path is None:
        return []
    return read_backlog_file(path)
