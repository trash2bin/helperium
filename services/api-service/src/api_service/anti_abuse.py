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
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from api_service.prometheus_metrics import abuse_blocked_total


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
    # Curated bot markers are matched as case-insensitive SUBSTRINGS of the
    # header: "Mozilla/5.0 (compatible; curl/8.4.0)" must be blocked exactly
    # like a bare "curl/8.4.0". Markers are specific enough (all carry a
    # product suffix or distinctive token) that no real browser UA contains
    # them. Do not re-anchor these with '^'.
    blocked_user_agents: list[str] = field(
        default_factory=lambda: [
            r"curl/",
            r"wget/",
            r"python-requests",
            r"go-http-client",
            r"java/",
            r"libwww",
            r"lwp",
            r"www-mechanize",
            r"scrapy",
            r"python-urllib",
            r"axios/",
            r"postmanruntime",
        ]
    )
    block_empty_user_agent: bool = True

    # Emergency
    emergency_mode: bool = False
    emergency_preset: str = "normal"


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


def load_abuse_config() -> AbuseConfig:
    """Load AbuseConfig from environment variables (falling back to defaults)."""

    return AbuseConfig(
        rps=_float_env("ABUSE_RPS", 1.0),
        burst=_int_env("ABUSE_BURST", 5),
        max_message_length=_int_env("ABUSE_MAX_MSG_LENGTH", 2000),
        min_interval_ms=_int_env("ABUSE_MIN_INTERVAL_MS", 1000),
        max_user_turns_per_session=_int_env("ABUSE_MAX_USER_TURNS", 50),
        max_repeated_count=_int_env("ABUSE_MAX_REPEATED", 3),
    )


def load_ip_bucket_config() -> AbuseConfig:
    """Global per-IP budget (pentest H2): independent of session_id.

    Unlike per-session ABUSE_RPS/ABUSE_BURST, this bucket is shared across all
    sessions and agents for one client IP, so rotating session_id cannot mint
    fresh budgets. Only the rps/burst fields are meaningful.
    """
    return AbuseConfig(
        rps=_float_env("ABUSE_IP_RPS", 1.0),
        burst=_int_env("ABUSE_IP_BURST", 20),
    )


# ── Token Bucket Rate Limiter ──


class BucketBackend(Protocol):
    """Shared storage boundary for token buckets (Трек 3).

    One atomic consume per request. A backend must be process-safe: N workers
    share ONE bucket per key, so the total rate limit across workers equals the
    limit a single worker would enforce. The in-memory backend is the default
    (rollback by omitting the config flag); Redis is opt-in via env.
    """

    def consume(self, key: str, *, rps: float, burst: int) -> tuple[bool, float | None]:
        """Atomically consume one token.

        Returns ``(allowed, retry_after_seconds | None)``.
        """
        ...


