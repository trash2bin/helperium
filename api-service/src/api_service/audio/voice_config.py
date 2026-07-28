"""Voice configuration storage (STT/TTS providers).

Provides functional API for loading/saving/resolving voice config
as Pydantic VoiceConfig models.

Backed by SQLite (global_config table in agents.sqlite).
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path

from helperium_sdk.api.models import VoiceAgentConfig, VoiceConfig
from api_service.audio.stt_engine import LiteLLMSTTProvider, LocalSTTProvider
from api_service.audio.tts_engine import LiteLLMTTSProvider, LocalTTSProvider
from api_service.agent_repository import SqliteAgentRepository

logger = logging.getLogger(__name__)

_GLOBAL_CONFIG_KEY = "voice"

DEFAULT_VOICE_CONFIG_DICT: dict = {
    "enabled": True,
    "stt_providers": [
        {
            "name": "OpenAI Whisper",
            "provider": "litellm",
            "model": "whisper-1",
            "api_key": None,
            "api_base": None,
            "enabled": True,
        }
    ],
    "tts_providers": [
        {
            "name": "OpenAI TTS",
            "provider": "litellm",
            "model": "tts-1",
            "voice": "alloy",
            "api_key": None,
            "api_base": None,
            "enabled": False,
        }
    ],
    "stt_fallback_enabled": True,
    "tts_fallback_enabled": True,
    "max_voice_message_size": 10 * 1024 * 1024,
    "min_voice_interval_seconds": 10,
    "max_voice_duration_seconds": 120,
}


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


def build_tts_providers(config):
    """Build TTS provider instances from a Pydantic VoiceConfig object."""
    providers = []
    for p in config.tts_providers:
        if not p.enabled:
            continue
        if p.provider == "litellm":
            providers.append(
                LiteLLMTTSProvider(
                    name=p.name,
                    model=p.model,
                    voice=p.voice,
                    api_key=p.api_key,
                    api_base=p.api_base,
                )
            )
        elif p.provider == "local":
            providers.append(
                LocalTTSProvider(name=p.name, model=p.model, voice=p.voice)
            )
    return providers


# ── Functional API used by server.py ──


def load_voice_config() -> VoiceConfig:
    """Load the current voice config as a Pydantic VoiceConfig model."""
    repo = _get_repo()
    raw = repo.get_global_config(_GLOBAL_CONFIG_KEY)
    if raw is None:
        raw = copy.deepcopy(DEFAULT_VOICE_CONFIG_DICT)
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
    if agent_override.tts_fallback is not None:
        cfg["tts_fallback_enabled"] = agent_override.tts_fallback
    if agent_override.voice_input_disabled is True:
        cfg["enabled"] = False
    if agent_override.voice_output_disabled is True:
        # Clear TTS providers — no speech synthesis for this agent
        cfg["tts_providers"] = []

    return VoiceConfig(**cfg)
