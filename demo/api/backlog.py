"""Model interaction backlog — full trace of every model interaction.

Stores one file per session in the backlog directory.
Each record is pretty-printed JSON separated by ---===--- markers.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from demo.settings import settings

logger = logging.getLogger("demo.api.backlog")


class ModelBacklog:
    """Append-only trace of every model interaction, stored as pretty-printed JSON per session."""

    _SEPARATOR = "\n---===---\n"

    def __init__(self) -> None:
        self._dir = Path(settings.backlog_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._dir / f"{safe}.jsonl"

    def _get_session_id(self) -> str:
        return ""

    #  Writing

    def _record(self, **kw: Any) -> dict[str, Any]:
        return kw

    def _write(self, session_id: str, record: dict[str, Any]) -> None:
        path = self._path(session_id)
        try:
            text = json.dumps(record, ensure_ascii=False, indent=2, default=str)
        except Exception:
            logger.exception("Failed to serialize backlog record for session %s", session_id)
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + self._SEPARATOR)
        except OSError as e:
            logger.warning("Failed to write backlog record: %s", e)

    def _read_records(self, session_id: str) -> list[dict[str, Any]]:
        """Read all records from a session file.

        Supports both the new pretty-printed format (separator-based)
        and the legacy JSONL format (one JSON object per line).
        """
        path = self._path(session_id)
        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8")
        records: list[dict[str, Any]] = []

        parts = content.split(self._SEPARATOR)
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                # Fallback: try legacy JSONL (one object per line)
                for line in stripped.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        return records

    #  Public event methods

    def turn_start(self, session_id: str, user_message: str) -> str:
        turn_id = uuid.uuid4().hex[:12]
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": 0,
            "event": "turn_start",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"user_message": user_message},
        })
        return turn_id

    def model_request(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "model_request",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "num_messages": len(messages),
                "messages": messages,
                "num_tools": len(tools),
                "tools": tools,
                "model": getattr(settings, "ollama_model", None),
                "api_base": getattr(settings, "ollama_url", None),
            },
        })

    def model_response(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        response: dict[str, Any],
        duration_ms: float,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "model_response",
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 1),
            "tokens": token_usage,
            "data": {
                "content": response.get("content", ""),
                "tool_calls": response.get("tool_calls"),
                "reasoning_content": response.get("reasoning_content"),
                "finish_reason": response.get("finish_reason"),
            },
        })

    def stream_start(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        messages: list[dict[str, Any]],
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "stream_start",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "num_messages": len(messages),
                "messages": messages,
            },
        })

    def stream_end(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        full_text: str,
        duration_ms: float,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "stream_end",
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 1),
            "tokens": token_usage,
            "data": {
                "full_text": full_text,
                "chars": len(full_text),
            },
        })

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "tool_call",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"name": name, "arguments": arguments},
        })

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float,
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "tool_result",
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 1),
            "data": {
                "name": name,
                "result": result,
                "result_chars": len(result),
            },
        })

    def empty_round(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        reasoning_content: str | None,
        messages: list[dict[str, Any]],
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "empty_round",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "reasoning_content": reasoning_content or "",
                "num_messages": len(messages),
                "messages": messages,
            },
        })

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._write(session_id, {
            "session_id": session_id,
            "turn_id": turn_id,
            "iteration": iteration,
            "event": "error",
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"error": error, "context": context or {}},
        })

    #  Reading

    def list_sessions(self) -> list[dict[str, Any]]:
        paths = sorted(self._dir.glob("*.jsonl"))
        result = []
        for path in paths:
            session_id = path.stem
            records = self._read_records(session_id)
            first = records[0] if records else None
            last = records[-1] if records else None
            result.append({
                "session_id": session_id,
                "size_bytes": path.stat().st_size,
                "num_events": len(records),
                "first_event": first,
                "last_event": last,
            })
        return sorted(result, key=lambda s: s.get("first_event", {}).get("ts", ""), reverse=True)

    def read_session(
        self,
        session_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        records = self._read_records(session_id)
        return records[offset:offset + limit]

    #  Maintenance

    def cleanup_old(self) -> None:
        cutoff = time.time() - settings.backlog_retention_days * 86400
        for path in self._dir.glob("*.jsonl"):
            if path.stat().st_mtime < cutoff:
                try:
                    path.unlink()
                    logger.info("Cleaned up old backlog: %s", path.name)
                except OSError as e:
                    logger.warning("Failed to clean up %s: %s", path.name, e)


# Global singleton
backlog = ModelBacklog()