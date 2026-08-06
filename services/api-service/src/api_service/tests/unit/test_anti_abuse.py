"""Tests for anti-abuse engine — rate limiting + abuse detection."""

from __future__ import annotations

import os
import time

import pytest

from api_service.anti_abuse import (
    AbuseConfig,
    AntiAbuseChecker,
    TokenBucket,
    load_abuse_config,
)


# ════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════


@pytest.fixture
def cfg() -> AbuseConfig:
    return AbuseConfig(rps=1, burst=5)


@pytest.fixture
def tb(cfg: AbuseConfig) -> TokenBucket:
    return TokenBucket(cfg)


@pytest.fixture
def checker() -> AntiAbuseChecker:
    return AntiAbuseChecker(AbuseConfig())


# ════════════════════════════════════════════════════════════════
# Token bucket tests
# ════════════════════════════════════════════════════════════════


class TestTokenBucket:
    def test_accepts_within_burst(self, tb: TokenBucket):
        for i in range(5):
            ok, _ = tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
            assert ok, f"request {i + 1} should be allowed (within burst)"

    def test_blocks_excess(self, tb: TokenBucket):
        for i in range(5):
            tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        ok, ctx = tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        assert not ok
        assert "retry_after" in ctx

    def test_per_session_isolation(self, tb: TokenBucket):
        for i in range(5):
            tb.allow("sess-a", "10.0.0.1", "Mozilla/5.0")
        ok_a, _ = tb.allow("sess-a", "10.0.0.1", "Mozilla/5.0")
        assert not ok_a  # sess-a exhausted
        ok_b, _ = tb.allow("sess-b", "10.0.0.1", "Mozilla/5.0")
        assert ok_b  # sess-b still has burst

    def test_replenishes(self, tb: TokenBucket):
        for i in range(5):
            tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        # Advance time by 2 seconds → ~2 tokens at 1 rps
        tb._advance_time("sess-1", 2000)
        assert tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")[0]
        assert tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")[0]
        ok, _ = tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        assert not ok  # burst of 2 replenished, 3rd blocked

    def test_key_includes_ip_and_ua(self, tb: TokenBucket):
        """Different IPs with same session get different buckets."""
        for i in range(5):
            tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        ok, _ = tb.allow("sess-1", "10.0.0.1", "Mozilla/5.0")
        assert not ok  # exhausted
        ok2, _ = tb.allow("sess-1", "10.0.0.2", "Mozilla/5.0")
        assert ok2  # different IP → new bucket


# ════════════════════════════════════════════════════════════════
# Anti-abuse checker tests
# ════════════════════════════════════════════════════════════════


class TestAntiAbuseChecker:
    def test_rejects_empty_user_agent(self, checker: AntiAbuseChecker):
        result = checker.check("sess-1", "10.0.0.1", "", "hello", n_msg=1)
        assert not result.allowed
        assert "User-Agent" in result.reason

    def test_rejects_curl_user_agent(self, checker: AntiAbuseChecker):
        result = checker.check("sess-1", "10.0.0.1", "curl/8.0", "hello", n_msg=1)
        assert not result.allowed

    def test_rejects_python_requests(self, checker: AntiAbuseChecker):
        result = checker.check(
            "sess-1", "10.0.0.1", "python-requests/2.31", "hello", n_msg=1
        )
        assert not result.allowed

    def test_accepts_browser_ua(self, checker: AntiAbuseChecker):
        result = checker.check(
            "sess-1",
            "10.0.0.1",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36",
            "hello",
            n_msg=1,
        )
        assert result.allowed

    def test_message_too_long(self, checker: AntiAbuseChecker):
        result = checker.check("sess-1", "10.0.0.1", "Mozilla/5.0", "A" * 2001, n_msg=1)
        assert not result.allowed
        assert "too long" in result.reason

    def test_repeated_text_blocked(self, checker: AntiAbuseChecker):
        # Call check 4 times with same message — 4th should be blocked
        for i in range(3):
            result = checker.check(
                "sess-1", "10.0.0.1", "Mozilla/5.0", "hello", n_msg=1
            )
            assert result.allowed, f"call {i + 1} should be allowed"
        result = checker.check("sess-1", "10.0.0.1", "Mozilla/5.0", "hello", n_msg=1)
        assert not result.allowed  # 4th same message blocked
        assert "Repeated" in result.reason

    def test_min_interval_enforced(self, checker: AntiAbuseChecker):
        result1 = checker.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "hi", n_msg=1, last_msg_time_since=0.5
        )
        assert not result1.allowed  # less than 1 second
        result2 = checker.check(
            "sess-1", "10.0.0.1", "Mozilla/5.0", "hi", n_msg=1, last_msg_time_since=1.5
        )
        assert result2.allowed  # more than 1 second

    def test_session_budget_exceeded(self, checker: AntiAbuseChecker):
        result = checker.check("sess-1", "10.0.0.1", "Mozilla/5.0", "hello", n_msg=51)
        assert not result.allowed
        assert "budget" in result.reason

    def test_checker_rejects_go_http_client(self, checker: AntiAbuseChecker):
        result = checker.check(
            "sess-1", "10.0.0.1", "Go-http-client/2.0", "hello", n_msg=1
        )
        assert not result.allowed

    def test_checker_rejects_wget(self, checker: AntiAbuseChecker):
        result = checker.check("sess-1", "10.0.0.1", "Wget/1.21.4", "hello", n_msg=1)
        assert not result.allowed


