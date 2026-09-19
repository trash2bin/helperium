"""Tests for load/stress resilience of the abuse protection layer.

PENTEST-CHEK.md §3.2 (Load/stress testing) — no load testing infrastructure exists.
These tests verify that the per-IP and per-session rate limiters behave correctly
under burst attack patterns, preventing DoS via session rotation or payload flooding.

PENTEST-CHEK.md §2 (Load testing) — rate-limiting bypass (slowloris, multipart chunking),
DoS через большие payloads в RAG pipeline.
"""

from __future__ import annotations

import time

import pytest

from api_service.anti_abuse import AbuseConfig, AntiAbuseChecker, TokenBucket


@pytest.fixture
def checker():
    return AntiAbuseChecker(AbuseConfig())


@pytest.fixture
def tb():
    return TokenBucket(AbuseConfig(rps=1, burst=5))


# ── Session rotation bypass prevention ─────────────────────────────────


class TestSessionRotationAttack:
    """An attacker rotating session_id per request must not escape the per-IP budget.

    The per-session token bucket is keyed on (session_id, ip, user_agent), so a
    client that mints a fresh session_id per request gets a fresh per-session
    bucket every time. The global per-IP bucket is the defense.
    """

    def test_ip_budget_not_defeated_by_session_rotation(self):
        """Even with 100 fresh session_ids, the IP budget caps total requests."""
        ip_tb = TokenBucket(AbuseConfig(rps=0, burst=10))
        for i in range(10):
            # Each request gets a fresh session_id — would get a fresh
            # per-session bucket, but the IP bucket is shared.
            sess_ok, _ = ip_tb.allow(f"session-{i}", "203.0.113.7", "Browser/1.0")
            assert sess_ok  # per-session always allowed
            ip_ok, _ = ip_tb.allow_ip("203.0.113.7")
            assert ip_ok, f"request {i + 1} should be within IP burst"
        # 11th request with a brand-new session_id
        ip_ok, ctx = ip_tb.allow_ip("203.0.113.7")
        assert not ip_ok, "IP budget must block despite fresh session_id"
        assert "retry_after" in ctx

    def test_ip_budget_not_defeated_by_ua_rotation(self):
        """Rotating User-Agent with the same IP must not escape the IP budget."""
        ip_tb = TokenBucket(AbuseConfig(rps=0, burst=5))
        for i in range(5):
            sess_ok, _ = ip_tb.allow("session-1", "203.0.113.7", f"Browser/{i}")
            assert sess_ok
            ip_ok, _ = ip_tb.allow_ip("203.0.113.7")
            assert ip_ok
        # Fresh UA, same IP, new session: per-session bucket is fresh,
        # but the IP budget must still be spent.
        sess_ok, _ = ip_tb.allow("session-2", "203.0.113.7", "Browser-999")
        ip_ok, _ = ip_tb.allow_ip("203.0.113.7")
        assert sess_ok, "per-session should allow"
        assert not ip_ok, "IP budget must block despite UA rotation"


# ── Message length DoS prevention ───────────────────────────────────────


class TestPayloadSizeAttack:
    """Huge messages (megabyte-scale) must be rejected before LLM processing."""

    def test_extremely_long_message_is_rejected(self, checker):
        """A 50KB message must be rejected by the length cap."""
        result = checker.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "A" * 50_000, user_turn_count=1
        )
        assert not result.allowed
        assert "too long" in result.reason

    def test_message_at_exact_boundary_is_allowed(self, checker):
        """Message at exactly max_message_length is allowed (boundary)."""
        cfg = AbuseConfig(max_message_length=2000)
        chk = AntiAbuseChecker(cfg)
        result = chk.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "A" * 2000, user_turn_count=1
        )
        assert result.allowed, "boundary value (2000 chars) should be allowed"

    def test_message_one_over_boundary_is_rejected(self, checker):
        """Message 1 char over the limit is rejected."""
        cfg = AbuseConfig(max_message_length=2000)
        chk = AntiAbuseChecker(cfg)
        result = chk.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "A" * 2001, user_turn_count=1
        )
        assert not result.allowed
        assert "too long" in result.reason


# ── Rate-limiting: rapid fire burst ─────────────────────────────────────


