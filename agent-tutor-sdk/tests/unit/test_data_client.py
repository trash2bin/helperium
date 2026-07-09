"""Тесты HTTP-клиента data-service (AsyncDataServiceClient + DataServiceClientSync).

Проверяет:
- AsyncDataServiceClient: health, stats, CRUD (get/find/list_all), _parse_one, _parse_many,
  error handling, lazy client init, context manager
- DataServiceClientSync: health, get, list_all, close, context manager

Запуск:
    uv run pytest agent-tutor-sdk/tests/unit/test_data_client.py -v
"""

from __future__ import annotations

import pytest
import respx
from httpx import Request, Response

from agent_tutor_sdk.data_client import AsyncDataServiceClient, DataServiceClientSync
from agent_tutor_sdk.models import Entity

BASE_URL = "http://test-ds:8084"


def _resp(status_code: int, json_data=None) -> Response:
    """Create an httpx.Response with a minimal request set.

    httpx's raise_for_status() requires request to be set on the Response.
    """
    req = Request("GET", BASE_URL)
    return Response(status_code=status_code, json=json_data, request=req)


# ── Fixtures ──


@pytest.fixture
def async_client() -> AsyncDataServiceClient:
    return AsyncDataServiceClient(base_url=BASE_URL)


@pytest.fixture
def sync_client() -> DataServiceClientSync:
    return DataServiceClientSync(base_url=BASE_URL)


# ═══════════════════════════════════════════════════════════════
# AsyncDataServiceClient
# ═══════════════════════════════════════════════════════════════


