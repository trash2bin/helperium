import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock, patch

from rag.service import app, state
from helperium_sdk.rag.models import Document, RagSearchResult


@pytest.fixture(autouse=True)
async def mock_state():
    """Мокаем состояние сервиса перед каждым тестом."""
    with (
        patch.object(state, "get_pipeline") as mock_pipe,
        patch.object(state, "get_db") as mock_db,
    ):
        # Создаем мок пайплайна
        pipeline = MagicMock()
        # Мокаем базовый конфиг, который может понадобиться в /health
        pipeline.config.embedding_model = "test-model"
        mock_pipe.return_value = pipeline

        # Мокаем БД
        db = MagicMock()
        mock_db.return_value = db

        yield pipeline, db


@pytest.mark.asyncio
async def test_health_ok(mock_state):
    """Проверка /health в состоянии OK."""
    pipeline, db = mock_state

    # Настраиваем успешные ответы
    db.ping.return_value = None
    pipeline.list_documents.return_value = []

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"]["status"] == "ok"
    assert data["chroma"]["status"] == "ok"
    assert data["embedding"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_health_degraded(mock_state):
    """Проверка /health, когда БД или Chroma недоступны."""
    pipeline, db = mock_state

    # БД падает
    db.ping.side_effect = Exception("DB Connection Error")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/health")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["database"]["status"] == "error"
    assert "DB Connection Error" in data["database"]["error"]


@pytest.mark.asyncio
async def test_list_documents_success(mock_state):
    """Проверка успешного получения списка документов."""
    pipeline, _ = mock_state

    # Мокаем возвращаемые документы
    mock_doc = Document(
        id="doc1",
        title="Title 1",
        source_path="p1.txt",
        mime_type="text/plain",
        discipline_id="d1",
        created_at="now",
    )
    pipeline.list_documents.return_value = [mock_doc]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Тест POST версии
        response = await ac.post(
            "/documents/list", json={"discipline_id": "d1", "limit": 10}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["documents"][0]["id"] == "doc1"


@pytest.mark.asyncio
async def test_import_document_success(mock_state):
    """Проверка успешного импорта документа."""
    pipeline, _ = mock_state

    mock_doc = Document(
        id="doc_new",
        title="New Doc",
        source_path="new.txt",
        mime_type="text/plain",
        discipline_id="d1",
        created_at="now",
    )
    # Мокаем результат импорта (DocumentImportResult)
    pipeline.import_document.return_value = MagicMock(document=mock_doc, chunks_count=5)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/documents/import",
            json={"path": "new.txt", "discipline_id": "d1", "title": "New Doc"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["document"]["id"] == "doc_new"
    assert data["chunks_count"] == 5


@pytest.mark.asyncio
async def test_import_document_not_found(mock_state):
    """Проверка обработки FileNotFoundError при импорте."""
    pipeline, _ = mock_state
    pipeline.import_document.side_effect = FileNotFoundError("File not found on disk")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/documents/import",
            json={"path": "missing.txt", "discipline_id": "d1", "title": "Title"},
        )

    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_document_success(mock_state):
    """Проверка успешного удаления документа."""
    pipeline, _ = mock_state

    # Мокаем репозиторий внутри пайплайна
    mock_repo = MagicMock()
    pipeline.repository = mock_repo

    # Мокаем поиск документа перед удалением
    mock_repo.find_document_for_delete.return_value = {
        "id": "doc_del",
        "title": "To Delete",
        "source_path": "del.txt",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/documents/delete", json={"document_id": "doc_del"})

    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == "doc_del"
    assert data["title"] == "To Delete"

    # Проверяем, что были вызваны методы удаления
    pipeline.delete_document_vectors.assert_called_once_with("doc_del")
    mock_repo.delete_document_record.assert_called_once_with("doc_del", commit=True)


@pytest.mark.asyncio
async def test_delete_document_not_found(mock_state):
    """Проверка удаления несуществующего документа (идемпотентность)."""
    pipeline, _ = mock_state
    mock_repo = MagicMock()
    pipeline.repository = mock_repo
    mock_repo.find_document_for_delete.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/documents/delete", json={"document_id": "ghost"})

    assert response.status_code == 200
    assert response.json()["deleted"] is None
    assert "not found" in response.json()["message"]


@pytest.mark.asyncio
async def test_search_success(mock_state):
    """Проверка семантического поиска."""
    pipeline, _ = mock_state

    mock_result = RagSearchResult(
        document_id="doc1",
        document_title="T1",
        source_path="p1",
        discipline_id="d1",
        chunk_id="c1",
        chunk_index=0,
        page=1,
        score=0.95,
        content="Relevant content",
    )
    pipeline.search_documents.return_value = [mock_result]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/search", json={"query": "hello", "limit": 1})

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["results"][0]["content"] == "Relevant content"
    assert data["results"][0]["score"] == 0.95


@pytest.mark.asyncio
async def test_context_success(mock_state):
    """Проверка сборки RAG-контекста."""
    pipeline, _ = mock_state

    # Используем реальные Pydantic-модели вместо MagicMock
    mock_chunk = RagSearchResult(
        document_id="doc1",
        document_title="T1",
        source_path="p1",
        discipline_id="d1",
        chunk_id="c1",
        chunk_index=0,
        page=1,
        score=0.95,
        content="...",
    )

    mock_context = MagicMock()
    mock_context.answer_instruction = "The actual context text"
    mock_context.chunks = [mock_chunk]
    pipeline.build_rag_context.return_value = mock_context

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/context", json={"query": "hello"})

    assert response.status_code == 200
    data = response.json()
    assert data["context"] == "The actual context text"
    assert len(data["sources"]) == 1
