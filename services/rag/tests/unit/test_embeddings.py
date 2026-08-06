import pytest
from unittest.mock import MagicMock, patch
import numpy as np

from rag.config import RagConfig
from rag.embeddings import SentenceTransformerEmbedding


def test_embedding_lazy_loading():
    """Проверка, что модель не грузится до первого вызова encode_batched."""
    config = RagConfig()
    service = SentenceTransformerEmbedding(config)

    assert service._model is None

    with patch("sentence_transformers.SentenceTransformer") as mock_st:
        mock_instance = mock_st.return_value
        mock_instance.encode.return_value = [np.random.rand(384).tolist()]

        # Первый вызов должен инициализировать модель
        service.encode_batched(["test text"])
        assert service._model is not None
        mock_st.assert_called_once()


def test_encode_batched_logic():
    """Проверка корректности батчинга при векторизации."""
    config = RagConfig(embedding_batch_size=2)
    service = SentenceTransformerEmbedding(config)

    # Мокаем модель
    mock_model = MagicMock()
    # Возвращаем список векторов той же длины, что и вход
    mock_model.encode.side_effect = lambda texts, **kwargs: [
        np.random.rand(384).tolist() for _ in texts
    ]
    service._model = mock_model

    texts = ["текст 1", "текст 2", "текст 3", "текст 4", "текст 5"]
    embeddings = service.encode_batched(texts)

    assert len(embeddings) == 5
    # При batch_size=2, для 5 текстов должно быть 3 вызова (2, 2, 1)
    assert mock_model.encode.call_count == 3


def test_encode_empty_list():
    """Проверка обработки пустого списка текстов."""
    config = RagConfig()
    service = SentenceTransformerEmbedding(config)
    assert service.encode_batched([]) == []


def test_model_load_failure():
    """Проверка обработки ошибки при загрузке модели."""
    config = RagConfig()
    service = SentenceTransformerEmbedding(config)

    with patch(
        "sentence_transformers.SentenceTransformer",
        side_effect=Exception("Download error"),
    ):
        with pytest.raises(RuntimeError, match="Failed to load embedding model"):
            service.encode_batched(["test"])
