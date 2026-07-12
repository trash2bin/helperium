import pytest

from rag.config import RagConfig
from rag.interfaces import EmbeddingProtocol
from rag.vector_store import ChromaDBVectorStore
from helperium_sdk.rag.models import RagSearchResult


class MockEmbeddingService(EmbeddingProtocol):
    """Простой мок эмбеддингов, возвращающий фиксированные векторы."""

    def encode_batched(self, texts: list[str], mode: str = "passage"):
        # Генерируем простой вектор на основе длины строки, чтобы поиск был хоть как-то разным
        return [[float(len(t))] * 384 for t in texts]


@pytest.fixture
def rag_config(tmp_path):
    return RagConfig(
        chroma_path=str(tmp_path / "chroma_db"),
        chroma_collection="test_collection",
        embedding_batch_size=10,
    )


@pytest.fixture
def vector_store(rag_config):
    embedding_service = MockEmbeddingService()
    return ChromaDBVectorStore(rag_config, embedding_service)


def test_add_and_search_chunks(vector_store):
    """Проверка добавления чанков и последующего поиска по ним."""
    chunk_ids = ["c1", "c2"]
    chunk_texts = ["Первый текст про котиков", "Второй текст про собак"]
    chunk_metadatas = [{"page": 1, "chunk_index": 0}, {"page": 2, "chunk_index": 0}]

    vector_store.add_chunks(
        chunk_ids=chunk_ids,
        chunk_texts=chunk_texts,
        chunk_metadatas=chunk_metadatas,
        document_id="doc1",
        document_title="Животные",
        source_path="animals.txt",
        discipline_id="bio101",
    )

    # Ищем "котиков". В нашем MockEmbedding вектор зависит от длины.
    # Для простоты просто проверяем, что результаты возвращаются и имеют правильный формат.
    results = vector_store.search("котиков", discipline_id="bio101", limit=1)

    assert len(results) == 1
    assert isinstance(results[0], RagSearchResult)
    assert results[0].document_id == "doc1"
    assert results[0].document_title == "Животные"
    assert results[0].discipline_id == "bio101"
    assert results[0].chunk_id == "c1" or results[0].chunk_id == "c2"


def test_search_with_filter(vector_store):
    """Проверка фильтрации по discipline_id."""
    vector_store.add_chunks(
        ["c1"], ["Текст А"], [{}], "doc1", "Title A", "pathA", "discA"
    )
    vector_store.add_chunks(
        ["c2"], ["Текст Б"], [{}], "doc2", "Title B", "pathB", "discB"
    )

    # Поиск только по discB
    results = vector_store.search("запрос", discipline_id="discB")
    assert len(results) == 1
    assert results[0].discipline_id == "discB"


def test_delete_by_document_id(vector_store):
    """Проверка удаления всех чанков конкретного документа."""
    vector_store.add_chunks(
        ["c1", "c2"], ["T1", "T2"], [{}, {}], "doc1", "Title 1", "p1", "d1"
    )
    vector_store.add_chunks(["c3"], ["T3"], [{}], "doc2", "Title 2", "p2", "d1")

    # Удаляем doc1
    vector_store.delete_by_document_id("doc1")

    # Должен остаться только doc2
    results = vector_store.search("запрос", discipline_id="d1")
    assert len(results) == 1
    assert results[0].document_id == "doc2"


def test_delete_by_ids(vector_store):
    """Проверка удаления конкретных чанков по их ID."""
    vector_store.add_chunks(
        ["c1", "c2"], ["T1", "T2"], [{}, {}], "doc1", "Title 1", "p1", "d1"
    )

    vector_store.delete_by_ids(["c1"])

    results = vector_store.search("запрос", discipline_id="d1")
    assert len(results) == 1
    assert results[0].chunk_id == "c2"
