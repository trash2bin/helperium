"""Eviction and cap contracts for anti-abuse in-memory state.

TokenBucket._buckets and AntiAbuseChecker._recent_messages are keyed by
attacker-influenced session IDs, so both maps need bounded memory: idle
eviction past a horizon, a hard entry cap with least-recently-seen eviction,
and stale-session cleanup. Public rate-limiting behavior must not change.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import api_service.anti_abuse as anti_abuse
from api_service.anti_abuse import (
    RECENT_MESSAGE_WINDOW_SECONDS,
    RECENT_MESSAGES_MAX_SESSIONS,
    TOKEN_BUCKET_EVICT_SCAN_INTERVAL,
    TOKEN_BUCKET_IDLE_SECONDS,
    TOKEN_BUCKET_MAX_ENTRIES,
    AbuseConfig,
    AntiAbuseChecker,
    TokenBucket,
)

_UA = "Mozilla/5.0 eviction-test"


class TestTokenBucketCap:
    def test_cap_enforced_on_distinct_keys(self):
        tb = TokenBucket(AbuseConfig())
        for i in range(TOKEN_BUCKET_MAX_ENTRIES + 50):
            tb.allow(f"churn-{i}", "10.0.0.1", _UA)
        assert len(tb._buckets) <= TOKEN_BUCKET_MAX_ENTRIES

    def test_rate_limiting_works_after_cap_eviction(self):
        tb = TokenBucket(AbuseConfig())
        for i in range(TOKEN_BUCKET_MAX_ENTRIES + 10):
            tb.allow(f"churn-{i}", "10.0.0.1", _UA)
        results = [tb.allow("fresh", "10.0.0.1", _UA)[0] for _ in range(6)]
        assert results == [True] * 5 + [False]


class TestTokenBucketIdleEviction:
    def test_idle_buckets_evicted_on_periodic_scan(self, monkeypatch):
        tb = TokenBucket(AbuseConfig())
        tb.allow("old-session", "10.0.0.1", _UA)
        old_key = tb._key("old-session", "10.0.0.1", _UA)
        assert old_key in tb._buckets

        future = time.monotonic() + TOKEN_BUCKET_IDLE_SECONDS + 10
        monkeypatch.setattr(
            anti_abuse, "time", SimpleNamespace(monotonic=lambda: future)
        )
        # New-key inserts drive the lazy scan; one full interval must
        # trigger it and drop the idle bucket.
        for i in range(TOKEN_BUCKET_EVICT_SCAN_INTERVAL):
            tb.allow(f"warm-{i}", "10.0.0.1", _UA)

        assert old_key not in tb._buckets
        assert len(tb._buckets) <= TOKEN_BUCKET_MAX_ENTRIES

    def test_active_buckets_survive_idle_scan(self, monkeypatch):
        tb = TokenBucket(AbuseConfig())
        for i in range(TOKEN_BUCKET_EVICT_SCAN_INTERVAL):
            tb.allow(f"warm-{i}", "10.0.0.1", _UA)

        future = time.monotonic() + TOKEN_BUCKET_IDLE_SECONDS + 10
        monkeypatch.setattr(
            anti_abuse, "time", SimpleNamespace(monotonic=lambda: future)
        )
        # Refresh one bucket's last_seen under the mocked clock so it counts
        # as active, then drive a periodic scan with fresh inserts.
        tb.allow("warm-5", "10.0.0.1", _UA)
        for i in range(TOKEN_BUCKET_EVICT_SCAN_INTERVAL):
            tb.allow(f"later-{i}", "10.0.0.1", _UA)

        assert tb._key("warm-5", "10.0.0.1", _UA) in tb._buckets


class TestRecentMessagesCap:
    def test_cap_enforced_on_distinct_sessions(self):
        checker = AntiAbuseChecker(AbuseConfig())
        for i in range(RECENT_MESSAGES_MAX_SESSIONS + 50):
            checker._check_repeated_message(f"sess-{i}", f"unique-{i}")
        assert len(checker._recent_messages) <= RECENT_MESSAGES_MAX_SESSIONS

    def test_stale_sessions_dropped_first_on_cap(self):
        checker = AntiAbuseChecker(AbuseConfig())
        stale_ts = time.monotonic() - RECENT_MESSAGE_WINDOW_SECONDS - 1
        checker._recent_messages["stale-session"] = [("old", stale_ts)]
        for i in range(RECENT_MESSAGES_MAX_SESSIONS + 10):
            checker._check_repeated_message(f"sess-{i}", f"unique-{i}")
        assert "stale-session" not in checker._recent_messages

    def test_repeated_detection_survives_cap_churn(self):
        checker = AntiAbuseChecker(AbuseConfig())
        for i in range(RECENT_MESSAGES_MAX_SESSIONS + 10):
            checker._check_repeated_message(f"churn-{i}", f"unique-{i}")
        for _ in range(3):
            assert checker._check_repeated_message("live", "same-text") is None
        assert checker._check_repeated_message("live", "same-text") is not None


class TestRecentMessagesStaleCleanup:
    def test_prune_writes_back_surviving_entries(self):
        checker = AntiAbuseChecker(AbuseConfig())
        now = time.monotonic()
        checker._recent_messages["s"] = [
            ("ancient", now - RECENT_MESSAGE_WINDOW_SECONDS - 5),
            ("recent", now - 10),
        ]
        with checker._lock:
            result = checker._prune_session_locked("s", now)
        assert [m for m, _ in result] == ["recent"]
        assert [m for m, _ in checker._recent_messages["s"]] == ["recent"]

    def test_prune_drops_empty_session_entry(self):
        checker = AntiAbuseChecker(AbuseConfig())
        stale_ts = time.monotonic() - RECENT_MESSAGE_WINDOW_SECONDS - 1
        checker._recent_messages["gone"] = [("old", stale_ts)]
        with checker._lock:
            result = checker._prune_session_locked("gone", time.monotonic())
        assert result == []
        assert "gone" not in checker._recent_messages

    def test_access_flushes_stale_history(self):
        checker = AntiAbuseChecker(AbuseConfig())
        stale_ts = time.monotonic() - RECENT_MESSAGE_WINDOW_SECONDS - 1
        checker._recent_messages["s"] = [("msg", stale_ts)]
        checker._check_repeated_message("s", "msg")
        assert [m for m, _ in checker._recent_messages["s"]] == ["msg"]
