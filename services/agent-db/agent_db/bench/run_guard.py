"""Exclusive local ownership and evidence layout for benchmark CLI runs."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BenchmarkRunInProgressError(RuntimeError):
    """Raised when another process already owns an isolated API benchmark lock."""


@dataclass(frozen=True)
class BenchmarkRunContext:
    """Filesystem locations and immutable identity for a single benchmark run."""

    run_uuid: str
    started_at: str
    run_dir: Path
    manifest_path: Path
    lock_path: Path


class BenchmarkRunGuard:
    """Own one benchmark target at a time and preserve isolated run evidence.

    The lock scope is the normalized API URL under one local bench-log root.
    That deliberately blocks concurrent agents and tenants on the same isolated API,
    because they can still contend for the same upstream provider credential.
    """

    _MANIFEST_VERSION = 1

    def __init__(
        self,
        *,
        api_url: str,
        lock_root: Path,
        artifact_root: Path,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._lock_root = Path(lock_root)
        self._artifact_root = Path(artifact_root)
        self._context: BenchmarkRunContext | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _lock_path(self) -> Path:
        digest = hashlib.sha256(self._api_url.encode("utf-8")).hexdigest()[:16]
        return self._lock_root / ".benchmark-locks" / f"api-{digest}.lock"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def _holder_payload(self, *, run_uuid: str) -> dict[str, Any]:
        return {
            "api_url": self._api_url,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "run_uuid": run_uuid,
            "started_at": self._now(),
        }

    def _manifest_payload(
        self,
        *,
        status: str,
        completed_at: str | None = None,
        report_path: Path | None = None,
    ) -> dict[str, Any]:
        context = self.context
        payload: dict[str, Any] = {
            "api_url": self._api_url,
            "bench_log_dir": str(context.run_dir),
            "host": socket.gethostname(),
            "lock_path": str(context.lock_path),
            "manifest_version": self._MANIFEST_VERSION,
            "pid": os.getpid(),
            "run_uuid": context.run_uuid,
            "started_at": context.started_at,
            "status": status,
        }
        if completed_at is not None:
            payload["completed_at"] = completed_at
        if report_path is not None:
            payload["report_path"] = str(report_path)
        return payload

    @property
    def context(self) -> BenchmarkRunContext:
        if self._context is None:
            raise RuntimeError("BenchmarkRunGuard has not acquired its lock")
        return self._context

    def acquire(self) -> BenchmarkRunContext:
        """Atomically acquire target ownership and write a running manifest."""
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        run_uuid = uuid.uuid4().hex

        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            holder = self._read_json(lock_path)
            holder_run_uuid = holder.get("run_uuid", "unknown")
            holder_pid = holder.get("pid", "unknown")
            raise BenchmarkRunInProgressError(
                "Benchmark already running for API "
                f"{self._api_url} (run_uuid={holder_run_uuid}, pid={holder_pid}). "
                f"Inspect or remove only a confirmed stale lock: {lock_path}"
            ) from exc

        try:
            holder_payload = self._holder_payload(run_uuid=run_uuid)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(holder_payload, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            run_dir = self._artifact_root / "runs" / run_uuid
            run_dir.mkdir(parents=True, exist_ok=False)
            self._context = BenchmarkRunContext(
                run_uuid=run_uuid,
                started_at=str(holder_payload["started_at"]),
                run_dir=run_dir,
                manifest_path=run_dir / "run-manifest.json",
                lock_path=lock_path,
            )
            self._write_json_atomic(
                self.context.manifest_path,
                self._manifest_payload(status="running"),
            )
            return self.context
        except BaseException:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def finalize(self, *, status: str, report_path: Path | None = None) -> None:
        """Persist terminal evidence then release only this guard's lock file."""
        if status not in {"completed", "failed"}:
            raise ValueError(f"Unsupported benchmark terminal status: {status}")

        context = self.context
        self._write_json_atomic(
            context.manifest_path,
            self._manifest_payload(
                status=status,
                completed_at=self._now(),
                report_path=report_path,
            ),
        )
        holder = self._read_json(context.lock_path)
        if holder.get("run_uuid") != context.run_uuid:
            raise RuntimeError("Benchmark lock ownership changed before release")
        context.lock_path.unlink()