class InMemoryBucketBackend:
    """Per-process token bucket storage (the pre-Track-3 behaviour).

    Uses ``time.monotonic()`` so the refill clock is immune to wall-clock
    jumps. Not shared across processes: with ``--workers N`` every worker keeps
    its own bucket, which over-admits by a factor of N. That is the honest
    trade-off of the default backend — and exactly why Redis exists as an
    opt-in.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, dict] = {}  # key -> {tokens, last_time, last_seen}
        self._lock = threading.Lock()
        self._inserts_since_scan = 0

    def consume(self, key: str, *, rps: float, burst: int) -> tuple[bool, float | None]:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = {
                    "tokens": float(burst),
                    "last_time": now,
                    "last_seen": now,
                }
                self._buckets[key] = bucket
                self._maybe_evict_locked(now)
            else:
                bucket["last_seen"] = now

            elapsed = now - bucket["last_time"]
            bucket["last_time"] = now

            bucket["tokens"] += elapsed * rps
            if bucket["tokens"] > burst:
                bucket["tokens"] = float(burst)

            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True, None

            deficit = 1.0 - bucket["tokens"]
            retry_after = deficit / rps if rps > 0 else 1.0
            return False, max(0.1, retry_after)

    def _maybe_evict_locked(self, now: float) -> None:
        """Lazy eviction, called on new-bucket inserts while holding the lock."""
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
            ordered = sorted(self._buckets.items(), key=lambda kv: kv[1]["last_seen"])
            excess = len(self._buckets) - TOKEN_BUCKET_MAX_ENTRIES
            for k, _ in ordered[:excess]:
                del self._buckets[k]

    def advance_time(self, key_prefix: str, ms: int) -> None:
        """Test helper: move the refill clock back by ``ms`` for matching keys."""
        delta = ms / 1000.0
        with self._lock:
            for key, bucket in self._buckets.items():
                if key.startswith(key_prefix):
                    bucket["last_time"] -= delta


class RedisBucketBackend:
    """Redis-backed token bucket, shared across processes.

    Atomicity without Lua: ``WATCH``/``MULTI``/``EXEC`` optimistic locking —
    the refill-and-decrement is a read-modify-write, and a conflicting writer
    aborts the transaction (``WatchError``), after which we retry. This is what
    keeps the sum limit at N workers equal to one worker, and it is testable
    with fakeredis (which implements WATCH/MULTI, no ``lupa`` needed).

    The clock is wall time (``time.time()``): every worker shares the same
    domain, and a backwards jump is clamped to zero elapsed so a NTP step does
    not mint extra tokens.
    """

    _TTL_SECONDS = 3600
    _MAX_RETRIES = 4

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _token_key(key: str) -> str:
        return f"abuse:tokens:{key}"

    @staticmethod
    def _last_key(key: str) -> str:
        return f"abuse:last:{key}"

    def consume(self, key: str, *, rps: float, burst: int) -> tuple[bool, float | None]:
        now = time.time()
        token_key = self._token_key(key)
        last_key = self._last_key(key)
        # Lazy import: redis is only needed when this backend is actually
        # selected, so the default in-memory path (and the whole module) never
        # requires the redis dependency to be installed.
        from redis.exceptions import WatchError

        for _ in range(self._MAX_RETRIES):
            try:
                with self._client.pipeline() as pipe:
                    pipe.watch(token_key, last_key)
                    raw_tokens = pipe.get(token_key)
                    raw_last = pipe.get(last_key)
                    tokens = (
                        float(raw_tokens) if raw_tokens is not None else float(burst)
                    )
                    last_time = float(raw_last) if raw_last is not None else now
                    elapsed = max(0.0, now - last_time)
                    tokens = min(float(burst), tokens + elapsed * rps)
                    if tokens >= 1.0:
                        tokens -= 1.0
                        allowed = True
                        retry_after = None
                    else:
                        allowed = False
                        retry_after = max(0.1, (1.0 - tokens) / rps) if rps > 0 else 1.0
                    pipe.multi()
                    pipe.set(token_key, tokens, ex=self._TTL_SECONDS)
                    pipe.set(last_key, now, ex=self._TTL_SECONDS)
                    pipe.execute()
                    return allowed, retry_after
            except WatchError:
                continue
        # Exhausted retries under contention: deny rather than over-admit. The
        # honest limit is the one that errs toward blocking, never toward
        # minting extra budget.
        return False, 1.0


def build_token_bucket_backend() -> BucketBackend:
    """Select the token-bucket storage backend from configuration (Трек 3).

    ``ABUSE_STORAGE_URI`` (a Redis URL, e.g. ``redis://127.0.0.1:6379/0``) opts
    into the shared Redis backend; unset/empty keeps the in-memory default, so
    rollback is by omission (no revert needed). The Redis client is created
    once here and shared by every bucket, so all buckets — global, per-IP,
    per-agent — see one store.
    """
    uri = os.environ.get("ABUSE_STORAGE_URI", "").strip()
    if not uri:
        return InMemoryBucketBackend()
    import redis

    return RedisBucketBackend(redis.Redis.from_url(uri))


class TokenBucket:
    """Per-session token bucket rate limiter.

    Each unique (session_id, ip, user_agent_hash) tuple gets its own bucket.
    Tokens refill at `config.rps` per second. Burst capacity = `config.burst`.
    Storage is delegated to a :class:`BucketBackend` so the backend can be
    swapped by configuration (in-memory default, Redis opt-in).
    """

    def __init__(
        self, config: AbuseConfig, backend: BucketBackend | None = None
    ) -> None:
        self.config = config
        self._backend: BucketBackend = backend or InMemoryBucketBackend()

    def _key(self, session_id: str, ip: str, user_agent: str) -> str:
        """Composite key: session + IP + UA hash prevents bypass via IP switching."""
        ua_hash = str(hash(user_agent) & 0xFFFFFFFF)
        return f"{session_id}:{ip}:{ua_hash}"

    def allow(self, session_id: str, ip: str, user_agent: str) -> tuple[bool, dict]:
        """Check if request is within rate limit.

        Returns (allowed, context) where context dict may contain 'retry_after'.
        """
        key = self._key(session_id, ip, user_agent)
        return self._allow_key(key)

    def allow_ip(self, ip: str) -> tuple[bool, dict]:
        """Global per-IP budget (pentest H2): keyed on IP only.

        Independent from allow() so session_id/user-agent rotation cannot mint
        fresh buckets. Shares the same eviction/cap machinery (key namespace
        "ip:<ip>"). Returns (allowed, context).
        """
        return self._allow_key(f"ip:{ip}")

    def _allow_key(self, key: str) -> tuple[bool, dict]:
        """Shared bucket logic for allow() and allow_ip()."""
        allowed, retry_after = self._backend.consume(
            key, rps=self.config.rps, burst=self.config.burst
        )
        if allowed:
            return True, {}
        return False, {"retry_after": retry_after if retry_after is not None else 1.0}

    def _advance_time(self, key_prefix: str, ms: int) -> None:
        """Test helper: advance time for all buckets matching key_prefix.

        Only meaningful for the in-memory backend (it owns the monotonic clock).
        Redis buckets are keyed on wall time; tests that need refill there wait
        for real time instead.
        """
        backend = self._backend
        if isinstance(backend, InMemoryBucketBackend):
            backend.advance_time(key_prefix, ms)


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
