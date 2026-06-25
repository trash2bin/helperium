"""RAG-инструменты — не зависят от data-service.

Используют HTTP-клиент к RAG-сервису (не к БД университета).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from agent_tutor_sdk.rag.client import RagClient, RAG_SERVICE_URL

logger = logging.getLogger(__name__)

rag_client: RagClient | None = None


def init_rag() -> None:
    global rag_client
    if rag_client is not None:
        return
    logger.info("Initializing RAG client...")
    rag_client = RagClient(RAG_SERVICE_URL)
    logger.info("RAG client ready")


async def _list_documents(
    discipline_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Any]:
    return await asyncio.to_thread(rag_client.list_documents_sync, discipline_id, limit) or []


async def _search_documents(
    query: str,
    discipline_id: Optional[str] = None,
    limit: int = 5,
) -> List[Any]:
    return await asyncio.to_thread(rag_client.search_documents_sync, query, discipline_id, limit) or []


async def _context_search_in_documents(
    query: str,
    discipline_id: Optional[str] = None,
    limit: int = 5,
) -> Any:
    return await asyncio.to_thread(rag_client.build_rag_context_sync, query, discipline_id, limit)


async def _get_health_status_rag() -> dict:
    rag_status = {"status": "ok", "error": None}
    try:
        if rag_client is None:
            raise RuntimeError("RAG client not initialized")
        health = await asyncio.to_thread(rag_client.health_sync)
        if health.get("status") != "ok":
            rag_status = {"status": "error", "error": "RAG service degraded"}
    except Exception as e:
        rag_status = {"status": "error", "error": str(e)}
    return rag_status
