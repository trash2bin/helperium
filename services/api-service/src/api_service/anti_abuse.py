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


# Eviction bounds for attacker-influenced in-memory state. Session IDs and
# IPs arrive from request data, so the per-key maps below would otherwise
# grow without limit under crafted traffic. These are module constants, not
# AbuseConfig fields: AbuseConfig is an extra=forbid DTO contract.
TOKEN_BUCKET_MAX_ENTRIES = 4096
TOKEN_BUCKET_IDLE_SECONDS = 3600
TOKEN_BUCKET_EVICT_SCAN_INTERVAL = 128
RECENT_MESSAGES_MAX_SESSIONS = 4096
RECENT_MESSAGE_WINDOW_SECONDS = 300

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
    max_user_turns_per_session: int = 50  # accepted user turns in a session
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

    # Emergency
    emergency_mode: bool = False
    emergency_preset: str = "normal"


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
        max_user_turns_per_session=_int_env("ABUSE_MAX_USER_TURNS", 50),
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
        self._buckets: dict[str, dict] = {}  # key -> {tokens, last_time, last_seen}
        self._lock = threading.Lock()
        self._inserts_since_scan = 0

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
                    "last_seen": now,
                }
                self._buckets[key] = bucket
                self._maybe_evict_locked(now)
            else:
                bucket["last_seen"] = now

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

    def _maybe_evict_locked(self, now: float) -> None:
        """Lazy eviction, called on new-bucket inserts while holding the lock.

        Idle eviction runs at most once per TOKEN_BUCKET_EVICT_SCAN_INTERVAL
        inserts. The hard cap is enforced on every insert that would exceed
        it (O(n) over the map only when the cap is actually crossed).
        """
        self._inserts_since_scan += 1
        if self._inserts_since_scan >= TOKEN_BUCKET_EVICT_SCAN_INTERVAL:
            self._inserts_since_scan = 0
            idle_cutoff = now - TOKEN_BUCKET_IDLE_SECONDS
            stale = [
                k for k, b in self._buckets.items() if b["last_seen"] < idle_cutoff
            ]
            for k in stale:
                del self._buckets[k]

        if len(self._buckets) > TOKEN_BUCKET_MAX_ENTRIES:
            # Drop least-recently-seen buckets until back under the cap.
            ordered = sorted(self._buckets.items(), key=lambda kv: kv[1]["last_seen"])
            excess = len(self._buckets) - TOKEN_BUCKET_MAX_ENTRIES
            for k, _ in ordered[:excess]:
                del self._buckets[k]

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
    retry_after: float | None = None  # seconds


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

    def _prune_session_locked(
        self, session_id: str, now: float
    ) -> list[tuple[str, float]]:
        """Drop messages outside the window for one session.

        Callers must hold the lock. Entries older than the repeated-message
        window are removed; sessions whose list becomes empty are dropped
        from the map entirely. The hard cap evicts sessions whose newest
        message is oldest.
        """
        cutoff = now - RECENT_MESSAGE_WINDOW_SECONDS
        msgs = [
            (m, t) for m, t in self._recent_messages.get(session_id, []) if t > cutoff
        ]
        if msgs:
            self._recent_messages[session_id] = msgs
        else:
            self._recent_messages.pop(session_id, None)
        return msgs

    def _maybe_evict_sessions_locked(self, session_id: str, now: float) -> None:
        """Hard-cap eviction for the recent-messages map, after an append.

        Callers must hold the lock. Sessions are evicted by newest stored
        timestamp (least-recently-active first) until back under the cap;
        the session being served always survives.
        """
        if len(self._recent_messages) <= RECENT_MESSAGES_MAX_SESSIONS:
            return
        protected = self._recent_messages.get(session_id)
        ordered = sorted(
            (
                (k, max(t for _, t in v))
                for k, v in self._recent_messages.items()
                if k != session_id
            ),
            key=lambda kv: kv[1],
        )
        excess = len(self._recent_messages) - RECENT_MESSAGES_MAX_SESSIONS
        for k, _ in ordered[:excess]:
            del self._recent_messages[k]
        if protected is not None:
            self._recent_messages[session_id] = protected

    def _check_user_agent(self, user_agent: str) -> str | None:
        """Returns error reason if UA is blocked, None if OK."""
        if not user_agent and self.config.block_empty_user_agent:
            return "Empty or missing User-Agent header"
        if not user_agent:
            return None
        for pattern in self._ua_patterns:
            if pattern.search(user_agent):
                return f"Blocked User-Agent: {user_agent[:60]}"
        return None

    def _check_message_length(self, message: str) -> str | None:
        if len(message) > self.config.max_message_length:
            return (
                f"Message too long ({len(message)} > {self.config.max_message_length})"
            )
        return None

    def _check_repeated_message(self, session_id: str, message: str) -> str | None:
        """Check if this session has sent the same message too many times."""
        now = time.monotonic()
        with self._lock:
            msgs = self._prune_session_locked(session_id, now)

            count = sum(1 for m, _ in msgs if m == message)

            if count >= self.config.max_repeated_count:
                return f"Repeated message detected ({count + 1} times)"

            msgs.append((message, now))
            self._recent_messages[session_id] = msgs
            self._maybe_evict_sessions_locked(session_id, now)
        return None

    def check(
        self,
        session_id: str,
        ip: str,
        user_agent: str,
        message: str,
        user_turn_count: int = 0,
        last_msg_time_since: float | None = None,
    ) -> CheckResult:
        """Run all checks against this request.

        Args:
            session_id: Current chat session ID.
            ip: Client IP address.
            user_agent: User-Agent header value.
            message: The message text from the user.
            user_turn_count: Accepted user turns already consumed in this session.
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

        # 4. Session user-turn quota
        if user_turn_count >= self.config.max_user_turns_per_session:
            abuse_blocked_total.labels(reason="user_turn_quota").inc()
            return CheckResult(
                allowed=False,
                reason=(
                    "Session user-turn quota exceeded "
                    f"({user_turn_count} >= {self.config.max_user_turns_per_session})"
                ),
            )

        # 5. Repeated text detection
        repeat_reason = self._check_repeated_message(session_id, message)
        if repeat_reason:
            abuse_blocked_total.labels(reason="repeated_text").inc()
            return CheckResult(allowed=False, reason=repeat_reason)

        return CheckResult(allowed=True)
