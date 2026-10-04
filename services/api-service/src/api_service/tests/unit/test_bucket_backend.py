"""Трек 3: хранилище бакетов за интерфейсом, бэкенд подменяется конфигом.

Главный инвариант — честность суммарного лимита при N воркерах: два
процесса (два воркера), разделяющих один бэкенд, должны видеть ОДИН бакет,
а не по одному на воркер. Иначе стресс даёт «xN» не за счёт платформы, а
за счёт N-кратного лимита (ложный потолок, §5 RUNBOOK).

Тесты идут на fakeredis (без lupa → без Lua, атомарность через
WATCH/MULTI/EXEC), поэтому они же проверяют и прод-путь с настоящим Redis.
"""

from __future__ import annotations

import fakeredis
import pytest

from api_service.anti_abuse import (
    AbuseConfig,
    InMemoryBucketBackend,
    RedisBucketBackend,
    TokenBucket,
    build_token_bucket_backend,
)


@pytest.fixture
def shared_redis():
    """Один сервер, два клиента — модель двух воркеров, делящих хранилище."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server), fakeredis.FakeRedis(server=server)


class TestInMemoryBackend:
    def test_burst_then_block(self):
        backend = InMemoryBucketBackend()
        cfg = AbuseConfig(rps=1, burst=3)
        for _ in range(3):
            allowed, _ = backend.consume("k", rps=cfg.rps, burst=cfg.burst)
            assert allowed
        allowed, retry = backend.consume("k", rps=cfg.rps, burst=cfg.burst)
        assert not allowed
        assert retry is not None and retry > 0

    def test_refill_over_time(self):
        backend = InMemoryBucketBackend()
        cfg = AbuseConfig(rps=1, burst=1)
        assert backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        assert not backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        # 1.5 секунды → 1.5 токена
        backend.advance_time("k", 1500)
        assert backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        assert not backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]

    def test_keys_are_isolated(self):
        backend = InMemoryBucketBackend()
        cfg = AbuseConfig(rps=1, burst=1)
        assert backend.consume("a", rps=cfg.rps, burst=cfg.burst)[0]
        assert backend.consume("b", rps=cfg.rps, burst=cfg.burst)[0]


class TestRedisBackend:
    def test_burst_then_block(self, shared_redis):
        client, _ = shared_redis
        backend = RedisBucketBackend(client)
        cfg = AbuseConfig(rps=1, burst=3)
        for _ in range(3):
            allowed, _ = backend.consume("k", rps=cfg.rps, burst=cfg.burst)
            assert allowed
        allowed, retry = backend.consume("k", rps=cfg.rps, burst=cfg.burst)
        assert not allowed
        assert retry is not None and retry > 0

    def test_two_workers_share_one_bucket(self, shared_redis):
        """Ключевой тест честности: два воркера = один бакет, не два.

        Каждый воркер тратит burst=2. Если бы у воркера был свой бакет,
        суммарно прошло бы 4 запроса; при общем бакете — ровно 2, и третий
        запрос от любого воркера обязан быть отклонён.
        """
        client_a, client_b = shared_redis
        cfg = AbuseConfig(rps=0, burst=2)
        worker_a = RedisBucketBackend(client_a)
        worker_b = RedisBucketBackend(client_b)

        assert worker_a.consume("shared", rps=cfg.rps, burst=cfg.burst)[0]
        assert worker_b.consume("shared", rps=cfg.rps, burst=cfg.burst)[0]
        # Бакет общий и исчерпан: оба воркера видят отказ.
        assert not worker_a.consume("shared", rps=cfg.rps, burst=cfg.burst)[0]
        assert not worker_b.consume("shared", rps=cfg.rps, burst=cfg.burst)[0]

    def test_refill_over_time(self, shared_redis):
        client, _ = shared_redis
        backend = RedisBucketBackend(client)
        cfg = AbuseConfig(rps=1, burst=1)
        assert backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        assert not backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        # Ждём реального времени: 1.2 с → 1 токен восстановлен.
        import time

        time.sleep(1.2)
        assert backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]

    def test_zero_rps_blocks_with_retry(self, shared_redis):
        client, _ = shared_redis
        backend = RedisBucketBackend(client)
        cfg = AbuseConfig(rps=0, burst=1)
        assert backend.consume("k", rps=cfg.rps, burst=cfg.burst)[0]
        allowed, retry = backend.consume("k", rps=cfg.rps, burst=cfg.burst)
        assert not allowed
        assert retry is not None and retry >= 0.1


class TestTokenBucketBackendSelection:
    def test_default_backend_is_in_memory(self):
        tb = TokenBucket(AbuseConfig(rps=1, burst=2))
        assert isinstance(tb._backend, InMemoryBucketBackend)

    def test_explicit_redis_backend(self, shared_redis):
        client, _ = shared_redis
        tb = TokenBucket(
            AbuseConfig(rps=1, burst=2), backend=RedisBucketBackend(client)
        )
        assert isinstance(tb._backend, RedisBucketBackend)
        # Публичный API TokenBucket работает поверх Redis-бэкенда.
        assert tb.allow("sess", "10.0.0.1", "Mozilla/5.0")[0]
        assert tb.allow("sess", "10.0.0.1", "Mozilla/5.0")[0]
        assert not tb.allow("sess", "10.0.0.1", "Mozilla/5.0")[0]


class TestBuildBackendFromEnv:
    def test_unset_selects_memory(self, monkeypatch):
        monkeypatch.delenv("ABUSE_STORAGE_URI", raising=False)
        assert isinstance(build_token_bucket_backend(), InMemoryBucketBackend)

    def test_redis_uri_selects_redis(self, monkeypatch):
        monkeypatch.setenv("ABUSE_STORAGE_URI", "redis://127.0.0.1:6379/0")
        backend = build_token_bucket_backend()
        # ``redis.Redis.from_url`` не соединяется до первой команды, так что
        # тип бэкенда проверяется без живого сервера (откат: URI не задан).
        assert isinstance(backend, RedisBucketBackend)
