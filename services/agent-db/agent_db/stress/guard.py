"""Exclusive ownership of one measured target (doc/stress/README.md §10, §11).

Two load runs against the same stand are not two measurements, they are one
measurement of both of them: the second generator's traffic lands inside the
first run's latency and neither manifest describes what happened. The bench has
the same problem and solved it with ``agent_db.bench.run_guard``; this module is
the stress-side sibling.

It is a sibling rather than a reuse, and the difference is not cosmetic:

- the bench guard's terminal statuses are ``completed|failed`` and it *raises*
  on anything else, while §10 requires ``aborted`` with an ``abort_reason`` -
  a run stopped because the image digest moved or the host stopped being quiet
  is evidence, and it is neither a success nor a failure of the platform;
- the bench guard writes into ``<artifact_root>/runs/<uuid>/`` and its manifest
  is the bench's flat v1, while §10 fixes ``test-results/stress/<uuid>/`` and a
  manifest split into ``environment`` and ``code``;
- the lock scope is the *measured target* (the MCP gateway or the chat API),
  not the bench's API URL, because those are the two hosts a second run would
  contaminate.

Phase 7 of the design folds both guards into one; doing it now would refactor a
working instrument (the bench) for a consumer that only just appeared, which is
the risk §13 names. Until then the two deliberately mirror each other, and this
one carries the tests that pin its own semantics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# §10: a run stopped for a reason that is neither success nor platform failure.
TERMINAL_STATUSES = ("completed", "failed", "aborted")


class StressRunInProgressError(RuntimeError):
    """Another process already owns this measured target."""


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON through a temp file and ``os.replace``.

    An evidence artefact half-written when the run is killed is worse than a
    missing one: it parses, and it says something the run never concluded. Used
    for every JSON artefact this package writes, not only the manifest.
    """
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


@dataclass(frozen=True)
class StressRunContext:
    """Where a run's evidence goes, and which lock protects it."""

    run_uuid: str
    started_at: str
    run_dir: Path
    lock_path: Path
    target: str


class StressRunGuard:
    """Own one measured target at a time, with §10's run directory layout."""

    def __init__(
        self,
        *,
        target: str,
        lock_root: Path,
        artifact_root: Path,
    ) -> None:
        if not target.strip():
            raise ValueError("a stress run must name the target it measures")
        self._target = target.rstrip("/")
        self._lock_root = Path(lock_root)
        self._artifact_root = Path(artifact_root)
        self._context: StressRunContext | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def _lock_path(self) -> Path:
        digest = hashlib.sha256(self._target.encode("utf-8")).hexdigest()[:16]
        return self._lock_root / ".stress-locks" / f"target-{digest}.lock"

    @property
    def context(self) -> StressRunContext:
        if self._context is None:
            raise RuntimeError("StressRunGuard has not acquired its lock")
        return self._context

    def _holder_payload(self, *, run_uuid: str) -> dict[str, Any]:
        return {
            "target": self._target,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "run_uuid": run_uuid,
            "started_at": self._now(),
        }

    def _clean_stale_lock(self, lock_path: Path, holder: dict[str, Any]) -> bool:
        """Remove a lock whose holder is demonstrably dead.

        Only a dead PID justifies this: a run can be legitimately idle for a long
        time (a 48-hour soak spends most of its life sleeping between ticks), so
        age is not evidence of staleness and is deliberately not used.
        """
        holder_pid = holder.get("pid")
        if isinstance(holder_pid, int) and not _pid_is_alive(holder_pid):
            logger.warning(
                "Removing stale stress lock (holder pid %s is dead): %s",
                holder_pid,
                lock_path,
            )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            return True
        return False

    def acquire(self) -> StressRunContext:
        """Take the lock and create the run directory, or refuse loudly."""
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        run_uuid = uuid.uuid4().hex

        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            holder = _read_json(lock_path)
            if self._clean_stale_lock(lock_path, holder):
                return self.acquire()
            raise StressRunInProgressError(
                "a stress run is already measuring "
                f"{self._target} (run_uuid={holder.get('run_uuid', 'unknown')}, "
                f"pid={holder.get('pid', 'unknown')}). Two generators on one stand "
                "are one measurement of both of them; wait for it to finish or "
                f"remove only a confirmed stale lock: {lock_path}"
            ) from exc

        try:
            payload = self._holder_payload(run_uuid=run_uuid)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            # §10 layout: <artifact_root>/<run_uuid>/, not bench's runs/<uuid>.
            run_dir = self._artifact_root / run_uuid
            run_dir.mkdir(parents=True, exist_ok=False)
            self._context = StressRunContext(
                run_uuid=run_uuid,
                started_at=str(payload["started_at"]),
                run_dir=run_dir,
                lock_path=lock_path,
                target=self._target,
            )
            return self._context
        except BaseException:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def release(self) -> None:
        """Drop the lock, refusing to release someone else's."""
        context = self.context
        holder = _read_json(context.lock_path)
        if holder.get("run_uuid") != context.run_uuid:
            raise RuntimeError(
                "stress lock ownership changed before release: refusing to "
                f"unlink {context.lock_path}"
            )
        context.lock_path.unlink()
        self._context = None

    def release_quiet(self) -> None:
        """Release if still owned; never raise.

        For abort paths: an owner that is already gone (or a lock someone else
        reclaimed through the stale-PID path) must not mask the original
        failure with a secondary release error.
        """
        try:
            self.release()
        except (RuntimeError, OSError, FileNotFoundError):
            pass


__all__ = [
    "TERMINAL_STATUSES",
    "StressRunContext",
    "StressRunGuard",
    "StressRunInProgressError",
    "write_json_atomic",
]
