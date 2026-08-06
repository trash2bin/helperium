"""Tests for ModelBacklog — trace logging of model interactions."""

import time
import tempfile
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


# --- Writing ---


def test_turn_start(backlog_tmpdir):
    """turn_start writes a record and returns turn_id."""
    turn_id = backlog_tmpdir.turn_start("session-1", "Hello, agent!")
    assert len(turn_id) == 12  # hex[:12]

    session_path = backlog_tmpdir._path("session-1")
    assert session_path.exists()

    content = session_path.read_text(encoding="utf-8")
    assert "Hello, agent!" in content
    assert "turn_start" in content


def test_tool_call_and_result(backlog_tmpdir):
    """tool_call + tool_result writes both with duration."""
    backlog_tmpdir.tool_call(
        "session-1",
        "abc123",
        0,
        name="get_student",
        arguments={"student_id": "123"},
    )
    backlog_tmpdir.tool_result(
        "session-1",
        "abc123",
        0,
        name="get_student",
        result='{"name": "Ivan"}',
        duration_ms=42.0,
    )

    records = backlog_tmpdir._read_records("session-1")
    assert records[0]["event"] == "tool_call"
    assert records[0]["data"]["name"] == "get_student"
    assert records[1]["event"] == "tool_result"
    assert records[1]["duration_ms"] == 42.0


def test_error_event(backlog_tmpdir):
    """error writes error message and context."""
    backlog_tmpdir.error(
        "session-1",
        "abc123",
        0,
        error="Something went wrong",
        context={"module": "orchestrator"},
    )

    records = backlog_tmpdir._read_records("session-1")
    assert records[0]["event"] == "error"
    assert records[0]["data"]["error"] == "Something went wrong"


# --- Reading ---


def test_list_sessions(backlog_tmpdir):
    """list_sessions returns all sessions sorted by time desc."""
    backlog_tmpdir.turn_start("session-a", "Hello A")
    time.sleep(0.01)
    backlog_tmpdir.turn_start("session-b", "Hello B")

    sessions = backlog_tmpdir.list_sessions()
    assert len(sessions) == 2

    # Sorted: newest first (B then A)
    assert sessions[0]["session_id"] == "session-b"
    assert sessions[1]["session_id"] == "session-a"
    assert sessions[0]["num_events"] == 1
    assert sessions[1]["num_events"] == 1


def test_read_session(backlog_tmpdir):
    """read_session returns records with offset/limit."""
    for i in range(10):
        backlog_tmpdir.turn_start("session-1", f"Message {i}")

    all_records = backlog_tmpdir.read_session("session-1")
    assert len(all_records) == 10

    # Offset + limit
    subset = backlog_tmpdir.read_session("session-1", limit=3, offset=2)
    assert len(subset) == 3


def test_read_session_nonexistent(backlog_tmpdir):
    """read_session for missing session returns []."""
    records = backlog_tmpdir.read_session("nonexistent")
    assert records == []


def test_list_sessions_empty(backlog_tmpdir):
    """list_sessions with no data returns []."""
    assert backlog_tmpdir.list_sessions() == []


# --- Serialization ---


def test_backlog_handles_serialization_error(backlog_tmpdir):
    """_write handles non-serializable data via default=str."""

    class Unserializable:
        pass

    # _write uses json.dumps with default=str, so it stringifies the object
    backlog_tmpdir._write("session-1", {"bad": Unserializable()})

    records = backlog_tmpdir._read_records("session-1")
    assert len(records) == 1
    assert "bad" in records[0]
    assert "Unserializable object" in records[0]["bad"]


# --- Cross-session isolation ---


def test_sessions_are_isolated(backlog_tmpdir):
    """Writes to different sessions don't mix."""
    backlog_tmpdir.turn_start("session-x", "X")
    backlog_tmpdir.turn_start("session-y", "Y")

    assert len(backlog_tmpdir.read_session("session-x")) == 1
    assert len(backlog_tmpdir.read_session("session-y")) == 1
    assert backlog_tmpdir.read_session("session-x")[0]["data"]["user_message"] == "X"


# --- Backlog mode ---


def test_backlog_mode_off(backlog_tmpdir):
    """backlog_mode='off' writes nothing."""
    with patch("api_service.backlog.settings.backlog_mode", "off"):
        bl = ModelBacklog()
        bl.turn_start("test", "hello")
        records = bl._read_records("test")
        assert len(records) == 0


def test_backlog_mode_errors_skips_non_errors(backlog_tmpdir):
    """backlog_mode='errors' writes only error/tool_error events."""
    with patch("api_service.backlog.settings.backlog_mode", "errors"):
        bl = ModelBacklog()
        bl.turn_start("test", "hello")
        bl.tool_result("test", "t1", 0, "foo", "ok", 1.0)
        records = bl._read_records("test")
        assert len(records) == 0

        # Now write an error
        bl.error("test", "t1", 0, "something broke")
        records = bl._read_records("test")
        assert len(records) == 1
        assert records[0]["event"] == "error"


def test_backlog_mode_errors_keeps_error_events(backlog_tmpdir):
    """backlog_mode='errors' keeps records with event='error' or type=RECORD_ERROR."""
    with patch("api_service.backlog.settings.backlog_mode", "errors"):
        bl = ModelBacklog()
        # error() creates records with event="error" → should pass
        bl.error("test", "t1", 0, "something broke")
        records = bl._read_records("test")
        assert len(records) == 1

        # llm_call with status=error has type=RECORD_LLM_CALL, not "error"
        # and event is not set — should NOT pass the filter
        bl.record_llm_call("test", model="gpt", provider="openai", status="error")
        records = bl._read_records("test")
        assert len(records) == 1  # still 1


def test_backlog_mode_full_default(backlog_tmpdir):
    """Default backlog_mode='full' writes everything."""
    # backlog_tmpdir already runs with default settings
    backlog_tmpdir.turn_start("test", "hello")
    backlog_tmpdir.tool_result("test", "t1", 0, "foo", "some result", 1.0)
    backlog_tmpdir.error("test", "t1", 0, "err")
    records = backlog_tmpdir._read_records("test")
    assert len(records) == 3


def test_backlog_setting_default_is_full():
    """The settings.backlog_mode defaults to 'full'."""
    from helperium_sdk.settings import settings

    assert settings.backlog_mode == "full"


def test_tool_result_stores_full_content_in_backlog():
    """backlog.tool_result stores full content (truncation is in tool_handler)."""
    with tempfile.TemporaryDirectory() as td:
        with patch("api_service.backlog.settings.backlog_dir", td):
            with patch("api_service.backlog.settings.backlog_mode", "full"):
                bl = ModelBacklog()
                long_result = "x" * 15_000
                bl.tool_result("session-1", "t1", 0, "test_tool", long_result, 1.0)
                records = bl._read_records("session-1")
                assert len(records) == 1
                stored = records[0]["data"]["result"]
                # backlog stores full content; truncation is in tool_handler.py
                assert len(stored) == 15_000
                assert records[0]["data"]["result_chars"] == 15_000
