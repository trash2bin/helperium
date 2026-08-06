"""Tests for ConversationManager session lock cleanup and LRU eviction."""

from __future__ import annotations

import pytest

from api_service.agent.conversation import ConversationManager


@pytest.mark.asyncio
async def test_session_lock_cleanup():
    """Get a lock, clean it up, verify it's removed from _session_locks."""
    mgr = ConversationManager()
    session_id = "test-session-1"

    # Get a lock — creates it
    lock1 = await mgr.get_session_lock(session_id)
    assert lock1 is not None
    assert await mgr._session_locks.has_key(session_id)

    # Clean up
    await mgr.cleanup_session_lock(session_id)
    assert not await mgr._session_locks.has_key(session_id)

    # Getting the lock again creates a fresh one
    lock2 = await mgr.get_session_lock(session_id)
    assert lock2 is not None
    assert lock2 is not lock1  # fresh lock


@pytest.mark.asyncio
async def test_session_lock_max_eviction():
    """Create more locks than max, verify oldest evicted."""
    mgr = ConversationManager(max_session_locks=3)

    # Create 3 locks → fills the cache
    await mgr.get_session_lock("s1")
    await mgr.get_session_lock("s2")
    await mgr.get_session_lock("s3")

    assert await mgr._session_locks.has_key("s1")
    assert await mgr._session_locks.has_key("s2")
    assert await mgr._session_locks.has_key("s3")
    assert await mgr._session_locks.size() == 3

    # Create 4th lock — evicts oldest (s1)
    await mgr.get_session_lock("s4")

    assert await mgr._session_locks.size() == 3
    assert not await mgr._session_locks.has_key("s1")  # evicted
    assert await mgr._session_locks.has_key("s2")
    assert await mgr._session_locks.has_key("s3")
    assert await mgr._session_locks.has_key("s4")


@pytest.mark.asyncio
async def test_session_lock_reuse_prevents_eviction():
    """Reuse a lock, verify it's not evicted (MRU promotion)."""
    mgr = ConversationManager(max_session_locks=3)

    # Create 3 locks: order s1, s2, s3
    s1 = await mgr.get_session_lock("s1")
    await mgr.get_session_lock("s2")
    await mgr.get_session_lock("s3")

    # Reuse s1 — moves it to "most recently used"
    s1_again = await mgr.get_session_lock("s1")
    assert s1_again is s1  # same lock object

    # Now access order should be: s2, s3, s1
    # Adding s4 should evict s2 (oldest), NOT s1
    await mgr.get_session_lock("s4")

    assert await mgr._session_locks.size() == 3
    assert not await mgr._session_locks.has_key("s2")  # evicted
    assert await mgr._session_locks.has_key("s1")  # preserved
    assert await mgr._session_locks.has_key("s3")
    assert await mgr._session_locks.has_key("s4")


@pytest.mark.asyncio
async def test_session_lock_cleanup_idempotent():
    """Cleaning up a non-existent lock should not raise."""
    mgr = ConversationManager()

    # Cleanup on non-existent session — no error
    await mgr.cleanup_session_lock("non-existent")
    await mgr.cleanup_session_lock("")

    # Still works after cleanup
    lock = await mgr.get_session_lock("real-session")
    assert lock is not None
    await mgr.cleanup_session_lock("real-session")
    assert not await mgr._session_locks.has_key("real-session")
