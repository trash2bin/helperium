"""Voice configuration storage (STT/TTS providers).

Provides functional API for loading/saving/resolving voice config
as Pydantic VoiceConfig models.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from threading import Lock

from helperium_sdk.api.models import VoiceAgentConfig, VoiceConfig
from api_service.audio.stt_engine import LiteLLMSTTProvider, LocalSTTProvider
from api_service.audio.tts_engine import LiteLLMTTSProvider, LocalTTSProvider

logger = logging.getLogger(__name__)

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


class VoiceConfigStore:
    """Thread-safe JSON-backed voice configuration store."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            try:
                from helperium_sdk.settings import settings

                db_path = str(
                    Path(settings.session_db_path).parent / "voice_config.json"
                )
            except Exception:
                db_path = str(
                    Path(
                        os.environ.get("SESSION_DB_PATH", ".data/sessions/sessions.db")
                    ).parent
                    / "voice_config.json"
                )
        self._path = db_path
        self._lock = Lock()
        self._config = self._load()

    def _load(self) -> dict:
        try:
            if os.path.exists(self._path):
                with open(self._path) as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning("Failed to load voice config from %s: %s", self._path, exc)
        return copy.deepcopy(DEFAULT_VOICE_CONFIG_DICT)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def get(self) -> dict:
        """Return a copy of the current voice config as a plain dict."""
        with self._lock:
            return copy.deepcopy(self._config)

    def update(self, config: dict) -> None:
        """Replace the entire voice config and persist."""
        with self._lock:
            self._config = config
            self._save()


# Singleton
_voice_config_store: VoiceConfigStore | None = None
_voice_config_lock = Lock()


def get_voice_config_store() -> VoiceConfigStore:
    """Get or create the singleton VoiceConfigStore."""
    global _voice_config_store
    if _voice_config_store is None:
        with _voice_config_lock:
            if _voice_config_store is None:
                _voice_config_store = VoiceConfigStore()
    return _voice_config_store


# ── Provider builders ──


def build_stt_providers(config):
    """Build STT provider instances from a Pydantic VoiceConfig object."""
    providers = []
    for p in config.stt_providers:
        if not p.enabled:
            continue
        if p.provider == "litellm":
            providers.append(
                LiteLLMSTTProvider(
                    name=p.name, model=p.model, api_key=p.api_key, api_base=p.api_base
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
    store = get_voice_config_store()
    raw = store.get()
    return VoiceConfig(**raw)


def save_voice_config(config: VoiceConfig) -> None:
    """Persist a VoiceConfig model to the store."""
    store = get_voice_config_store()
    store.update(config.model_dump(mode="json"))


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
