"""HTTP-клиент для data-service (Go-сервис доступа к БД).

Используется MCP-сервером и API для вызовов к data-service через HTTP.

Публичный API — только асинхронный (AsyncDataServiceClient).
Для синхронных CLI-вызовов — DataServiceClientSync (на базе httpx.Client).

Все методы возвращают :class:`Entity` — generic-запись из data-service.
ID-поля семантических названий контрактов (full_name, value) живут �� data-service.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import httpx

from agent_tutor_sdk.models import Entity

logger = logging.getLogger(__name__)

DATA_SERVICE_URL: str = os.environ.get("DATA_SERVICE_URL", "http://127.0.0.1:8084")


class AsyncDataServiceClient:
    """Тонкий асинхронный HTTP-клиент к Go data-service.

    Используется в long-running сервисах (api, mcp) — не блокирует event loop.
    Поддерживает переиспользование HTTP-соединения через :class:`httpx.AsyncClient`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or DATA_SERVICE_URL).rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncDataServiceClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    async def _get(self, path: str) -> httpx.Response:
        return await self.client.get(self._url(path))

    # ── Health ──

    async def health(self) -> dict[str, str]:
        resp = await self._get("/health")
        resp.raise_for_status()
        return resp.json()

    # ── Stats ──

    async def get_stats(self) -> dict[str, int]:
        resp = await self._get("/stats")
        resp.raise_for_status()
        return resp.json()

    # ── Generic CRUD ──

    @staticmethod
    def _parse_one(resp: httpx.Response) -> Entity | None:
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Entity(**resp.json())

    @staticmethod
    def _parse_many(resp: httpx.Response) -> list[Entity]:
        resp.raise_for_status()
        return [Entity(**item) for item in resp.json()]

    async def get(self, entity: str, id: str) -> Entity | None:
        """Get one entity by ID, e.g. get(\"students\", \"s1\")."""
        return self._parse_one(await self._get(f"/{entity}/{quote(id, safe='')}"))

    async def find(self, entity: str, field: str, value: str) -> Entity | None:
        """Find one entity by field value, e.g. find(\"students\", \"name\", \"Иван\")."""
        return self._parse_one(await self._get(f"/{entity}?{field}={quote(value)}"))

    async def list_all(self, entity: str) -> list[Entity]:
        """List all entities, e.g. list_all(\"students\")."""
        return self._parse_many(await self._get(f"/{entity}"))


class DataServiceClientSync:
    """Синхронный HTTP-клиент к Go data-service для CLI (agent-generate, document_generator).

    Использует httpx.Client для синхронных вызовов, не блокируя и не создавая event loop.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or DATA_SERVICE_URL).rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str) -> httpx.Response:
        return self.client.get(self._url(path))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "DataServiceClientSync":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── Health ──

    def health(self) -> dict[str, str]:
        resp = self._get("/health")
        resp.raise_for_status()
        return resp.json()

    def get_stats(self) -> dict[str, int]:
        resp = self._get("/stats")
        resp.raise_for_status()
        return resp.json()

    # ── Generic CRUD ──

    @staticmethod
    def _parse_one(resp: httpx.Response) -> Entity | None:
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Entity(**resp.json())

    @staticmethod
    def _parse_many(resp: httpx.Response) -> list[Entity]:
        resp.raise_for_status()
        return [Entity(**item) for item in resp.json()]

    def get(self, entity: str, id: str) -> Entity | None:
        """Get one entity by ID, e.g. get(\"students\", \"s1\")."""
        return self._parse_one(self._get(f"/{entity}/{quote(id, safe='')}"))

    def find(self, entity: str, field: str, value: str) -> Entity | None:
        """Find one entity by field value, e.g. find(\"students\", \"name\", \"Иван\")."""
        return self._parse_one(self._get(f"/{entity}?{field}={quote(value)}"))

    def list_all(self, entity: str) -> list[Entity]:
        """List all entities, e.g. list_all(\"students\")."""
        return self._parse_many(self._get(f"/{entity}"))


# ── Глобальный экземпляр (ленивый) ───

_async_client: AsyncDataServiceClient | None = None


def get_async_data_service_client() -> AsyncDataServiceClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncDataServiceClient()
    return _async_client