# ════════════════════════════════════════════════════════════════
# Config loading tests
# ════════════════════════════════════════════════════════════════


class TestAbuseConfig:
    def test_config_from_env(self):
        os.environ["ABUSE_BURST"] = "10"
        os.environ["ABUSE_RPS"] = "2"
        os.environ["ABUSE_MAX_MSG_LENGTH"] = "1000"
        os.environ["ABUSE_MAX_MESSAGES"] = "30"
        os.environ["ABUSE_MIN_INTERVAL_MS"] = "500"
        os.environ["ABUSE_MAX_REPEATED"] = "5"
        try:
            cfg = load_abuse_config()
            assert cfg.burst == 10
            assert cfg.rps == 2
            assert cfg.max_message_length == 1000
            assert cfg.max_messages_per_session == 30
            assert cfg.min_interval_ms == 500
            assert cfg.max_repeated_count == 5
        finally:
            for k in (
                "ABUSE_BURST",
                "ABUSE_RPS",
                "ABUSE_MAX_MSG_LENGTH",
                "ABUSE_MAX_MESSAGES",
                "ABUSE_MIN_INTERVAL_MS",
                "ABUSE_MAX_REPEATED",
            ):
                os.environ.pop(k, None)

    def test_config_defaults(self):
        cfg = load_abuse_config()
        assert cfg.rps == 1.0
        assert cfg.burst == 5
        assert cfg.max_message_length == 2000
        assert cfg.max_messages_per_session == 50
        assert cfg.min_interval_ms == 1000
        assert cfg.max_repeated_count == 3
        assert cfg.block_empty_user_agent is True
        assert len(cfg.blocked_user_agents) == 12

    def test_config_invalid_env_falls_back_to_default(self):
        os.environ["ABUSE_BURST"] = "not-a-number"
        try:
            cfg = load_abuse_config()
            assert cfg.burst == 5  # default
        finally:
            os.environ.pop("ABUSE_BURST", None)


# ════════════════════════════════════════════════════════════════
# Benchmark test — no real API tokens required
# ════════════════════════════════════════════════════════════════


class TestTokenBucketBenchmark:
    def test_token_bucket_throughput(self):
        """Simulate 1000 concurrent sessions, measure throughput in ms."""
        cfg = AbuseConfig(rps=10, burst=20)
        tb = TokenBucket(cfg)
        sessions = [f"sess-{i}" for i in range(1000)]

        start = time.monotonic()
        allowed = 0
        blocked = 0
        # Simulate 3 bursts across 1000 sessions
        for _round in range(3):
            for i, session_id in enumerate(sessions):
                ip = f"10.0.0.{i % 255}"
                ok, _ = tb.allow(session_id, ip, "Mozilla/5.0")
                if ok:
                    allowed += 1
                else:
                    blocked += 1

        elapsed = time.monotonic() - start
        print(
            f"Benchmark: 3000 requests in {elapsed * 1000:.0f}ms: "
            f"{allowed} allowed, {blocked} blocked"
        )
        assert elapsed < 5.0, f"Token bucket too slow: {elapsed:.2f}s"
        assert allowed >= 1000, f"Expected at least 1000 allowed, got {allowed}"
