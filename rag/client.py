"""HTTP-клиент для RAG-сервиса.

Используется MCP-сервером и другими компонентами для вызовов к standalone RAG-сервису.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rag.models import Document, DocumentImportResult, RagContext, RagSearchResult

logger = logging.getLogger(__name__)

# === Конфигурация клиента ===

RAG_SERVICE_URL: str = os.environ.get("RAG_SERVICE_URL", "http://127.0.0.1:8082")
RAG_HTTP_TIMEOUT: float = float(os.environ.get("RAG_HTTP_TIMEOUT", "60.0"))


class RagClient:
    """Тонкий HTTP-клиент для обращений к RAG-сервису.
    
    Создаётся с базовым URL сервиса, поддерживает таймауты и переиспользование
    асинхронного клиента httpx для повышения производительности.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = RAG_HTTP_TIMEOUT,
    ):
        """Инициализация клиента.
        
        Args:
            base_url: базовый URL RAG-сервиса (по умолчанию из RAG_SERVICE_URL)
            timeout: таймаут для HTTP-запросов в секундах
        """
        self.base_url = base_url or RAG_SERVICE_URL
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RagClient":
        """Асинхронный контекстный менеджер для клиента."""
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Закрытие асинхронного клиента."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Ленивая инициализация асинхронного клиента."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        """Закрыть клиент синхронно (если был создан)."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _build_url(self, endpoint: str) -> str:
        """Построить полный URL эндпоинта."""
        return f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    async def health(self) -> dict[str, Any]:
        """Проверка состояния RAG-сервиса.
        
        Returns:
            dict с полем 'status' ('ok' или 'degraded') и статусами компонентов
        """
        url = self._build_url("/health")
        async with self.client as c:
            response = await c.get(url)
            response.raise_for_status()
            return response.json()

    async def list_documents(
        self,
        discipline_id: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        """Получить список документов.
        
        Args:
            discipline_id: опциональный фильтр по ID дисциплины
            limit: опциональное ограничение количества документов
            
        Returns:
            список Document
        """
        url = self._build_url("/documents/list")
        payload: dict[str, Any] = {}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if limit is not None:
            payload["limit"] = limit

        async with self.client as c:
            response = await c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return [Document(**doc) for doc in data["documents"]]

    async def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        title: str | None = None,
    ) -> DocumentImportResult:
        """Импортировать документ в RAG-индекс.
        
        Args:
            path: путь к файлу документа
            discipline_id: опциональный ID дисциплины
            title: опциональное название документа
            
        Returns:
            DocumentImportResult с документом и количеством чанков
            
        Raises:
            FileNotFoundError: если файл не найден
            ValueError: если документ невалиден
        """
        url = self._build_url("/documents/import")
        payload: dict[str, Any] = {"path": path}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if title is not None:
            payload["title"] = title

        async with self.client as c:
            response = await c.post(url, json=payload)
            
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
        """Удалить документ из RAG-индекса.
        
        Args:
            path: путь к файлу (опционально)
            document_id: ID документа (опционально)
            
        Returns:
            dict с информацией об удалённом документе
            
        Raises:
            ValueError: если не передан ни path, ни document_id
        """
        url = self._build_url("/documents/delete")
        payload: dict[str, Any] = {}
        if path is not None:
            payload["path"] = path
        if document_id is not None:
            payload["document_id"] = document_id

        async with self.client as c:
            response = await c.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    async def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск по фрагментам документов.
        
        Args:
            query: поисковый запрос
            discipline_id: опциональный фильтр по ID дисциплины
            limit: максимальное количество результатов (1-20)
            
        Returns:
            список RagSearchResult
        """
        url = self._build_url("/search")
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        async with self.client as c:
            response = await c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return [RagSearchResult(**r) for r in data["results"]]

    async def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        """Получить готовый RAG-контекст для LLM-ответа.
        
        Args:
            query: вопрос пользователя
            discipline_id: опциональный фильтр по ID дисциплины
            limit: количество фрагментов в контексте (1-20)
            
        Returns:
            RagContext с инструкцией и чанками
        """
        url = self._build_url("/context")
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        async with self.client as c:
            response = await c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return RagContext(**data)

    # === Синхронные обёртки для использования в синхронном коде ===

    def health_sync(self) -> dict[str, Any]:
        """Синхронная проверка состояния."""
        import httpx as sync_httpx
        
        url = self._build_url("/health")
        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.get(url)
            response.raise_for_status()
            return response.json()

    def list_documents_sync(
        self,
        discipline_id: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        """Синхронный список документов."""
        import httpx as sync_httpx
        
        url = self._build_url("/documents/list")
        payload: dict[str, Any] = {}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if limit is not None:
            payload["limit"] = limit

        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return [Document(**doc) for doc in data["documents"]]

    def import_document_sync(
        self,
        path: str,
        discipline_id: str | None = None,
        title: str | None = None,
    ) -> DocumentImportResult:
        """Синхронный импорт документа."""
        import httpx as sync_httpx
        
        url = self._build_url("/documents/import")
        payload: dict[str, Any] = {"path": path}
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id
        if title is not None:
            payload["title"] = title

        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.post(url, json=payload)
            
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

    def delete_document_sync(
        self,
        path: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Синхронное удаление документа."""
        import httpx as sync_httpx
        
        url = self._build_url("/documents/delete")
        payload: dict[str, Any] = {}
        if path is not None:
            payload["path"] = path
        if document_id is not None:
            payload["document_id"] = document_id

        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    def search_documents_sync(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Синхронный поиск по документам."""
        import httpx as sync_httpx
        
        url = self._build_url("/search")
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return [RagSearchResult(**r) for r in data["results"]]

    def build_rag_context_sync(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        """Синхронный RAG-контекст."""
        import httpx as sync_httpx
        
        url = self._build_url("/context")
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if discipline_id is not None:
            payload["discipline_id"] = discipline_id

        with sync_httpx.Client(timeout=self.timeout) as c:
            response = c.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return RagContext(**data)
