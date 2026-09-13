"""Voice configuration storage (STT providers).

Provides functional API for loading/saving/resolving voice config
as Pydantic VoiceConfig models.

Backed by SQLite (global_config table in agents.sqlite). There is no
auto-seeded default: voice stays unconfigured (empty STT provider list)
until an admin saves a config via ``PUT /api/voice-config``.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from helperium_sdk.api.models import VoiceAgentConfig, VoiceConfig
from api_service.audio.stt_engine import LiteLLMSTTProvider, LocalSTTProvider
from api_service.agent_repository import SqliteAgentRepository

logger = logging.getLogger(__name__)

_GLOBAL_CONFIG_KEY = "voice"


def _get_db_path() -> str:
    """Resolve the agents.sqlite path the same way AgentStore does."""
    from helperium_sdk.settings import settings

    return os.environ.get(
        "AGENT_DB_PATH",
        str(Path(settings.session_db_path).parent / "agents.sqlite"),
    )


# Singleton repo — shares the same SQLite file as SqliteAgentRepository
_repo: SqliteAgentRepository | None = None
_repo_lock = threading.Lock()


def _get_repo() -> SqliteAgentRepository:
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = SqliteAgentRepository(_get_db_path())
    return _repo


# ── Provider builders ──


def build_stt_providers(config):
    """Build STT provider instances from a Pydantic VoiceConfig object.

    Falls back to ``OPENAI_API_KEY`` env var when ``api_key`` is not set
    in the stored config (matching the pattern in ``LiteLLMProvider``).
    """
    providers = []
    for p in config.stt_providers:
        if not p.enabled:
            continue
        if p.provider == "litellm":
            api_key = p.api_key or os.environ.get("OPENAI_API_KEY")
            providers.append(
                LiteLLMSTTProvider(
                    name=p.name, model=p.model, api_key=api_key, api_base=p.api_base
                )
            )
        elif p.provider == "local":
            providers.append(LocalSTTProvider(name=p.name, model=p.model))
    return providers


# ── Functional API used by server.py ──


def load_voice_config() -> VoiceConfig:
    """Load the current voice config as a Pydantic VoiceConfig model.

    Returns an unconfigured ``VoiceConfig()`` (no STT providers) until an
    admin persists a config; nothing is auto-seeded on first boot.
    """
    repo = _get_repo()
    raw = repo.get_global_config(_GLOBAL_CONFIG_KEY)
    if raw is None:
        return VoiceConfig()
    return VoiceConfig(**raw)


def save_voice_config(config: VoiceConfig) -> None:
    """Persist a VoiceConfig model to the store."""
    repo = _get_repo()
    repo.set_global_config(_GLOBAL_CONFIG_KEY, config.model_dump(mode="json"))


def resolve_voice_config(
    global_config: VoiceConfig,
    agent_override: VoiceAgentConfig | None,
) -> VoiceConfig:
    """Merge global voice config with per-agent overrides.

    Returns a new VoiceConfig with agent overrides applied.
    """
    if agent_override is None:
        return global_config

    cfg = global_config.model_dump(mode="json")

    if agent_override.enabled is not None:
        cfg["enabled"] = agent_override.enabled
    if agent_override.stt_fallback is not None:
        cfg["stt_fallback_enabled"] = agent_override.stt_fallback
    if agent_override.voice_input_disabled is True:
        cfg["enabled"] = False

    return VoiceConfig(**cfg)
