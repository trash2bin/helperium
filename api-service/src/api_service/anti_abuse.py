#!/usr/bin/env python3
"""Anti-abuse engine for Agent Tutor embed widget chat API.

Provides:
- TokenBucket: per-session rate limiter (burst + sustained)
- AntiAbuseChecker: User-Agent validation, message length, interval, budget, repeated text
- load_abuse_config(): loads config from environment or returns defaults

Integration: used as middleware in server.py before chat handlers.
"""

from __future__ import annotations

import os
import re

from api_service.prometheus_metrics import abuse_blocked_total
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Config ──


@dataclass
class AbuseConfig:
    """Configuration for anti-abuse and rate limiting.

    All settings can be overridden via env vars.
    Per-agent overrides can be stored in AgentStore and applied at runtime.
    """

    # Token bucket — burst + sustained
    rps: float = 1.0  # tokens per second (sustained rate)
    burst: int = 5  # burst capacity

    # Anti-abuse checks
    max_message_length: int = 2000  # max chars per message
    min_interval_ms: int = 1000  # min ms between messages in session
    max_messages_per_session: int = 50  # max messages in a session
    max_repeated_count: int = 3  # repeated identical message threshold

    # User-Agent filtering
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
    block_empty_user_agent: bool = True


def load_abuse_config() -> AbuseConfig:
    """Load AbuseConfig from environment variables (falling back to defaults)."""

    def _int_env(key: str, default: int) -> int:
        v = os.environ.get(key)
        if v is not None:
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
        return default

    def _float_env(key: str, default: float) -> float:
        v = os.environ.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
        return default

    return AbuseConfig(
        rps=_float_env("ABUSE_RPS", 1.0),
        burst=_int_env("ABUSE_BURST", 5),
        max_message_length=_int_env("ABUSE_MAX_MSG_LENGTH", 2000),
        min_interval_ms=_int_env("ABUSE_MIN_INTERVAL_MS", 1000),
        max_messages_per_session=_int_env("ABUSE_MAX_MESSAGES", 50),
        max_repeated_count=_int_env("ABUSE_MAX_REPEATED", 3),
    )


# ── Token Bucket Rate Limiter ──


class TokenBucket:
    """Per-session token bucket rate limiter.

    Each unique (session_id, ip, user_agent_hash) tuple gets its own bucket.
    Tokens refill at `config.rps` per second. Burst capacity = `config.burst`.
    """

    def __init__(self, config: AbuseConfig) -> None:
        self.config = config
        self._buckets: dict[str, dict] = {}  # key -> {tokens, last_time}
        self._lock = threading.Lock()

    def _key(self, session_id: str, ip: str, user_agent: str) -> str:
        """Composite key: session + IP + UA hash prevents bypass via IP switching."""
        ua_hash = str(hash(user_agent) & 0xFFFFFFFF)
        return f"{session_id}:{ip}:{ua_hash}"

    def allow(self, session_id: str, ip: str, user_agent: str) -> tuple[bool, dict]:
        """Check if request is within rate limit.

        Returns (allowed, context) where context dict may contain 'retry_after'.
        """
        key = self._key(session_id, ip, user_agent)
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = {
                    "tokens": float(self.config.burst),
                    "last_time": now,
                }
                self._buckets[key] = bucket

            elapsed = now - bucket["last_time"]
            bucket["last_time"] = now

            # Refill tokens
            bucket["tokens"] += elapsed * self.config.rps
            if bucket["tokens"] > self.config.burst:
                bucket["tokens"] = float(self.config.burst)

            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True, {}

            # Calculate retry-after
            deficit = 1.0 - bucket["tokens"]
            retry_after = deficit / self.config.rps if self.config.rps > 0 else 1.0
            return False, {"retry_after": max(0.1, retry_after)}

    def _advance_time(self, key_prefix: str, ms: int) -> None:
        """Test helper: advance time for all buckets matching key_prefix.

        This moves last_time BACKWARD by `ms` milliseconds, effectively
        simulating that time has passed without using time.sleep().
        """
        delta = ms / 1000.0
        with self._lock:
            for key, bucket in self._buckets.items():
                if key.startswith(key_prefix):
                    bucket["last_time"] -= delta


# ── Anti-Abuse Checker ──


