"""Tests for LiteLLMEmbedding provider.

TDD approach: tests define the contract. Run with mock to avoid real API calls.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock


from rag.config import RagConfig
from rag.embedding.litellm_provider import LiteLLMEmbedding


# litellm.embedding is imported inside encode_batched(), so
# we need to patch litellm.embedding (module-level), not the local ref
PATCH_PATH = "litellm.embedding"


class TestLiteLLMEmbeddingInit:
    """LiteLLMEmbedding should initialize without errors."""

    def test_instantiation(self):
        """Can create LiteLLMEmbedding with a config."""
        config = RagConfig(
            embedding_provider="litellm",
            embedding_model="text-embedding-3-small",
        )
        emb = LiteLLMEmbedding(config)
        assert emb.config.embedding_model == "text-embedding-3-small"
        assert emb.config.embedding_provider == "litellm"

    def test_empty_texts_returns_empty(self):
        """encode_batched([]) returns []."""
        config = RagConfig(embedding_provider="litellm")
        emb = LiteLLMEmbedding(config)
        assert emb.encode_batched([]) == []


class TestLiteLLMEmbeddingAPI:
    """Test encode_batched with mocked litellm.embedding()."""

    @patch(PATCH_PATH)
    def test_encode_single_text(self, mock_embedding):
        """encode_batched sends correct args to litellm.embedding()."""
        # Setup mock
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1, 0.2, 0.3]}]
        mock_embedding.return_value = mock_response

        config = RagConfig(
            embedding_provider="litellm",
            embedding_model="text-embedding-3-small",
            embedding_dimensions=3,
        )
        emb = LiteLLMEmbedding(config)

        result = emb.encode_batched(["hello world"], mode="query")

        # Verify result shape
        assert len(result) == 1
        assert len(result[0]) == 3
        assert result[0] == [0.1, 0.2, 0.3]

        # Verify litellm was called with correct args
        mock_embedding.assert_called_once()
        call_kwargs = mock_embedding.call_args[1]
        assert call_kwargs["model"] == "text-embedding-3-small"
        assert call_kwargs["input"] == ["hello world"]
        assert call_kwargs["dimensions"] == 3

    @patch(PATCH_PATH)
    def test_encode_batch(self, mock_embedding):
        """encode_batched with multiple texts returns multiple embeddings."""
        mock_response = MagicMock()
        mock_response.data = [
            {"embedding": [0.1, 0.0, 0.0]},
            {"embedding": [0.0, 0.2, 0.0]},
        ]
        mock_embedding.return_value = mock_response

        config = RagConfig(embedding_provider="litellm", embedding_dimensions=3)
        emb = LiteLLMEmbedding(config)

        result = emb.encode_batched(["text a", "text b"], mode="passage")

        assert len(result) == 2
        assert result[0][0] == 0.1
        assert result[1][1] == 0.2

    @patch(PATCH_PATH)
    def test_query_prefix(self, mock_embedding):
        """e5-style prefixes should be prepended."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.5, 0.5]}]
        mock_embedding.return_value = mock_response

        config = RagConfig(
            embedding_provider="litellm",
            embedding_query_prefix="query: ",
            embedding_passage_prefix="passage: ",
            embedding_dimensions=2,
        )
        emb = LiteLLMEmbedding(config)

        # Query mode should add "query: " prefix
        emb.encode_batched(["find me something"], mode="query")
        assert mock_embedding.call_args[1]["input"] == ["query: find me something"]

        # Passage mode should add "passage: " prefix
        emb.encode_batched(["some document text"], mode="passage")
        assert mock_embedding.call_args[1]["input"] == ["passage: some document text"]

    @patch(PATCH_PATH)
    def test_api_key_passed_to_litellm(self, mock_embedding):
        """embedding_api_key should be forwarded to litellm."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1]}]
        mock_embedding.return_value = mock_response

        config = RagConfig(
            embedding_provider="litellm",
            embedding_model="text-embedding-3-small",
            embedding_api_key="sk-test-key-12345",
            embedding_dimensions=1,
        )
        emb = LiteLLMEmbedding(config)
        emb.encode_batched(["test"], mode="query")

        assert mock_embedding.call_args[1]["api_key"] == "sk-test-key-12345"

    @patch(PATCH_PATH)
    def test_api_base_passed_to_litellm(self, mock_embedding):
        """embedding_api_base should be forwarded as api_base."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1]}]
        mock_embedding.return_value = mock_response

        config = RagConfig(
            embedding_provider="litellm",
            embedding_model="text-embedding-3-small",
            embedding_api_base="https://api.polza.ai/v1",
            embedding_dimensions=1,
        )
        emb = LiteLLMEmbedding(config)
        emb.encode_batched(["test"], mode="query")

        assert mock_embedding.call_args[1]["api_base"] == "https://api.polza.ai/v1"

    @patch(PATCH_PATH)
    def test_skip_dimensions_when_zero(self, mock_embedding):
        """If dimensions is 0 or unset, should not pass dimensions param."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1]}]
        mock_embedding.return_value = mock_response

        config = RagConfig(
            embedding_provider="litellm",
            embedding_dimensions=0,
        )
        emb = LiteLLMEmbedding(config)
        emb.encode_batched(["test"], mode="query")

        assert "dimensions" not in mock_embedding.call_args[1]
