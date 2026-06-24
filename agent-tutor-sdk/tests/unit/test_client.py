import pytest
import respx
from httpx import Response
from agent_tutor_sdk.rag.client import RagClient
from agent_tutor_sdk.rag.models import Document, RagSearchResult


@pytest.fixture
def rag_client():
    # Используем тестовый URL через base_url
    return RagClient(base_url="http://test-rag:8082")


@pytest.mark.asyncio
@respx.mock
async def test_client_list_documents(rag_client):
    """Проверка вызова list_documents через клиент."""
    route = respx.post("http://test-rag:8082/documents/list").mock(
        return_value=Response(
            200,
            json={
                "documents": [
                    {
                        "id": "doc1",
                        "title": "T1",
                        "source_path": "p1",
                        "mime_type": "txt",
                        "discipline_id": "d1",
                        "created_at": "now",
                    }
                ],
                "count": 1,
            },
        )
    )

    docs = await rag_client.list_documents(discipline_id="d1")

    assert route.called
    assert len(docs) == 1
    assert docs[0].id == "doc1"
    assert isinstance(docs[0], Document)


@pytest.mark.asyncio
@respx.mock
async def test_client_search_documents(rag_client):
    """Проверка вызова search_documents через клиент."""
    route = respx.post("http://test-rag:8082/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "document_id": "doc1",
                        "document_title": "T1",
                        "source_path": "p1",
                        "discipline_id": "d1",
                        "chunk_id": "c1",
                        "chunk_index": 0,
                        "page": 1,
                        "score": 0.9,
                        "content": "Hello world",
                    }
                ],
                "count": 1,
            },
        )
    )

    results = await rag_client.search_documents(query="hello")

    assert route.called
    assert len(results) == 1
    assert results[0].content == "Hello world"
    assert isinstance(results[0], RagSearchResult)


@pytest.mark.asyncio
@respx.mock
async def test_client_get_context(rag_client):
    """Проверка вызова get_rag_context через клиент."""
    route = respx.post("http://test-rag:8082/context").mock(
        return_value=Response(
            200,
            json={
                "query": "hello",
                "answer_instruction": "Ответь на вопрос, используя контекст.",
                "chunks": [
                    {
                        "document_id": "doc1",
                        "document_title": "T1",
                        "source_path": "p1",
                        "discipline_id": "d1",
                        "chunk_id": "c1",
                        "chunk_index": 0,
                        "page": 1,
                        "score": 0.9,
                        "content": "Some content",
                    }
                ],
            },
        )
    )

    context_data = await rag_client.build_rag_context(query="hello")

    assert route.called
    assert context_data.query == "hello"
    assert len(context_data.chunks) == 1


@pytest.mark.asyncio
@respx.mock
async def test_client_error_handling(rag_client):
    """Проверка обработки HTTP-ошибок клиентом."""
    respx.post("http://test-rag:8082/health").mock(return_value=Response(500))

    # Клиент должен пробрасывать исключение или обрабатывать его
    # В текущей реализации RagClient просто вызывает response.raise_for_status()
    with pytest.raises(Exception):
        await rag_client.health()
