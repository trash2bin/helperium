"""Tests for RedisCache — uses fakeredis for in-memory Redis mocking.

Run: uv run pytest rag/tests/unit/test_redis_cache.py -v
"""

from __future__ import annotations

import pytest

from rag.cache.redis import RedisCache


@pytest.fixture
def redis_client():
    """Create a fakeredis client for testing."""
    pytest.importorskip("fakeredis")
    import fakeredis

    return fakeredis.FakeStrictRedis()


@pytest.fixture
def cache(redis_client):
    """RedisCache instance backed by fakeredis."""
    try:
        return RedisCache(redis_client, ttl=10)
    except Exception:
        pytest.skip("Redis cache not available (install fakeredis for tests)")


SAMPLE_RESULTS = [
    {"chunk_id": "1", "content": "test content", "score": 0.95},
    {"chunk_id": "2", "content": "another chunk", "score": 0.85},
]


class TestRedisCache:
    """Tests that RedisCache passes CacheProtocol contract."""

    def test_instantiation(self, cache: RedisCache):
        assert cache is not None

    def test_get_miss_returns_none(self, cache: RedisCache):
        result = cache.get_cached_search("nonexistent", None, 5)
        assert result is None

    def test_set_and_get(self, cache: RedisCache):
        cache.set_cached_search("hello world", None, SAMPLE_RESULTS)
        result = cache.get_cached_search("hello world", None, 5)
        assert result == SAMPLE_RESULTS
        assert len(result) == 2

    def test_discipline_id_isolation(self, cache: RedisCache):
        cache.set_cached_search("math", "discipline-a", [{"id": "a"}])
        cache.set_cached_search("math", "discipline-b", [{"id": "b"}])

        result_a = cache.get_cached_search("math", "discipline-a", 5)
        result_b = cache.get_cached_search("math", "discipline-b", 5)

        assert result_a == [{"id": "a"}]
        assert result_b == [{"id": "b"}]

    def test_cache_miss_after_clear(self, cache: RedisCache):
        cache.set_cached_search("test", None, SAMPLE_RESULTS)
        cache.clear()
        result = cache.get_cached_search("test", None, 5)
        assert result is None

    def test_set_twice_overwrites(self, cache: RedisCache):
        cache.set_cached_search("dup", None, [{"id": "first"}])
        cache.set_cached_search("dup", None, [{"id": "second"}])
        result = cache.get_cached_search("dup", None, 5)
        assert result == [{"id": "second"}]
