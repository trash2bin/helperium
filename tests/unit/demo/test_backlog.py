"""Tests for ModelBacklog — trace logging of model interactions."""

import json
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from demo.api.backlog import ModelBacklog


@pytest.fixture
def backlog_tmpdir():
    """Create a ModelBacklog with a temp directory."""
    with tempfile.TemporaryDirectory() as td:
        with patch("demo.api.backlog.settings.backlog_dir", td):
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


def test_model_request(backlog_tmpdir):
    """model_request writes messages and tools info."""
    backlog_tmpdir.model_request(
        "session-1", "abc123", 0,
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "get_student"}],
    )
    
    records = backlog_tmpdir._read_records("session-1")
    assert len(records) == 1
    assert records[0]["event"] == "model_request"
    assert records[0]["data"]["num_messages"] == 1
    assert records[0]["data"]["num_tools"] == 1


def test_model_response(backlog_tmpdir):
    """model_response writes content, tool_calls, reasoning, finish_reason."""
    backlog_tmpdir.model_response(
        "session-1", "abc123", 0,
        response={"content": "Hello!", "finish_reason": "stop"},
        duration_ms=150.5,
        token_usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    
    records = backlog_tmpdir._read_records("session-1")
    assert records[0]["data"]["content"] == "Hello!"
    assert records[0]["data"]["finish_reason"] == "stop"
    assert records[0]["duration_ms"] == 150.5
    assert records[0]["tokens"]["prompt_tokens"] == 10


def test_stream_lifecycle(backlog_tmpdir):
    """stream_start + stream_end writes both records."""
    backlog_tmpdir.stream_start(
        "session-1", "abc123", 0,
        messages=[{"role": "user", "content": "hi"}],
    )
    backlog_tmpdir.stream_end(
        "session-1", "abc123", 0,
        full_text="Hello there!",
        duration_ms=200.0,
    )
    
    records = backlog_tmpdir._read_records("session-1")
    assert len(records) == 2
    assert records[0]["event"] == "stream_start"
    assert records[1]["event"] == "stream_end"
    assert records[1]["data"]["chars"] == 12  # len("Hello there!")


def test_tool_call_and_result(backlog_tmpdir):
    """tool_call + tool_result writes both with duration."""
    backlog_tmpdir.tool_call(
        "session-1", "abc123", 0,
        name="get_student",
        arguments={"student_id": "123"},
    )
    backlog_tmpdir.tool_result(
        "session-1", "abc123", 0,
        name="get_student",
        result='{"name": "Ivan"}',
        duration_ms=42.0,
    )
    
    records = backlog_tmpdir._read_records("session-1")
    assert records[0]["event"] == "tool_call"
    assert records[0]["data"]["name"] == "get_student"
    assert records[1]["event"] == "tool_result"
    assert records[1]["duration_ms"] == 42.0


def test_empty_round(backlog_tmpdir):
    """empty_round writes reasoning and messages."""
    backlog_tmpdir.empty_round(
        "session-1", "abc123", 0,
        reasoning_content="Thinking...",
        messages=[{"role": "user", "content": "hi"}],
    )
    
    records = backlog_tmpdir._read_records("session-1")
    assert records[0]["event"] == "empty_round"
    assert records[0]["data"]["reasoning_content"] == "Thinking..."


def test_error_event(backlog_tmpdir):
    """error writes error message and context."""
    backlog_tmpdir.error(
        "session-1", "abc123", 0,
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