class TestAsyncClient:
    """AsyncDataServiceClient — асинхронные HTTP-вызовы к data-service."""

    # ── Health ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_health(self, async_client):
        """health() → GET /health → dict."""
        route = respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        result = await async_client.health()

        assert route.called
        assert result == {"status": "ok"}

    # ── Stats ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_get_stats(self, async_client):
        """get_stats() → GET /stats → dict."""
        route = respx.get(f"{BASE_URL}/stats").mock(
            return_value=Response(200, json={"entities": 42, "tenants": 3})
        )

        result = await async_client.get_stats()

        assert route.called
        assert result == {"entities": 42, "tenants": 3}

    # ── Get ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_get_found(self, async_client):
        """get(entity, id) → 200 → Entity."""
        route = respx.get(f"{BASE_URL}/students/s1").mock(
            return_value=Response(
                200, json={"id": "s1", "full_name": "Alice", "course": 3}
            )
        )

        entity = await async_client.get("students", "s1")

        assert route.called
        assert entity is not None
        assert isinstance(entity, Entity)
        assert entity.id == "s1"
        assert entity.full_name == "Alice"
        assert entity.course == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_get_not_found(self, async_client):
        """get(entity, id) → 404 → None."""
        route = respx.get(f"{BASE_URL}/students/nonexistent").mock(
            return_value=Response(404)
        )

        entity = await async_client.get("students", "nonexistent")

        assert route.called
        assert entity is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_get_url_encoded_id(self, async_client):
        """get() URL-encodes special characters в id."""
        route = respx.get(f"{BASE_URL}/items/id%2F1").mock(
            return_value=Response(200, json={"id": "id/1"})
        )

        entity = await async_client.get("items", "id/1")

        assert route.called
        assert entity is not None
        assert entity.id == "id/1"

    # ── Find ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_find_found(self, async_client):
        """find(entity, field, value) → Entity."""
        route = respx.get(f"{BASE_URL}/students?name=Alice").mock(
            return_value=Response(
                200, json={"id": "s1", "full_name": "Alice", "course": 3}
            )
        )

        entity = await async_client.find("students", "name", "Alice")

        assert route.called
        assert entity is not None
        assert entity.full_name == "Alice"

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_find_url_encoded_value(self, async_client):
        """find() URL-encodes значение query-параметра."""
        route = respx.get(f"{BASE_URL}/students?name=%D0%98%D0%B2%D0%B0%D0%BD").mock(
            return_value=Response(200, json={"id": "s2", "full_name": "Иван"})
        )

        entity = await async_client.find("students", "name", "Иван")

        assert route.called
        assert entity is not None
        assert entity.full_name == "Иван"

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_find_not_found(self, async_client):
        """find(entity, field, value) → 404 → None."""
        route = respx.get(f"{BASE_URL}/students?name=NoOne").mock(
            return_value=Response(404)
        )

        entity = await async_client.find("students", "name", "NoOne")

        assert route.called
        assert entity is None

    # ── List All ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_list_all(self, async_client):
        """list_all(entity) → list[Entity]."""
        route = respx.get(f"{BASE_URL}/students").mock(
            return_value=Response(
                200,
                json=[
                    {"id": "s1", "full_name": "Alice"},
                    {"id": "s2", "full_name": "Bob"},
                ],
            )
        )

        entities = await async_client.list_all("students")

        assert route.called
        assert len(entities) == 2
        assert all(isinstance(e, Entity) for e in entities)
        assert entities[0].full_name == "Alice"
        assert entities[1].full_name == "Bob"

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_list_all_empty(self, async_client):
        """list_all(entity) → пустой список."""
        route = respx.get(f"{BASE_URL}/empty").mock(
            return_value=Response(200, json=[])
        )

        entities = await async_client.list_all("empty")

        assert route.called
        assert entities == []

    # ── Error handling ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_health_500_raises(self, async_client):
        """health() → 500 → raise httpx.HTTPStatusError."""
        respx.get(f"{BASE_URL}/health").mock(return_value=Response(500))

        with pytest.raises(Exception):
            await async_client.health()

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_get_500_raises(self, async_client):
        """get() → 500 → raise."""
        respx.get(f"{BASE_URL}/students/s1").mock(return_value=Response(500))

        with pytest.raises(Exception):
            await async_client.get("students", "s1")

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_list_all_500_raises(self, async_client):
        """list_all() → 500 → raise."""
        respx.get(f"{BASE_URL}/students").mock(return_value=Response(500))

        with pytest.raises(Exception):
            await async_client.list_all("students")

    # ── _parse_one static ──

    def test_async_parse_one_404_returns_none(self):
        """_parse_one(404) → None (не raise)."""
        resp = _resp(404)
        result = AsyncDataServiceClient._parse_one(resp)
        assert result is None

    def test_async_parse_one_200_returns_entity(self):
        """_parse_one(200) → Entity."""
        resp = _resp(200, json_data={"id": "x", "name": "test"})
        result = AsyncDataServiceClient._parse_one(resp)
        assert isinstance(result, Entity)
        assert result.id == "x"
        assert result.name == "test"

    def test_async_parse_one_500_raises(self):
        """_parse_one(500) → raise."""
        resp = _resp(500)
        with pytest.raises(Exception):
            AsyncDataServiceClient._parse_one(resp)

    # ── _parse_many static ──

    def test_async_parse_many_empty(self):
        """_parse_many([]) → []. """
        resp = _resp(200, json_data=[])
        result = AsyncDataServiceClient._parse_many(resp)
        assert result == []

    def test_async_parse_many_objects(self):
        """_parse_many([...]) → list[Entity]."""
        resp = _resp(200, json_data=[{"id": "a"}, {"id": "b"}])
        result = AsyncDataServiceClient._parse_many(resp)
        assert len(result) == 2
        assert all(isinstance(e, Entity) for e in result)
        assert result[0].id == "a"
        assert result[1].id == "b"

    def test_async_parse_many_500_raises(self):
        """_parse_many(500) → raise."""
        resp = _resp(500)
        with pytest.raises(Exception):
            AsyncDataServiceClient._parse_many(resp)

    # ── Lazy client init ──

    def test_async_client_lazy_init(self):
        """_client не создаётся в __init__, только при первом обращении."""
        client = AsyncDataServiceClient(base_url=BASE_URL)
        assert client._client is None  # не создан в __init__

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_client_client_created_on_first_call(self, async_client):
        """client создаётся при первом HTTP-вызове."""
        assert async_client._client is None
        respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )
        await async_client.health()
        assert async_client._client is not None

    # ── Context manager ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_context_manager(self):
        """__aenter__/__aexit__ — клиент закрывается после выхода."""
        respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        async with AsyncDataServiceClient(base_url=BASE_URL) as client:
            result = await client.health()
            assert result == {"status": "ok"}
            assert client._client is not None

        # После выхода _client должен быть None
        assert client._client is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_async_aclose(self, async_client):
        """aclose() закрывает и обнуляет _client."""
        respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )
        await async_client.health()
        assert async_client._client is not None

        await async_client.aclose()
        assert async_client._client is None


