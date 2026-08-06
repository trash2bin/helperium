import sqlite3
import pytest
from rag.config import RagConfig
from rag.repository import DocumentRepository
from helperium_sdk.rag.models import Document


@pytest.fixture
def db_conn():
    """Создает соединение с SQLite в памяти и разворачивает схему."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Создаем схему, необходимую для DocumentRepository
    conn.execute("""
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            source_path TEXT UNIQUE,
            mime_type TEXT,
            discipline_id TEXT,
            discipline_name TEXT,
            created_at TEXT,
            metadata_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            chunk_index INTEGER,
            page INTEGER,
            content TEXT,
            embedding_json TEXT,
            token_count INTEGER,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn):
    config = RagConfig()
    return DocumentRepository(db_conn, config)


def test_save_document_without_vector_store(repo):
    """Тест сохранения документа и чанков только в SQLite."""
    chunks = [
        {"content": "Первый чанк", "page": 1},
        {"content": "Второй чанк", "page": 1},
    ]

    result = repo.save_document_with_chunks(
        source_path="test.txt",
        chunks=chunks,
        discipline_id="disc1",
        title="Тестовый документ",
        vector_store=None,
    )

    assert isinstance(result.document, Document)
    assert result.chunks_count == 2
    assert result.document.title == "Тестовый документ"

    # Проверяем, что данные реально в БД
    doc = repo.get_document_by_id_as_model(result.document.id)
    assert doc is not None
    assert doc.source_path == "test.txt"


def test_find_existing_by_path(repo):
    """Проверка поиска документа по пути."""
    # Сохраняем
    res = repo.save_document_with_chunks("unique_path.pdf", [], "d1", "Title", None)
    doc_id = res.document.id

    # Ищем
    found_id = repo.find_existing_by_path("unique_path.pdf")
    assert found_id == doc_id

    # Несуществующий
    assert repo.find_existing_by_path("none.pdf") is None


def test_list_documents_filtering(repo):
    """Проверка листинга документов с фильтрацией по дисциплине."""
    repo.save_document_with_chunks("p1.txt", [], "disc1", "T1", None)
    repo.save_document_with_chunks("p2.txt", [], "disc1", "T2", None)
    repo.save_document_with_chunks("p3.txt", [], "disc2", "T3", None)

    # Все
    all_docs = repo.list_documents()
    assert len(all_docs) == 3

    # Только disc1
    disc1_docs = repo.list_documents(discipline_id="disc1")
    assert len(disc1_docs) == 2

    # С лимитом
    limited = repo.list_documents(limit=1)
    assert len(limited) == 1


def test_delete_document_record(repo):
    """Проверка удаления документа и его чанков."""
    res = repo.save_document_with_chunks(
        "del.txt", [{"content": "chunk", "page": 1}], "d1", "Title", None
    )
    doc_id = res.document.id

    # Проверяем, что чанки есть
    cursor = repo.conn.cursor()
    assert (
        cursor.execute(
            "SELECT id FROM document_chunks WHERE document_id=?", (doc_id,)
        ).fetchone()
        is not None
    )

    # Удаляем
    repo.delete_document_record(doc_id)

    # Проверяем, что всё удалено
    assert repo.get_document_by_id(doc_id) is None
    assert (
        cursor.execute(
            "SELECT id FROM document_chunks WHERE document_id=?", (doc_id,)
        ).fetchone()
        is None
    )


def test_transaction_rollback_on_vector_store_error(repo):
    """Критический тест: откат SQLite, если ChromaDB упала при импорте."""
    from unittest.mock import MagicMock

    mock_vs = MagicMock()
    # Мокаем ошибку при добавлении чанков в Chroma
    mock_vs.add_chunks.side_effect = Exception("ChromaDB Connection Error")

    chunks = [{"content": "text", "page": 1}]
    source_path = "error_doc.txt"

    with pytest.raises(Exception, match="ChromaDB Connection Error"):
        repo.save_document_with_chunks(
            source_path=source_path,
            chunks=chunks,
            discipline_id="d1",
            title="Title",
            vector_store=mock_vs,
        )

    # Проверяем, что в SQLite документ НЕ сохранился (откат транзакции)
    assert repo.find_existing_by_path(source_path) is None
