"""Live abuse config provider — connects admin-dashboard config to runtime.

Reads JSON from shared file (written by admin-dashboard), falls back to env vars.
Supports per-agent config merging and live reload via API.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from helperium_sdk.settings import settings
from .anti_abuse import AbuseConfig, AntiAbuseChecker, TokenBucket, load_abuse_config

logger = logging.getLogger("api_service.abuse_live")

DEFAULT_CONFIG_PATH = ".data/uploads/abuse_config.json"

# Fields on FullAbuseConfig that are NOT AbuseConfig fields and NOT internal.
_FAC_OWN_KEYS = frozenset(
    {
        "history_turns",
        "history_content_chars",
        "max_iterations",
        "max_empty_rounds",
        "max_turn_tokens",
        "session_ttl_hours",
    }
)


@dataclass
class FullAbuseConfig:
    """Complete config: abuse enforcement (via composition) + runtime settings."""

    # Abuse enforcement — composed, not duplicated
    base: AbuseConfig = field(default_factory=AbuseConfig)

    # Runtime settings (FullAbuseConfig's own fields)
    history_turns: int = 8
    history_content_chars: int = 6000
    max_iterations: int = 5
    max_empty_rounds: int = 3
    max_turn_tokens: int = 8000
    session_ttl_hours: int = 0

    # Per-agent overrides (loaded separately)
    _agent_overrides: dict[str, dict] = field(default_factory=dict, repr=False)

    def to_anti_abuse_config(self) -> AbuseConfig:
        """Return the composed AbuseConfig for AntiAbuseChecker / TokenBucket."""
        return self.base


def _apply_json(cfg: FullAbuseConfig, data: dict) -> None:
    """Merge JSON dict into config (only non-null values)."""
    # Apply AbuseConfig fields to cfg.base
    for key in cfg.base.__dataclass_fields__:
        if key in data and data[key] is not None:
            if isinstance(data[key], list):
                setattr(cfg.base, key, list(data[key]))
            else:
                setattr(cfg.base, key, data[key])

    # Apply FullAbuseConfig's own runtime fields
    for key in _FAC_OWN_KEYS:
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])


def _serialize_config(cfg: FullAbuseConfig) -> dict:
    """Serialize to flat JSON-compatible dict (matches admin-dashboard's format)."""
    result: dict = {}
    # AbuseConfig fields from cfg.base
    for key in cfg.base.__dataclass_fields__:
        val = getattr(cfg.base, key)
        result[key] = list(val) if isinstance(val, list) else val
    # FullAbuseConfig's own runtime fields
    for key in _FAC_OWN_KEYS:
        result[key] = getattr(cfg, key)
    return result


class LiveAbuseProvider:
    """Singleton — loads abuse config from JSON (written by admin-dashboard).

    Flow:
      1. admin-dashboard saves config to .data/uploads/abuse_config.json
      2. LiveAbuseProvider reads same file
      3. On change, admin-dashboard calls POST /admin/abuse-config/reload
      4. Chat handlers use AntiAbuseChecker with the latest config
    """

    _instance: LiveAbuseProvider | None = None
    _lock = threading.Lock()

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or os.environ.get(
            "ABUSE_CONFIG_PATH",
            str(
                Path(os.environ.get("DATA_DIR", ".data/uploads")) / "abuse_config.json"
            ),
        )
        self._full_config = self._load()
        self._anti_abuse_checker = AntiAbuseChecker(
            self._full_config.to_anti_abuse_config()
        )
        self._token_bucket = TokenBucket(self._full_config.to_anti_abuse_config())
        self._agent_enforcers: dict[str, tuple[AntiAbuseChecker, TokenBucket]] = {}
        self._rwlock = threading.RLock()

    @classmethod
    def get_instance(cls, config_path: str | None = None) -> LiveAbuseProvider:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    def _load(self) -> FullAbuseConfig:
        """Load from JSON file with env var fallback."""
        cfg = FullAbuseConfig()

        # Start from env-based defaults (backward compat)
        cfg.base = load_abuse_config()

        # Runtime from env (backward compat)
        cfg.history_turns = int(os.environ.get("DEMO_HISTORY_TURNS", "8"))
        cfg.history_content_chars = int(
            os.environ.get("DEMO_HISTORY_CONTENT_CHARS", "6000")
        )
        cfg.max_iterations = settings.agent_max_iterations
        cfg.max_empty_rounds = settings.agent_max_empty_rounds
        cfg.max_turn_tokens = settings.agent_max_turn_tokens

        # Overlay JSON file (admin-dashboard's output)
        path = Path(self._config_path)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                _apply_json(cfg, data)
                logger.info("Loaded abuse config from %s", path)
            except Exception as exc:
                logger.warning("Failed to load abuse config from %s: %s", path, exc)

        return cfg

    # ── Public API ──

    def get_config(self) -> FullAbuseConfig:
        """Get the current full config (thread-safe)."""
        with self._rwlock:
            return deepcopy(self._full_config)

    def get_anti_abuse_checker(self) -> AntiAbuseChecker:
        """Get the current AntiAbuseChecker instance."""
        with self._rwlock:
            return self._anti_abuse_checker

    def get_token_bucket(self) -> TokenBucket:
        """Get the current TokenBucket instance."""
        with self._rwlock:
            return self._token_bucket

    def get_enforcers(
        self, agent_abuse_config: dict | None = None
    ) -> tuple[AntiAbuseChecker, TokenBucket]:
        """Return persistent checker and bucket for global or agent policy."""
        if not agent_abuse_config:
            return self.get_anti_abuse_checker(), self.get_token_bucket()
        key = json.dumps(agent_abuse_config, sort_keys=True, separators=(",", ":"))
        with self._rwlock:
            enforcers = self._agent_enforcers.get(key)
            if enforcers is None:
                anti_cfg = self.get_effective_config(
                    agent_abuse_config
                ).to_anti_abuse_config()
                enforcers = (AntiAbuseChecker(anti_cfg), TokenBucket(anti_cfg))
                self._agent_enforcers[key] = enforcers
            return enforcers

    def reload(self) -> FullAbuseConfig:
        """Reload config from disk and recreate checker/bucket (thread-safe)."""
        with self._rwlock:
            self._full_config = self._load()
            anti_cfg = self._full_config.to_anti_abuse_config()
            self._anti_abuse_checker = AntiAbuseChecker(anti_cfg)
            self._token_bucket = TokenBucket(anti_cfg)
            self._agent_enforcers = {}
            logger.info("Abuse config reloaded from %s", self._config_path)
        return self.get_config()

    def get_effective_config(
        self, agent_abuse_config: dict | None = None
    ) -> FullAbuseConfig:
        """Merge global config with per-agent overrides.

        Args:
            agent_abuse_config: Dict from AgentStore's abuse_config field.

        Returns:
            A new FullAbuseConfig with agent overrides applied on top of global.
        """
        cfg = self.get_config()
        if not agent_abuse_config:
            return cfg

        merged = deepcopy(cfg)
        _apply_json(merged, agent_abuse_config)

        return merged

    def apply_runtime_settings(self) -> None:
        """Apply runtime settings to global ``demo.settings.settings`` object.

        This lets existing code that reads ``settings.history_turns`` etc.
        benefit from the new config without changes.
        """
        from helperium_sdk.settings import settings as live_settings

        cfg = self.get_config()

        # Session history
        live_settings.history_turns = cfg.history_turns
        live_settings.history_content_chars = cfg.history_content_chars

        # Agent loop
        live_settings.agent_max_iterations = cfg.max_iterations
        live_settings.agent_max_empty_rounds = cfg.max_empty_rounds
        live_settings.agent_max_turn_tokens = cfg.max_turn_tokens

    # ── Config file write (for sync with admin-dashboard) ──

    def save_config(self, data: dict) -> FullAbuseConfig:
        """Save new config to file (as if admin-dashboard wrote it).

        Used when config is changed through api-service's own admin API.
        """
        cfg = self.get_config()
        _apply_json(cfg, data)
        # Write to file
        path = Path(self._config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_serialize_config(cfg), indent=2, ensure_ascii=False)
        )
        # Reload (recreates checker/bucket)
        return self.reload()


# ── Global singleton shortcut ──


def get_live_abuse_provider() -> LiveAbuseProvider:
    return LiveAbuseProvider.get_instance()


def get_token_bucket() -> TokenBucket:
    return get_live_abuse_provider().get_token_bucket()
