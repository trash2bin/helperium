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
from .anti_abuse import AbuseConfig as AntiAbuseConfig
from .anti_abuse import AntiAbuseChecker, TokenBucket, load_abuse_config

logger = logging.getLogger("api_service.abuse_live")

DEFAULT_CONFIG_PATH = ".data/uploads/abuse_config.json"


@dataclass
class RuntimeSettings:
    """Runtime agent behaviour settings — not abuse enforcement, but agent loop params.

    These are stored alongside abuse config for convenience (one file to manage).
    """

    # Session history
    history_turns: int = 8
    history_content_chars: int = 6000

    # Agent loop limits
    max_iterations: int = 5
    max_empty_rounds: int = 3
    max_turn_tokens: int = 8000

    # Session TTL
    session_ttl_hours: int = 0  # 0 = forever

    # Spending
    token_budget: int = 0  # 0 = unlimited

    # Emergency
    emergency_mode: bool = False
    emergency_preset: str = "normal"


@dataclass
class FullAbuseConfig:
    """Complete config: abuse enforcement + runtime settings + per-agent overrides."""

    # Abuse enforcement (maps to AntiAbuseConfig)
    rps: float = 1.0
    burst: int = 5
    max_message_length: int = 2000
    min_interval_ms: int = 1000
    max_messages_per_session: int = 50
    max_repeated_count: int = 3
    block_empty_user_agent: bool = True
    blocked_user_agents: list[str] = field(
        default_factory=lambda: [
            r"^curl/",
            r"^wget/",
            r"^python-requests",
            r"^Go-http-client",
            r"^Java/",
            r"^libwww",
            r"^LWP",
            r"^WWW-Mechanize",
            r"^scrapy",
            r"^Python-urllib",
            r"^axios/",
            r"^PostmanRuntime",
        ]
    )

    # Runtime settings
    history_turns: int = 8
    history_content_chars: int = 6000
    max_iterations: int = 5
    max_empty_rounds: int = 3
    max_turn_tokens: int = 8000
    session_ttl_hours: int = 0
    token_budget: int = 0
    emergency_mode: bool = False
    emergency_preset: str = "normal"

    # Per-agent overrides (loaded separately)
    _agent_overrides: dict[str, dict] = field(default_factory=dict, repr=False)

    def to_anti_abuse_config(self) -> AntiAbuseConfig:
        """Convert to the format used by AntiAbuseChecker."""
        return AntiAbuseConfig(
            rps=self.rps,
            burst=self.burst,
            max_message_length=self.max_message_length,
            min_interval_ms=self.min_interval_ms,
            max_messages_per_session=self.max_messages_per_session,
            max_repeated_count=self.max_repeated_count,
            blocked_user_agents=list(self.blocked_user_agents),
            block_empty_user_agent=self.block_empty_user_agent,
        )


def _apply_json(cfg: FullAbuseConfig, data: dict) -> None:
    """Merge JSON dict into config (only non-null values)."""
    for key in (
        "rps",
        "burst",
        "max_message_length",
        "min_interval_ms",
        "max_messages_per_session",
        "max_repeated_count",
        "block_empty_user_agent",
        "history_turns",
        "history_content_chars",
        "max_iterations",
        "max_empty_rounds",
        "max_turn_tokens",
        "session_ttl_hours",
        "token_budget",
        "emergency_mode",
        "emergency_preset",
    ):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    if "blocked_user_agents" in data and isinstance(data["blocked_user_agents"], list):
        cfg.blocked_user_agents = list(data["blocked_user_agents"])


def _serialize_config(cfg: FullAbuseConfig) -> dict:
    """Serialize to JSON-compatible dict (matches admin-dashboard's format)."""
    return {
        "rps": cfg.rps,
        "burst": cfg.burst,
        "max_message_length": cfg.max_message_length,
        "min_interval_ms": cfg.min_interval_ms,
        "max_messages_per_session": cfg.max_messages_per_session,
        "max_repeated_count": cfg.max_repeated_count,
        "block_empty_user_agent": cfg.block_empty_user_agent,
        "blocked_user_agents": list(cfg.blocked_user_agents),
        "history_turns": cfg.history_turns,
        "history_content_chars": cfg.history_content_chars,
        "max_iterations": cfg.max_iterations,
        "max_empty_rounds": cfg.max_empty_rounds,
        "max_turn_tokens": cfg.max_turn_tokens,
        "session_ttl_hours": cfg.session_ttl_hours,
        "token_budget": cfg.token_budget,
        "emergency_mode": cfg.emergency_mode,
        "emergency_preset": cfg.emergency_preset,
    }


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
        env_cfg = load_abuse_config()
        cfg.rps = env_cfg.rps
        cfg.burst = env_cfg.burst
        cfg.max_message_length = env_cfg.max_message_length
        cfg.min_interval_ms = env_cfg.min_interval_ms
        cfg.max_messages_per_session = env_cfg.max_messages_per_session
        cfg.max_repeated_count = env_cfg.max_repeated_count
        cfg.block_empty_user_agent = env_cfg.block_empty_user_agent
        cfg.blocked_user_agents = list(env_cfg.blocked_user_agents)

        # Runtime from env (backward compat)
        cfg.history_turns = int(os.environ.get("DEMO_HISTORY_TURNS", "8"))
        cfg.history_content_chars = int(
            os.environ.get("DEMO_HISTORY_CONTENT_CHARS", "6000")
        )
        cfg.max_iterations = int(os.environ.get("AGENT_MAX_ITERATIONS", "5"))
        cfg.max_empty_rounds = int(os.environ.get("AGENT_MAX_EMPTY_ROUNDS", "3"))
        cfg.max_turn_tokens = int(os.environ.get("AGENT_MAX_TURN_TOKENS", "8000"))

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

    def reload(self) -> FullAbuseConfig:
        """Reload config from disk and recreate checker/bucket (thread-safe)."""
        with self._rwlock:
            self._full_config = self._load()
            anti_cfg = self._full_config.to_anti_abuse_config()
            self._anti_abuse_checker = AntiAbuseChecker(anti_cfg)
            self._token_bucket = TokenBucket(anti_cfg)
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
