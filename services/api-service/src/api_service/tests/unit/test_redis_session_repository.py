"""Redis-backed session repository tests (fakeredis, no real Redis needed)."""

from __future__ import annotations

import fakeredis
import pytest

from api_service.redis_session_repository import RedisSessionRepository


@pytest.fixture
def redis_repo() -> RedisSessionRepository:
    """Fresh fakeredis with a RedisSessionRepository on top."""
    client = fakeredis.FakeRedis()
    client.flushall()
    return RedisSessionRepository(client=client, max_turns=5)


class TestAbuseState:
    def test_new_session_zero_state(self, redis_repo):
        state = redis_repo.abuse_state("s1")
        assert state.user_turn_count == 0
        assert state.last_user_turn_at is None

    def test_accept_increments_count(self, redis_repo):
        redis_repo.accepted_user_turn("s1", 1000.0)
        state = redis_repo.abuse_state("s1")
        assert state.user_turn_count == 1
        assert state.last_user_turn_at == 1000.0

    def test_accept_sequential(self, redis_repo):
        redis_repo.accepted_user_turn("s1", 1000.0)
        redis_repo.accepted_user_turn("s1", 1100.0)
        state = redis_repo.abuse_state("s1")
        assert state.user_turn_count == 2
        assert state.last_user_turn_at == 1100.0


class TestTurns:
    def test_empty_history(self, redis_repo):
        assert redis_repo.read_turns("s1") == []

    def test_append_and_read(self, redis_repo):
        msgs = [{"role": "user", "content": "hi"}]
        redis_repo.append_turn("s1", msgs)
        turns = redis_repo.read_turns("s1")
        assert len(turns) == 1
        assert turns[0] == msgs

    def test_trim_to_max_turns(self, redis_repo):
        for i in range(7):
            redis_repo.append_turn("s1", [{"role": "user", "content": f"m{i}"}])
        turns = redis_repo.read_turns("s1")
        assert len(turns) == 5  # max_turns=5
        # oldest dropped
        assert turns[0] == [{"role": "user", "content": "m2"}]
        assert turns[-1] == [{"role": "user", "content": "m6"}]

    def test_multiple_sessions_isolated(self, redis_repo):
        redis_repo.append_turn("s1", [{"role": "user", "content": "a"}])
        redis_repo.append_turn("s2", [{"role": "user", "content": "b"}])
        assert len(redis_repo.read_turns("s1")) == 1
        assert len(redis_repo.read_turns("s2")) == 1
        assert redis_repo.read_turns("s1")[0][0]["content"] == "a"


class TestTokenBinding:
    def test_no_token(self, redis_repo):
        assert redis_repo.read_session_token_hash("s1") is None

    def test_bind_then_read(self, redis_repo):
        stored = redis_repo.bind_session_token("s1", "abc123")
        assert stored == "abc123"
        assert redis_repo.read_session_token_hash("s1") == "abc123"

    def test_bind_does_not_overwrite(self, redis_repo):
        first = redis_repo.bind_session_token("s1", "abc")
        second = redis_repo.bind_session_token("s1", "xyz")
        assert first == "abc"
        assert second == "abc"  # keeps first


class TestMultipleWorkers:
    def test_two_clients_share_state(self):
        """Two Redis clients sharing one FakeServer — the multi-worker invariant."""
        server = fakeredis.FakeServer()
        c1 = fakeredis.FakeRedis(server=server)
        c2 = fakeredis.FakeRedis(server=server)
        r1 = RedisSessionRepository(client=c1, max_turns=10)
        r2 = RedisSessionRepository(client=c2, max_turns=10)

        r1.accepted_user_turn("s1", 1000.0)
        state = r2.abuse_state("s1")
        assert state.user_turn_count == 1

        r1.append_turn("s1", [{"role": "user", "content": "x"}])
        turns = r2.read_turns("s1")
        assert len(turns) == 1
        assert turns[0][0]["content"] == "x"
