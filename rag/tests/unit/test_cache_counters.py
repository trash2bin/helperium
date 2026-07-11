"""Tests for cache hit/miss counters in the search pipeline.

Red:   rag_cache_hits and rag_cache_misses counters should be incremented on cache
       hit/miss respectively. Currently they are NEVER called.
Green: Counter.inc() is called on hit and miss.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag.pipeline.pipeline import RAGPipeline
from rag.config import RagConfig
from agent_tutor_sdk.rag.models import RagSearchResult


@pytest.fixture
def pipeline():
    cfg = RagConfig(cache_enabled=True, cache_maxsize=64, cache_ttl=300)
    return RAGPipeline(
        config=cfg,
        parser=MagicMock(),
        chunker=MagicMock(),
        embedding_service=MagicMock(),
        repository=MagicMock(),
        vector_store=MagicMock(),
    )


@pytest.fixture
def mock_cache():
    cache = MagicMock()
    return cache


@pytest.mark.asyncio
async def test_cache_hit_counter_incremented(pipeline, mock_cache):
    """При cache hit rag_cache_hits.inc() вызывается."""
    # Настраиваем кэш: вернёт результат при любом запросе
    mock_cache.get_cached_search.return_value = [
        RagSearchResult(document_id="d1", document_title="T", source_path="p",
                        chunk_id="c1", chunk_index=0, page=1, score=0.9, content="hit")
    ]
    pipeline._cache = mock_cache

    with patch("rag.pipeline.pipeline.rag_cache_hits") as mock_hits_counter:
        result = pipeline.search_documents("hello", discipline_id="d1", limit=5)

        assert len(result) == 1
        mock_hits_counter.inc.assert_called_once()


@pytest.mark.asyncio
async def test_cache_miss_counter_incremented(pipeline, mock_cache):
    """При cache miss rag_cache_misses.inc() вызывается (после поиска)."""
    # Кэш возвращает None (промах)
    mock_cache.get_cached_search.return_value = None
    pipeline._cache = mock_cache

    # Vector store возвращает результат
    pipeline.vector_store.search.return_value = [
        RagSearchResult(document_id="d1", document_title="T", source_path="p",
                        chunk_id="c1", chunk_index=0, page=1, score=0.9, content="miss")
    ]

    with (
        patch("rag.pipeline.pipeline.rag_cache_hits") as mock_hits,
        patch("rag.pipeline.pipeline.rag_cache_misses") as mock_misses,
    ):
        result = pipeline.search_documents("hello", discipline_id="d1", limit=5)

        assert len(result) == 1
        mock_hits.inc.assert_not_called()
        mock_misses.inc.assert_called_once()


@pytest.mark.asyncio
async def test_cache_disabled_no_counter(pipeline, mock_cache):
    """Когда кэш выключен, счётчики cache не вызываются."""
    pipeline._cache = None
    pipeline.vector_store.search.return_value = [
        RagSearchResult(document_id="d1", document_title="T", source_path="p",
                        chunk_id="c1", chunk_index=0, page=1, score=0.9, content="data")
    ]

    with (
        patch("rag.pipeline.pipeline.rag_cache_hits") as mock_hits,
        patch("rag.pipeline.pipeline.rag_cache_misses") as mock_misses,
    ):
        result = pipeline.search_documents("hello", discipline_id="d1", limit=5)

        assert len(result) == 1
        mock_hits.inc.assert_not_called()
        mock_misses.inc.assert_not_called()
