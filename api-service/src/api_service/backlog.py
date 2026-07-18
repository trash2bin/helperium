"""Model interaction backlog — full trace of every model interaction.

Stores one file per session in the backlog directory.
Each record is pretty-printed JSON separated by ---===--- markers.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api_service.prometheus_metrics import backlog_records_total, backlog_errors_total
from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.backlog")

# Record type constants
RECORD_LLM_CALL = "llm_call"
RECORD_ERROR = "error"


class ModelBacklog:
    """Append-only trace of every model interaction, stored as pretty-printed JSON per session."""

    _SEPARATOR = "\n---===---\n"

    def __init__(self) -> None:
        self._dir = Path(settings.backlog_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._dir / f"{safe}.jsonl"

    def _should_write(self, record: dict[str, Any]) -> bool:
        mode = settings.backlog_mode
        if mode == "off":
            return False
        if mode == "errors":
            return record.get("event") == "error" or record.get("type") == RECORD_ERROR
        return True  # full

    def _write(self, session_id: str, record: dict[str, Any]) -> None:
        if not self._should_write(record):
            return
        backlog_records_total.labels(
            type=record.get("type", record.get("event", "unknown"))
        ).inc()
        if record.get("type") == RECORD_ERROR or record.get("event") == "error":
            backlog_errors_total.labels(
                error_type=record.get("data", {}).get("error", "internal")[:50]
                if isinstance(record.get("data"), dict)
                else "internal"
            ).inc()
        path = self._path(session_id)
        try:
            text = json.dumps(record, ensure_ascii=False, indent=2, default=str)
        except Exception:
            logger.exception(
                "Failed to serialize backlog record for session %s", session_id
            )
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
        self._write(
            session_id,
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": 0,
                "event": "turn_start",
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {"user_message": user_message},
            },
        )
        return turn_id

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self._write(
            session_id,
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": iteration,
                "event": "tool_call",
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {"name": name, "arguments": arguments},
            },
        )

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float,
    ) -> None:
        self._write(
            session_id,
            {
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
            },
        )

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._write(
            session_id,
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": iteration,
                "event": "error",
                "type": RECORD_ERROR,
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {"error": error, "context": context or {}},
            },
        )

    def record_llm_call(
        self,
        session_id: str,
        *,
        model: str,
        provider: str,
        duration_ms: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: float = 0.0,
        status: str = "ok",
        error_message: str | None = None,
        **extra: Any,
    ) -> None:
        """Record an LLM call with token usage and cost."""
        record: dict[str, Any] = {
            "type": RECORD_LLM_CALL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "provider": provider,
            "duration_ms": round(duration_ms, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost": round(cost, 6),
            "status": status,
        }
        if error_message:
            record["error_message"] = error_message
        record.update(extra)
        self._write(session_id, record)

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        """Get aggregated stats for a session: total tokens, cost, call count."""
        records = self._read_records(session_id)
        llm_calls = [r for r in records if r.get("type") == RECORD_LLM_CALL]
        errors = [
            r
            for r in records
            if r.get("type") == RECORD_ERROR
            or (r.get("status") and r["status"] != "ok")
        ]

        return {
            "session_id": session_id,
            "total_llm_calls": len(llm_calls),
            "total_errors": len(errors),
            "total_prompt_tokens": sum(r.get("prompt_tokens", 0) for r in llm_calls),
            "total_completion_tokens": sum(
                r.get("completion_tokens", 0) for r in llm_calls
            ),
            "total_tokens": sum(r.get("total_tokens", 0) for r in llm_calls),
            "total_cost": round(sum(r.get("cost", 0.0) for r in llm_calls), 6),
        }

    def get_recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get most recent error records across all sessions."""
        errors: list[dict[str, Any]] = []
        for path in sorted(
            self._dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            session_id = path.stem
            records = self._read_records(session_id)
            for r in records:
                if r.get("type") == RECORD_ERROR or (
                    r.get("status") and r["status"] != "ok"
                ):
                    errors.append(
                        {
                            "session_id": session_id,
                            "timestamp": r.get("timestamp"),
                            "error": r.get("error_message", str(r.get("error", ""))),
                            "model": r.get("model", ""),
                        }
                    )
            if len(errors) >= limit:
                break
        return errors[:limit]

    #  Reading

    def list_sessions(self) -> list[dict[str, Any]]:
        paths = sorted(self._dir.glob("*.jsonl"))
        result = []
        for path in paths:
            session_id = path.stem
            records = self._read_records(session_id)
            first = records[0] if records else None
            last = records[-1] if records else None
            result.append(
                {
                    "session_id": session_id,
                    "size_bytes": path.stat().st_size,
                    "num_events": len(records),
                    "first_event": first,
                    "last_event": last,
                }
            )
        return sorted(
            result, key=lambda s: s.get("first_event", {}).get("ts", ""), reverse=True
        )

    def read_session(
        self,
        session_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        records = self._read_records(session_id)
        return records[offset : offset + limit]

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