@dataclass
class CheckResult:
    """Result of an anti-abuse check."""

    allowed: bool
    reason: str = ""
    retry_after: Optional[float] = None  # seconds


class AntiAbuseChecker:
    """Checks request quality metrics.

    All checks are stateless except for tracking repeated messages per session.
    """

    def __init__(self, config: AbuseConfig) -> None:
        self.config = config
        self._ua_patterns = [
            re.compile(p, re.IGNORECASE) for p in config.blocked_user_agents
        ]
        self._recent_messages: dict[str, list[tuple[str, float]]] = {}
        self._lock = threading.Lock()

    def _check_user_agent(self, user_agent: str) -> Optional[str]:
        """Returns error reason if UA is blocked, None if OK."""
        if not user_agent and self.config.block_empty_user_agent:
            return "Empty or missing User-Agent header"
        if not user_agent:
            return None
        for pattern in self._ua_patterns:
            if pattern.search(user_agent):
                return f"Blocked User-Agent: {user_agent[:60]}"
        return None

    def _check_message_length(self, message: str) -> Optional[str]:
        if len(message) > self.config.max_message_length:
            return (
                f"Message too long ({len(message)} > {self.config.max_message_length})"
            )
        return None

    def _check_repeated_message(self, session_id: str, message: str) -> Optional[str]:
        """Check if this session has sent the same message too many times."""
        now = time.monotonic()
        with self._lock:
            msgs = self._recent_messages.get(session_id, [])
            # Clean old entries (> 5 minutes)
            cutoff = now - 300
            msgs = [(m, t) for m, t in msgs if t > cutoff]

            count = sum(1 for m, _ in msgs if m == message)

            if count >= self.config.max_repeated_count:
                return f"Repeated message detected ({count + 1} times)"

            msgs.append((message, now))
            self._recent_messages[session_id] = msgs
        return None

    def check(
        self,
        session_id: str,
        ip: str,
        user_agent: str,
        message: str,
        n_msg: int = 0,
        last_msg_time_since: Optional[float] = None,
    ) -> CheckResult:
        """Run all checks against this request.

        Args:
            session_id: Current chat session ID.
            ip: Client IP address.
            user_agent: User-Agent header value.
            message: The message text from the user.
            n_msg: Current message count in this session.
            last_msg_time_since: Seconds since the last message in this session.

        Returns:
            CheckResult with allowed=True/False and reason if blocked.
        """
        # 1. User-Agent check
        ua_reason = self._check_user_agent(user_agent)
        if ua_reason:
            abuse_blocked_total.labels(reason="user_agent").inc()
            return CheckResult(allowed=False, reason=ua_reason)

        # 2. Message length
        len_reason = self._check_message_length(message)
        if len_reason:
            abuse_blocked_total.labels(reason="message_length").inc()
            return CheckResult(allowed=False, reason=len_reason)

        # 3. Min interval between messages
        if last_msg_time_since is not None and last_msg_time_since < (
            self.config.min_interval_ms / 1000
        ):
            remaining = (self.config.min_interval_ms / 1000) - last_msg_time_since
            abuse_blocked_total.labels(reason="interval").inc()
            return CheckResult(
                allowed=False,
                reason=f"Min interval not met ({last_msg_time_since:.1f}s < {self.config.min_interval_ms / 1000:.1f}s)",
                retry_after=remaining,
            )

        # 4. Session message budget
        if n_msg >= self.config.max_messages_per_session:
            abuse_blocked_total.labels(reason="session_budget").inc()
            return CheckResult(
                allowed=False,
                reason=f"Session message budget exceeded ({n_msg} >= {self.config.max_messages_per_session})",
            )

        # 5. Repeated text detection
        repeat_reason = self._check_repeated_message(session_id, message)
        if repeat_reason:
            abuse_blocked_total.labels(reason="repeated_text").inc()
            return CheckResult(allowed=False, reason=repeat_reason)

        return CheckResult(allowed=True)

    def cleanup_old_sessions(self, max_age_seconds: int = 3600) -> None:
        """Periodic cleanup of stale session tracking data."""
        now = time.monotonic()
        cutoff = now - max_age_seconds
        with self._lock:
            stale = [
                sid
                for sid, msgs in self._recent_messages.items()
                if all(t < cutoff for _, t in msgs)
            ]
            for sid in stale:
                del self._recent_messages[sid]
