"""Tests for backlog concurrent write safety — HIGH-2.

ModelBacklog._write() opens the file for each record and does a single
``f.write(text + separator)`` call.  While this is a single Python call,
Python's ``TextIOWrapper`` may split it into multiple ``write()``
syscalls when the data exceeds the internal buffer (~8KB).  Concurrent
writes to the same session file can then produce corrupted/truncated
records because individual ``write()`` syscalls from different threads
interleave at the OS level.

This test proves the race exists and will PASS only after a per-session
write lock is introduced (e.g. ``threading.Lock`` per session path).
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest

from api_service.backlog import ModelBacklog


@pytest.fixture
def backlog_tmpdir():
    """Create a ModelBacklog with a temp directory."""
    with tempfile.TemporaryDirectory() as td:
        with patch("api_service.backlog.settings.backlog_dir", td):
            bl = ModelBacklog()
            yield bl


def test_concurrent_writes_to_same_session(backlog_tmpdir):
    """Concurrent _write() calls to the same session file must not
    produce corrupted records.

    We write LARGE records (bigger than the default Python IO buffer of
    8KB) from multiple threads simultaneously to force interleaved
    write() syscalls.

    The test:
    1. Fires N threads, each writing a unique large record to the same session
    2. Waits for all to complete
    3. Reads the file back and verifies:
       - Every record is valid JSON
       - Exactly N records are readable
       - No records are truncated or interleaved

    WITHOUT a per-session lock this test MUST FAIL because the OS-level
    write() calls from concurrent threads can interleave.

    WITH a per-session lock this test MUST PASS.
    """
    N_WORKERS = 8
    LARGE_CONTENT = "x" * 12_000  # > 8KB default IO buffer

    session_id = "concurrent-session"
    records_written = []

    # Build N unique records, each with a unique tracking ID
    for i in range(N_WORKERS):
        record = {
            "type": "llm_call",
            "session_id": session_id,
            "tracking_id": i,
            "payload": f"{i}:{LARGE_CONTENT}",
        }
        records_written.append(record)

    def _write_record(record: dict) -> None:
        backlog_tmpdir._write(session_id, record)

    # Fire concurrent writes from a thread pool
    with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = [executor.submit(_write_record, r) for r in records_written]
        for f in as_completed(futures):
            exc = f.exception()
            if exc:
                raise AssertionError(f"Worker failed: {exc}") from exc

    # ── Verification ─────────────────────────────────────────────────
    path = backlog_tmpdir._path(session_id)
    assert path.exists(), "Backlog file should exist"

    raw = path.read_text(encoding="utf-8")
    records_parsed = backlog_tmpdir._read_records(session_id)

    # Check 1: exactly N records must be recoverable
    assert len(records_parsed) == N_WORKERS, (
        f"Expected {N_WORKERS} records, got {len(records_parsed)}. "
        f"File size: {path.stat().st_size} bytes. "
        f"Raw content preview (first 500 chars): {raw[:500]}"
    )

    # Check 2: every record must be valid JSON (already done by _read_records)
    # Check 3: each tracking_id must be present exactly once
    recovered_ids = {r.get("tracking_id") for r in records_parsed if "tracking_id" in r}
    expected_ids = set(range(N_WORKERS))
    assert recovered_ids == expected_ids, (
        f"Missing tracking IDs: {expected_ids - recovered_ids}. "
        f"Extra IDs: {recovered_ids - expected_ids}. "
        f"Raw content (last 500 chars): {raw[-500:]}"
    )

    # Check 4: no truncated records — each payload must contain its full content
    for r in records_parsed:
        tid = r.get("tracking_id")
        expected_payload = f"{tid}:{LARGE_CONTENT}"
        actual_payload = r.get("payload", "")
        assert actual_payload == expected_payload, (
            f"Record {tid} payload truncated or corrupted: "
            f"expected {len(expected_payload)} chars, "
            f"got {len(actual_payload)} chars. "
            f"Content diff at end: expected='...{expected_payload[-100:]}', "
            f"got='...{actual_payload[-100:]}'"
        )


def test_concurrent_asyncio_writes_to_same_session(backlog_tmpdir):
    """Same as test_concurrent_writes_to_same_session but using asyncio.

    In the asyncio path, the backlog writes happen via _AsyncBacklogWriter
    which calls backlog._write() synchronously on the event loop. Without
    a per-session lock, concurrent asyncio.Tasks writing to the same file
    will interleave at the OS level, producing the same corruption pattern
    as threaded writes.
    """
    import asyncio

    N_WORKERS = 8
    LARGE_CONTENT = "z" * 12_000

    session_id = "async-concurrent-session"
    records_written = []

    for i in range(N_WORKERS):
        record = {
            "type": "llm_call",
            "session_id": session_id,
            "tracking_id": i,
            "payload": f"{i}:{LARGE_CONTENT}",
        }
        records_written.append(record)

    async def _write_async(record: dict) -> None:
        await asyncio.to_thread(backlog_tmpdir._write, session_id, record)

    async def run_concurrent():
        tasks = [_write_async(r) for r in records_written]
        await asyncio.gather(*tasks)

    asyncio.run(run_concurrent())

    # ── Verification ─────────────────────────────────────────────────
    records_parsed = backlog_tmpdir._read_records(session_id)

    assert len(records_parsed) == N_WORKERS, (
        f"Expected {N_WORKERS} records, got {len(records_parsed)}"
    )

    recovered_ids = {r.get("tracking_id") for r in records_parsed if "tracking_id" in r}
    expected_ids = set(range(N_WORKERS))
    assert recovered_ids == expected_ids, (
        f"Missing tracking IDs: {expected_ids - recovered_ids}"
    )

    for r in records_parsed:
        tid = r.get("tracking_id")
        expected_payload = f"{tid}:{LARGE_CONTENT}"
        actual_payload = r.get("payload", "")
        assert actual_payload == expected_payload, (
            f"Record {tid} payload truncated: "
            f"expected {len(expected_payload)} chars, "
            f"got {len(actual_payload)} chars"
        )


def test_concurrent_writes_different_sessions_dont_interfere(backlog_tmpdir):
    """Concurrent writes to DIFFERENT session files must never block
    each other — the fix must use a per-session lock, NOT a global lock.
    """
    N_SESSIONS = 4
    N_RECORDS_PER_SESSION = 4
    LARGE_CONTENT = "y" * 8_000

    def _write_for_session(session_id: str, record_id: int):
        record = {
            "type": "llm_call",
            "session_id": session_id,
            "record_id": record_id,
            "payload": f"{session_id}:{record_id}:{LARGE_CONTENT}",
        }
        backlog_tmpdir._write(session_id, record)

    with ThreadPoolExecutor(max_workers=N_SESSIONS * 2) as executor:
        futures = []
        for sid in range(N_SESSIONS):
            sname = f"session-{sid}"
            for rid in range(N_RECORDS_PER_SESSION):
                futures.append(executor.submit(_write_for_session, sname, rid))
        for f in as_completed(futures):
            exc = f.exception()
            if exc:
                raise AssertionError(f"Worker failed: {exc}") from exc

    # Verify each session independently
    for sid in range(N_SESSIONS):
        sname = f"session-{sid}"
        records = backlog_tmpdir._read_records(sname)
        assert len(records) == N_RECORDS_PER_SESSION, (
            f"Session {sname}: expected {N_RECORDS_PER_SESSION} records, "
            f"got {len(records)}"
        )
        recovered = {r.get("record_id") for r in records if "record_id" in r}
        assert recovered == set(range(N_RECORDS_PER_SESSION)), (
            f"Session {sname}: missing record IDs"
        )
