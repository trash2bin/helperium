"""Tests for SessionStore — persistent chat session history."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from demo.api.sessions import SessionStore


@pytest.fixture
def session_db_path():
    """Temp path for session SQLite DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def connection_factory(session_db_path):
    """Factory creating isolated connections to the same session DB."""
    def _factory():
        conn = sqlite3.connect(str(session_db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    return _factory


@pytest.fixture
def store(connection_factory):
    """SessionStore with temp SQLite DB."""
    return SessionStore(
        connection_factory=connection_factory,
        max_turns=5,
        max_content_chars=200,
    )


# --- Basic CRUD ---


def test_append_and_get_turns(store):
    """append_turn + get_turns round-trip."""
    turn = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    store.append_turn("session-1", turn)

    turns = store.get_turns("session-1")
    assert len(turns) == 1
    assert turns[0][0]["role"] == "user"
    assert turns[0][0]["content"] == "Hello"
    assert turns[0][1]["role"] == "assistant"


def test_history_messages(store):
    """history_messages returns flattened, compacted messages."""
    store.append_turn("session-1", [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ])
    store.append_turn("session-1", [
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm fine!"},
    ])

    messages = store.history_messages("session-1")
    assert len(messages) == 4  # 2 turns × 2 messages
    assert messages[0]["content"] == "Hello"
    assert messages[3]["content"] == "I'm fine!"


def test_append_multiple_turns(store):
    """Multiple turns are stored and returned in order."""
    for i in range(3):
        store.append_turn("session-1", [
            {"role": "user", "content": f"Message {i}"},
        ])

    turns = store.get_turns("session-1")
    assert len(turns) == 3
    assert turns[0][0]["content"] == "Message 0"
    assert turns[2][0]["content"] == "Message 2"


# --- Trimming ---


def test_trim_exceeds_max_turns(store):
    """Session longer than max_turns gets trimmed to max_turns most recent."""
    for i in range(10):  # max_turns=5
        store.append_turn("session-1", [
            {"role": "user", "content": f"Turn {i}"},
        ])

    turns = store.get_turns("session-1")
    assert len(turns) == 5  # trimmed to max_turns

    # Only the last 5 turns remain
    contents = [t[0]["content"] for t in turns]
    assert "Turn 0" not in contents
    assert "Turn 5" in contents
    assert "Turn 9" in contents


def test_trim_to_one_turn(store):
    """Store with max_turns=1 only keeps the last turn."""
    tiny_store = SessionStore(
        connection_factory=store._connection_factory,
        max_turns=1,
        max_content_chars=200,
    )
    for i in range(3):
        tiny_store.append_turn("session-1", [
            {"role": "user", "content": f"Turn {i}"},
        ])

    turns = tiny_store.get_turns("session-1")
    assert len(turns) == 1
    assert turns[0][0]["content"] == "Turn 2"


# --- Content truncation ---


def test_long_content_truncated(store):
    """Content longer than max_content_chars gets truncated with indicator."""
    very_long = "A" * 500  # max_content_chars=200
    store.append_turn("session-1", [
        {"role": "user", "content": very_long},
    ])

    messages = store.history_messages("session-1")
    truncated = messages[0]["content"]
    assert len(truncated) <= 200 + len("\n\n...[обрезано в истории диалога]")
    assert "...[обрезано в истории диалога]" in truncated


# --- Filtering ---


def test_empty_assistant_turn_skipped(store):
    """Assistant message with no content and no tool_calls is filtered out."""
    store.append_turn("session-1", [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "", "tool_calls": []},
    ])
    turns = store.get_turns("session-1")
    assert len(turns) == 1  # the assistant-only turn was filtered
    assert turns[0][0]["role"] == "user"


def test_assistant_with_tool_calls_kept(store):
    """Assistant message with tool_calls but empty content is kept."""
    store.append_turn("session-1", [
        {"role": "assistant", "content": "", "tool_calls": [{"name": "search"}]},
    ])
    turns = store.get_turns("session-1")
    assert len(turns) == 1
    assert turns[0][0]["role"] == "assistant"
    assert turns[0][0]["tool_calls"] == [{"name": "search"}]


# --- Session ID ---


def test_normalize_session_id(store):
    """session_id is normalized: stripped, truncated, defaults to 'default'."""
    assert store.normalize_session_id("  abc  ") == "abc"
    assert store.normalize_session_id("") == "default"
    assert store.normalize_session_id(None) == "default"
    assert store.normalize_session_id("  ") == "default"

    long_id = "x" * 200
    assert len(store.normalize_session_id(long_id)) == 128


def test_multiple_sessions_isolated(store):
    """Different sessions have independent histories."""
    store.append_turn("session-a", [{"role": "user", "content": "A"}])
    store.append_turn("session-b", [{"role": "user", "content": "B"}])
    store.append_turn("session-a", [{"role": "user", "content": "A2"}])

    turns_a = store.get_turns("session-a")
    turns_b = store.get_turns("session-b")

    assert len(turns_a) == 2
    assert len(turns_b) == 1
    assert turns_a[0][0]["content"] == "A"
    assert turns_a[1][0]["content"] == "A2"
    assert turns_b[0][0]["content"] == "B"


# --- Reasoning content ---


def test_reasoning_content_stripped(store):
    """reasoning_content is stripped from messages in history."""
    store.append_turn("session-1", [
        {"role": "assistant", "content": "Answer", "reasoning_content": "Thinking..."},
    ])

    messages = store.history_messages("session-1")
    assert "reasoning_content" not in messages[0]


# --- Thread safety ---


def test_concurrent_writes(store):
    """Appending from multiple 'threads' doesn't corrupt data."""
    import threading

    results = []

    def writer(n):
        for i in range(5):
            store.append_turn(f"shared-session", [
                {"role": "user", "content": f"Thread {n} turn {i}"},
            ])
        results.append("done")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == "done" for r in results)
    turns = store.get_turns("shared-session")
    # At most max_turns (5) turns should be stored
    assert len(turns) <= 5
