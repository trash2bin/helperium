"""Agent Store — re-export from agent_repository for backward compat.

Legacy import:
    from api_service.agent_store import AgentStore
→ still works, returns SqliteAgentRepository (which has same API).

Новый импорт:
    from api_service.agent_repository import AgentRepository, SqliteAgentRepository
"""

from api_service.agent_repository import (
    AgentRepository,
    SqliteAgentRepository,
    _FERNET,
    _decrypt_value,
    _encrypt_value,
    _json_or_none,
    _parse_config,
    _unpack_json,
)

# backward compat: old name AgentStore → SqliteAgentRepository
AgentStore = SqliteAgentRepository

__all__ = [
    "AgentRepository",
    "AgentStore",
    "SqliteAgentRepository",
    "_FERNET",
    "_decrypt_value",
    "_encrypt_value",
    "_json_or_none",
    "_parse_config",
    "_unpack_json",
]