# ═══════════════════════════════════════════════════════════════
# DataServiceClientSync
# ═══════════════════════════════════════════════════════════════


class TestSyncClient:
    """DataServiceClientSync — синхронные HTTP-вызовы к data-service."""

    # ── Health ──

    @respx.mock
    def test_sync_health(self, sync_client):
        """health() → GET /health → dict."""
        route = respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        result = sync_client.health()

        assert route.called
        assert result == {"status": "ok"}

    # ── Stats ──

    @respx.mock
    def test_sync_get_stats(self, sync_client):
        """get_stats() → GET /stats → dict."""
        route = respx.get(f"{BASE_URL}/stats").mock(
            return_value=Response(200, json={"entities": 42})
        )

        result = sync_client.get_stats()

        assert route.called
        assert result == {"entities": 42}

    # ── Get ──

    @respx.mock
    def test_sync_get_found(self, sync_client):
        """get(entity, id) → 200 → Entity."""
        route = respx.get(f"{BASE_URL}/students/s1").mock(
            return_value=Response(200, json={"id": "s1", "full_name": "Alice"})
        )

        entity = sync_client.get("students", "s1")

        assert route.called
        assert entity is not None
        assert isinstance(entity, Entity)
        assert entity.full_name == "Alice"

    @respx.mock
    def test_sync_get_not_found(self, sync_client):
        """get(entity, id) → 404 → None."""
        route = respx.get(f"{BASE_URL}/students/nonexistent").mock(
            return_value=Response(404)
        )

        entity = sync_client.get("students", "nonexistent")

        assert route.called
        assert entity is None

    @respx.mock
    def test_sync_get_500_raises(self, sync_client):
        """get(entity, id) → 500 → raise."""
        respx.get(f"{BASE_URL}/students/s1").mock(return_value=Response(500))

        with pytest.raises(Exception):
            sync_client.get("students", "s1")

    # ── List All ──

    @respx.mock
    def test_sync_list_all(self, sync_client):
        """list_all(entity) → list[Entity]."""
        route = respx.get(f"{BASE_URL}/students").mock(
            return_value=Response(
                200,
                json=[
                    {"id": "s1", "full_name": "Alice"},
                    {"id": "s2", "full_name": "Bob"},
                ],
            )
        )

        entities = sync_client.list_all("students")

        assert route.called
        assert len(entities) == 2
        assert all(isinstance(e, Entity) for e in entities)
        assert entities[0].full_name == "Alice"

    @respx.mock
    def test_sync_list_all_empty(self, sync_client):
        """list_all(entity) → пустой список."""
        route = respx.get(f"{BASE_URL}/empty").mock(
            return_value=Response(200, json=[])
        )

        entities = sync_client.list_all("empty")

        assert route.called
        assert entities == []

    # ── Lazy client init ──

    def test_sync_client_lazy_init(self):
        """_client не создаётся в __init__."""
        client = DataServiceClientSync(base_url=BASE_URL)
        assert client._client is None

    # ── Close ──

    @respx.mock
    def test_sync_close(self, sync_client):
        """close() закрывает и обнуляет _client."""
        respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )
        sync_client.health()
        assert sync_client._client is not None

        sync_client.close()
        assert sync_client._client is None

    # ── Context manager ──

    @respx.mock
    def test_sync_context_manager(self):
        """__enter__/__exit__ — клиент закрывается после выхода."""
        respx.get(f"{BASE_URL}/health").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        with DataServiceClientSync(base_url=BASE_URL) as client:
            result = client.health()
            assert result == {"status": "ok"}
            assert client._client is not None

        assert client._client is None
