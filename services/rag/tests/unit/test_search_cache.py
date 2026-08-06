"""Tests for cache layer — TDD approach.

Run: uv run pytest rag/tests/unit/test_search_cache.py -v

First, tests define the contract. Run them — they should pass with LocalTTLCache.
Then the same tests should pass with a RedisCache adapter.
"""

from __future__ import annotations

import time

import pytest

from rag.cache.protocol import CacheProtocol
from rag.cache.local import LocalTTLCache


# ── Fixtures ──


@pytest.fixture
def cache() -> CacheProtocol:
    """Default local TTL cache for testing."""
    return LocalTTLCache(maxsize=64, ttl=10)


SAMPLE_RESULTS = [
    {"chunk_id": "1", "content": "test content", "score": 0.95},
    {"chunk_id": "2", "content": "another chunk", "score": 0.85},
]


class TestCacheProtocol:
    """Tests that every CacheProtocol implementation must pass."""

    def test_instantiation(self, cache: CacheProtocol):
        """CacheProtocol can be instantiated."""
        assert cache is not None

    def test_get_miss_returns_none(self, cache: CacheProtocol):
        """get_cached_search() returns None on cache miss."""
        result = cache.get_cached_search("nonexistent", None, 5)
        assert result is None

    def test_set_and_get(self, cache: CacheProtocol):
        """After set_cached_search(), get returns the same data."""
        cache.set_cached_search("hello world", None, SAMPLE_RESULTS)
        result = cache.get_cached_search("hello world", None, 5)
        assert result == SAMPLE_RESULTS
        assert len(result) == 2

    def test_discipline_id_isolation(self, cache: CacheProtocol):
        """Same query, different discipline_id → different cache entries."""
        cache.set_cached_search("math", "discipline-a", [{"id": "a"}])
        cache.set_cached_search("math", "discipline-b", [{"id": "b"}])

        result_a = cache.get_cached_search("math", "discipline-a", 5)
        result_b = cache.get_cached_search("math", "discipline-b", 5)

        assert result_a == [{"id": "a"}]
        assert result_b == [{"id": "b"}]

    def test_cache_miss_after_clear(self, cache: CacheProtocol):
        """After clear(), all cache entries are gone."""
        cache.set_cached_search("test", None, SAMPLE_RESULTS)
        cache.clear()
        result = cache.get_cached_search("test", None, 5)
        assert result is None

    def test_cache_miss_after_invalidate(self, cache: CacheProtocol):
        """invalidate(query) removes entries matching that query prefix."""
        cache.set_cached_search("alpha", None, [{"id": "a"}])
        cache.set_cached_search("beta", None, [{"id": "b"}])

        cache.invalidate("alpha")

        assert cache.get_cached_search("alpha", None, 5) is None
        # beta should still be there
        assert cache.get_cached_search("beta", None, 5) == [{"id": "b"}]

    def test_invalidate_all_with_none(self, cache: CacheProtocol):
        """invalidate(None) clears everything."""
        cache.set_cached_search("a", None, [{"id": "a"}])
        cache.set_cached_search("b", None, [{"id": "b"}])

        cache.invalidate(None)

        assert cache.get_cached_search("a", None, 5) is None
        assert cache.get_cached_search("b", None, 5) is None

    def test_ttl_expiry(self, cache: CacheProtocol):
        """After TTL seconds, cached entry should be gone."""
        # Use cache with 1s TTL
        fast_cache = LocalTTLCache(maxsize=16, ttl=1)
        fast_cache.set_cached_search("expire_me", None, SAMPLE_RESULTS)

        # Should be there immediately
        assert fast_cache.get_cached_search("expire_me", None, 5) is not None

        # Wait for expiry
        time.sleep(1.1)

        # Should be gone
        assert fast_cache.get_cached_search("expire_me", None, 5) is None

    def test_maxsize_eviction(self):
        """When maxsize is exceeded, oldest entries are evicted."""
        tiny_cache = LocalTTLCache(maxsize=2, ttl=60)

        tiny_cache.set_cached_search("key1", None, [{"id": "1"}])
        tiny_cache.set_cached_search("key2", None, [{"id": "2"}])
        # key1 might be evicted now or key2, depending on insertion order
        tiny_cache.set_cached_search("key3", None, [{"id": "3"}])

        # At least one should be evicted
        hits = sum(
            1
            for k in ["key1", "key2", "key3"]
            if tiny_cache.get_cached_search(k, None, 5) is not None
        )
        assert hits <= 2, "TTLCache should evict old entries"

    def test_set_twice_overwrites(self, cache: CacheProtocol):
        """Setting the same key twice overwrites the old value."""
        cache.set_cached_search("dup", None, [{"id": "first"}])
        cache.set_cached_search("dup", None, [{"id": "second"}])

        result = cache.get_cached_search("dup", None, 5)
        assert result == [{"id": "second"}]
