"""Stress run guard contract tests (§10, §11).

The lock is what keeps two generators from measuring each other, so its
semantics are pinned here: the layout it creates, the refusal it raises, and the
one case where removing a lock is correct (a dead holder) versus the case where
it never is (an idle one - a 48-hour soak spends most of its life sleeping).
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time

import pytest

from agent_db.stress.guard import (
    TERMINAL_STATUSES,
    StressRunGuard,
    StressRunInProgressError,
    write_json_atomic,
)

TARGET = "http://127.0.0.1:8083/mcp"


def _noop() -> None:
    """Module-level so ``multiprocessing`` can pickle it under the spawn start method."""


def _dead_pid() -> int:
    """A PID that is guaranteed to have exited by the time this returns."""
    process = multiprocessing.Process(target=_noop)
    process.start()
    pid = process.pid
    process.join(timeout=10)
    assert pid is not None
    assert not process.is_alive()
    return pid


@pytest.fixture
def guard(tmp_path):
    return StressRunGuard(
        target=TARGET,
        lock_root=tmp_path,
        artifact_root=tmp_path / "test-results",
    )


class TestAcquire:
    def test_the_run_directory_follows_the_documented_layout(self, guard, tmp_path):
        context = guard.acquire()
        # §10: test-results/stress/<run_uuid>/ - not the bench's runs/<uuid>/.
        assert context.run_dir == tmp_path / "test-results" / context.run_uuid
        assert context.run_dir.is_dir()
        assert context.target == TARGET

    def test_the_lock_is_scoped_to_the_target_and_holds_the_owner(
        self, guard, tmp_path
    ):
        context = guard.acquire()
        assert context.lock_path.parent == tmp_path / ".stress-locks"
        holder = json.loads(context.lock_path.read_text(encoding="utf-8"))
        assert holder["run_uuid"] == context.run_uuid
        assert holder["pid"] == os.getpid()
        assert holder["target"] == TARGET

    def test_a_second_run_on_the_same_target_is_refused(self, guard):
        first = guard.acquire()
        second = StressRunGuard(
            target=TARGET,
            lock_root=first.lock_path.parents[1],
            artifact_root=first.run_dir.parent,
        )
        with pytest.raises(StressRunInProgressError) as excinfo:
            second.acquire()
        # The refusal names the holder so an operator can decide, rather than
        # guessing whether the lock is stale.
        assert first.run_uuid in str(excinfo.value)
        assert str(os.getpid()) in str(excinfo.value)

    def test_a_different_target_gets_its_own_lock(self, guard, tmp_path):
        first = guard.acquire()
        other = StressRunGuard(
            target="http://127.0.0.1:8081",
            lock_root=tmp_path,
            artifact_root=tmp_path / "test-results",
        )
        second = other.acquire()
        assert second.lock_path != first.lock_path
        assert second.run_dir != first.run_dir


class TestStaleLocks:
    def test_a_lock_whose_holder_is_dead_is_cleaned(self, guard, tmp_path):
        lock_dir = tmp_path / ".stress-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        # Rebuild the exact path the guard would use, so the test exercises the
        # real digest rather than a copy of it.
        context = guard.acquire()
        lock_path = context.lock_path
        guard.release()
        lock_path.write_text(
            json.dumps({"run_uuid": "dead", "pid": _dead_pid(), "target": TARGET}),
            encoding="utf-8",
        )

        reclaimed = guard.acquire()
        assert reclaimed.run_uuid != "dead"
        assert reclaimed.lock_path == lock_path

    def test_an_idle_holder_is_not_stale(self, guard):
        # A soak sleeps for minutes between ticks; age is not evidence, and a
        # lock removed out from under a live run would let a second generator
        # start on top of it. The live holder here is a *different* process id
        # that is still alive: this one.
        guard.acquire()
        held = guard.context
        reclaimer = StressRunGuard(
            target=TARGET,
            lock_root=held.lock_path.parents[1],
            artifact_root=held.run_dir.parent,
        )
        time.sleep(0.01)
        with pytest.raises(StressRunInProgressError):
            reclaimer.acquire()
        assert held.lock_path.exists()


class TestRelease:
    def test_release_removes_only_this_runs_lock(self, guard):
        context = guard.acquire()
        guard.release()
        assert not context.lock_path.exists()
        # A second acquire succeeds, which is the property release exists for.
        assert guard.acquire().run_dir != context.run_dir

    def test_releasing_a_lock_someone_else_took_is_refused(self, guard):
        context = guard.acquire()
        # Simulate another owner having replaced the holder payload.
        context.lock_path.write_text(
            json.dumps({"run_uuid": "someone-else", "pid": os.getpid()}),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="ownership changed"):
            guard.release()

    def test_context_is_unavailable_before_and_after_the_lock(self, guard):
        with pytest.raises(RuntimeError, match="has not acquired"):
            _ = guard.context
        guard.acquire()
        guard.release()
        with pytest.raises(RuntimeError, match="has not acquired"):
            _ = guard.context

    def test_a_target_is_mandatory(self, tmp_path):
        with pytest.raises(ValueError, match="must name the target"):
            StressRunGuard(
                target="  ", lock_root=tmp_path, artifact_root=tmp_path
            )


class TestStatusVocabulary:
    def test_aborted_is_a_terminal_status(self):
        # §10 introduces `aborted` + `abort_reason`; the bench guard raises on it
        # and that is exactly why this package needs its own status vocabulary.
        assert "aborted" in TERMINAL_STATUSES
        assert set(TERMINAL_STATUSES) == {"completed", "failed", "aborted"}


class TestAtomicJson:
    def test_writes_the_payload_and_leaves_no_temp_file(self, tmp_path):
        path = tmp_path / "nested" / "status.json"
        write_json_atomic(path, {"status": "aborted", "abort_reason": "digest moved"})
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "status": "aborted",
            "abort_reason": "digest moved",
        }
        assert list(path.parent.glob("*.tmp")) == []

    def test_overwrites_in_place(self, tmp_path):
        path = tmp_path / "status.json"
        write_json_atomic(path, {"status": "running"})
        write_json_atomic(path, {"status": "completed"})
        assert json.loads(path.read_text(encoding="utf-8"))["status"] == "completed"
