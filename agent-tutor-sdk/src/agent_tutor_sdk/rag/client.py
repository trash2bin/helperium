"""HTTP-клиент для RAG-сервиса.

Используется MCP-сервером и другими компонентами для вызовов к standalone RAG-сервису.

Публичный API — асинхронный (httpx.AsyncClient).
Для синхронных CLI-вызовов (agent-ingest, agent-generate) — отдельный класс
RagClientSync, который запускает async-методы через asyncio.run.
НЕ использовать sync-методы из async-контекста — они блокируют event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from agent_tutor_sdk.rag.models import (
    Document,
    DocumentImportResult,
    RagContext,
    RagSearchResult,
)

logger = logging.getLogger(__name__)

# === Конфигурация клиента ===

RAG_SERVICE_URL: str = os.environ.get("RAG_SERVICE_URL", "http://127.0.0.1:8082")
RAG_HTTP_TIMEOUT: float = float(os.environ.get("RAG_HTTP_TIMEOUT", "60.0"))


class RagClient:
    """Асинхронный HTTP-клиент к RAG-сервису.

    Для синхронных вызовов используйте RagClientSync (CLI).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = RAG_HTTP_TIMEOUT,
    ):
        self.base_url = base_url or RAG_SERVICE_URL
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RagClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _build_url(self, endpoint: str) -> str:
        return f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    async def health(self) -> dict[str, Any]:
        response = await self.client.get(self._build_url("/health"))
        response.raise_for_status()
        return response.json()

    async def list_documents(
        self,
        discipline_id: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        payload: dict[str, Any] = {}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if limit is not None:
            payload["limit"] = limit

        response = await self.client.post(
            self._build_url("/documents/list"), json=payload
        )
        response.raise_for_status()
        data = response.json()
        return [Document(**doc) for doc in data["documents"]]

    async def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        discipline_name: str | None = None,
        title: str | None = None,
    ) -> DocumentImportResult:
        payload: dict[str, Any] = {"path": path}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if discipline_name is not None:
            payload["discipline_name"] = discipline_name
        if title is not None:
            payload["title"] = title

        response = await self.client.post(
            self._build_url("/documents/import"), json=payload
        )
        if response.status_code == 404:
            raise FileNotFoundError(f"Document not found: {path}")
        elif response.status_code == 422:
            raise ValueError(f"Invalid document: {response.text}")
        response.raise_for_status()
        data = response.json()
        return DocumentImportResult(
            document=Document(**data["document"]),
            chunks_count=data["chunks_count"],
        )

    async def delete_document(
        self,
        path: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if path is not None:
            payload["path"] = path
        if document_id is not None:
            payload["document_id"] = document_id

        response = await self.client.post(
            self._build_url("/documents/delete"), json=payload
        )
        response.raise_for_status()
        return response.json()

    async def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        payload: dict[str, Any] = {"query": query, "limit": limit}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        response = await self.client.post(self._build_url("/search"), json=payload)
        response.raise_for_status()
        data = response.json()
        return [RagSearchResult(**r) for r in data["results"]]

    async def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        payload: dict[str, Any] = {"query": query, "limit": limit}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        response = await self.client.post(self._build_url("/context"), json=payload)
        response.raise_for_status()
        data = response.json()
        return RagContext(**data)


class RagClientSync:
    """Синхронная обёртка над RagClient для CLI (agent-ingest, agent-generate).

    Каждый метод запускает соответствующий async-метод через asyncio.run.
    НЕ использовать из async-контекста (FastAPI endpoint, MCP tool) — блокирует event loop.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = RAG_HTTP_TIMEOUT,
    ):
        self.base_url = base_url or RAG_SERVICE_URL
        self.timeout = timeout

    def _build_client(self) -> RagClient:
        return RagClient(base_url=self.base_url, timeout=self.timeout)

    def health(self) -> dict[str, Any]:
        return asyncio.run(self._build_client().health())

    def list_documents(
        self,
        discipline_id: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        return asyncio.run(self._build_client().list_documents(discipline_id, limit))

    def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        discipline_name: str | None = None,
        title: str | None = None,
    ) -> DocumentImportResult:
        return asyncio.run(
            self._build_client().import_document(
                path, discipline_id, discipline_name, title
            )
        )

    def delete_document(
        self,
        path: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(self._build_client().delete_document(path, document_id))

    def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        return asyncio.run(
            self._build_client().search_documents(query, discipline_id, limit)
        )

    def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        return asyncio.run(
            self._build_client().build_rag_context(query, discipline_id, limit)
        )
