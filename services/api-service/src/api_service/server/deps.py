"""Dependency injection — lazy singletons and startup helpers."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from api_service.agent.orchestrator import LLMAgent, _pool as _provider_pool
from api_service.agent_repository import SqliteAgentRepository
from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.server")

_agent_instance: LLMAgent | None = None
_agent_lock = threading.Lock()
_agent_store: SqliteAgentRepository | None = None


def get_agent_store() -> SqliteAgentRepository:
    global _agent_store
    if _agent_store is None:
        with _agent_lock:
            if _agent_store is None:
                db_path = os.environ.get(
                    "AGENT_DB_PATH",
                    str(Path(settings.session_db_path).parent / "agents.sqlite"),
                )
                _agent_store = SqliteAgentRepository(db_path)
                logger.info("Agent store initialized at %s", db_path)
    return _agent_store


def get_agent() -> LLMAgent:
    """Получить (или создать) глобальный экземпляр агента.

    Инициализируется лениво — при первом обращении, а не при импорте модуля.
    Это позволяет:
      - менять окружение до первого запроса (тесты, разные конфиги)
      - не падать при импорте если MCP/БД недоступны
      - пересоздавать агента между тестами
    """
    global _agent_instance
    if _agent_instance is None:
        with _agent_lock:
            if _agent_instance is None:
                logger.info("Initializing LLM agent...")
                _agent_instance = LLMAgent()
                logger.info("LLM agent initialized")
    return _agent_instance


def get_agent_instance() -> LLMAgent | None:
    """Return the raw singleton reference (may be None before first request)."""
    return _agent_instance


async def _sync_pool_from_store() -> int:
    """Sync ProviderPool workers from ProviderStore.

    Reads all enabled providers from ProviderStore and (re-)populates
    the pool.  Returns the number of workers added.

    Called at startup.  Call again when providers change via admin API.
    """
    from api_service.provider_store import get_provider_store

    store = get_provider_store()
    router_config = store.get_active_router_config()

    # Clear and re-populate
    await _provider_pool.clear()

    count = 0
    for entry in router_config:
        params = entry.get("litellm_params", {})
        model = params.get("model", "")
        if not model:
            continue
        api_key = params.get("api_key", "") or ""
        api_base = params.get("api_base", "") or ""
        temperature = float(params.get("temperature", 0.5))
        timeout = float(params.get("timeout", 120.0))

        name = entry.get("model_name", model)
        _provider_pool.add_worker(
            name=name,
            model=model,
            api_base=api_base,
            api_key=api_key,
            timeout=timeout,
            temperature=temperature,
        )
        count += 1

    # Start background health checks after populating
    _provider_pool.start_health_checks()

    logger.info(
        "ProviderPool synced: %d workers from ProviderStore, health checks started",
        count,
    )
    return count