class TestRapidFireBurst:
    """Rapid sequential messages must be rate-limited by the per-IP bucket."""

    def test_ip_burst_exhausted_then_rejected(self):
        ip_tb = TokenBucket(AbuseConfig(rps=0, burst=3))
        for i in range(3):
            ok, _ = ip_tb.allow_ip("203.0.113.7")
            assert ok, f"burst slot {i + 1} should be allowed"
        ok, ctx = ip_tb.allow_ip("203.0.113.7")
        assert not ok, "IP burst exhausted — 4th must be denied"
        assert ctx.get("retry_after", 0) > 0

    def test_ip_burst_replenishes_after_wait(self):
        ip_tb = TokenBucket(AbuseConfig(rps=1, burst=1))
        assert ip_tb.allow_ip("203.0.113.7")[0]
        assert not ip_tb.allow_ip("203.0.113.7")[0], "burst of 1 exhausted"
        ip_tb._advance_time("ip:203.0.113.7", 1500)  # +1.5s → 1.5 tokens
        assert ip_tb.allow_ip("203.0.113.7")[0], "replenished after 1.5s"
        assert not ip_tb.allow_ip("203.0.113.7")[0], "only 1 token replenished"

    def test_different_ips_have_independent_budgets(self):
        ip_tb = TokenBucket(AbuseConfig(rps=0, burst=2))
        # Exhaust IP A
        ip_tb.allow_ip("203.0.113.7")
        ip_tb.allow_ip("203.0.113.7")
        assert not ip_tb.allow_ip("203.0.113.7")[0]
        # IP B is unaffected
        assert ip_tb.allow_ip("203.0.113.8")[0]
        assert ip_tb.allow_ip("203.0.113.8")[0]
        assert not ip_tb.allow_ip("203.0.113.8")[0]


# ── Session user-turn quota prevents long abuse ─────────────────────────


class TestSessionTurnQuota:
    """max_user_turns_per_session caps total accepted turns per session."""

    def test_session_cannot_exceed_max_turns(self, checker):
        """After max_user_turns_per_session, all further messages are blocked."""
        cfg = AbuseConfig(max_user_turns_per_session=3)
        chk = AntiAbuseChecker(cfg)
        for i in range(3):
            result = chk.check(
                "sess-1", "10.0.0.1", "Mozilla/5.0", "msg", user_turn_count=i
            )
            assert result.allowed, f"turn {i + 1} should be allowed"
        result = chk.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "msg", user_turn_count=3
        )
        assert not result.allowed
        assert "quota" in result.reason

    def test_new_session_is_unaffected_by_other_session_quota(self, checker):
        """A fresh session starts with turn count 0, regardless of other sessions."""
        cfg = AbuseConfig(max_user_turns_per_session=2)
        chk = AntiAbuseChecker(cfg)
        # Exhaust session A
        chk.check("sess-a", "10.0.0.1", "Mozilla/5.0", "msg", user_turn_count=1)
        chk.check("sess-a", "10.0.0.1", "Mozilla/5.0", "msg", user_turn_count=2)
        # Session B is fresh
        result = chk.check(
            "sess-b", "10.0.0.1", "Mozilla/5.0", "msg", user_turn_count=0
        )
        assert result.allowed, "fresh session should start with 0 turns"


# ── Token bucket eviction prevents memory exhaustion ───────────────────


class TestTokenBucketEviction:
    """Attacker-influenced bucket maps must not grow without bound.

    Session IDs and IPs come from request data. Without eviction, an attacker
    sending random session_ids could exhaust server memory.
    """

    def test_eviction_under_many_unique_ips(self):
        from api_service.anti_abuse import (
            TOKEN_BUCKET_MAX_ENTRIES,
        )

        cfg = AbuseConfig(rps=0, burst=1)
        tb = TokenBucket(cfg)

        # Fill beyond the cap
        for i in range(TOKEN_BUCKET_MAX_ENTRIES + 100):
            tb.allow_ip(f"10.0.0.{i % 255}")

        # Must not have grown unbounded
        bucket_count = len(tb._buckets)
        assert bucket_count <= TOKEN_BUCKET_MAX_ENTRIES, (
            f"Token bucket map grew to {bucket_count}, cap is {TOKEN_BUCKET_MAX_ENTRIES}"
        )

    def test_idle_buckets_are_eventually_evicted(self):
        """Test that idle buckets are evicted after TOKEN_BUCKET_IDLE_SECONDS.

        The eviction runs lazily on insert. We create an idle bucket,
        advance its last_seen to be old, then trigger an eviction scan.
        """
        from api_service.anti_abuse import (
            TOKEN_BUCKET_IDLE_SECONDS,
            TOKEN_BUCKET_EVICT_SCAN_INTERVAL,
        )

        cfg = AbuseConfig(rps=0, burst=1)
        tb = TokenBucket(cfg)
        # Create a bucket
        tb.allow_ip("10.0.0.1")
        assert "ip:10.0.0.1" in tb._buckets

        # Directly set last_seen to be old (simulating passage of time)
        # The eviction checks if last_seen < now - IDLE_SECONDS
        old_time = time.monotonic() - TOKEN_BUCKET_IDLE_SECONDS - 10
        with tb._lock:
            tb._buckets["ip:10.0.0.1"]["last_seen"] = old_time

        # Force eviction scan by creating enough new entries
        for i in range(TOKEN_BUCKET_EVICT_SCAN_INTERVAL + 1):
            tb.allow_ip(f"10.0.1.{i % 255}")

        # The old bucket should now be evicted
        assert "ip:10.0.0.1" not in tb._buckets, "Idle bucket should be evicted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
