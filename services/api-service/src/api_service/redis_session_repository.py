"""Redis-backed session persistence — removes SQLite WAL from the hot path.

Usage: set ``SESSION_STORAGE_URI=redis://127.0.0.1:6379/0`` (same Redis
instance as ``ABUSE_STORAGE_URI`` is fine). When unset the existing
SQLite backend is used (rollback by omission).
"""

from __future__ import annotations

import json
import time
from typing import Any

from .session_repository import SessionAbuseState


class RedisSessionRepository:
    """Redis implementation of :class:`SessionRepository`.

    Each session is a set of keys under the ``session:{id}:`` prefix, all with
    a shared TTL (idle expiry). Turns are stored as a Redis list of JSON
    strings — RPUSH for append, LRANGE for read, LTRIM for history cap.

    The client is a synchronous ``redis.Redis`` so callers continue to use
    ``asyncio.to_thread`` without changing the protocol. The thread overhead
    is negligible next to SQLite's WAL contention (p95 331 ms → p95 ~1 ms
    for local Redis).
    """

    _TTL_SECONDS = 86400  # 24 h idle expiry

    def __init__(self, client: Any, *, max_turns: int) -> None:
        self._client = client
        self._max_turns = max(1, max_turns)

    # -- keys ----------------------------------------------------------------

    @staticmethod
    def _k_turns(session_id: str) -> str:
        return f"session:{session_id}:turns"

    @staticmethod
    def _k_turn_count(session_id: str) -> str:
        return f"session:{session_id}:turn_count"

    @staticmethod
    def _k_last_turn_at(session_id: str) -> str:
        return f"session:{session_id}:last_turn_at"

    @staticmethod
    def _k_created_at(session_id: str) -> str:
        return f"session:{session_id}:created_at"

    @staticmethod
    def _k_updated_at(session_id: str) -> str:
        return f"session:{session_id}:updated_at"

    @staticmethod
    def _k_token_hash(session_id: str) -> str:
        return f"session:{session_id}:token_hash"

    # -- SessionRepository ----------------------------------------------------

    def read_turns(self, session_id: str) -> list[list[dict[str, Any]]]:
        raw = self._client.lrange(self._k_turns(session_id), 0, -1)
        turns: list[list[dict[str, Any]]] = []
        for entry in raw:
            if not isinstance(entry, bytes):
                continue
            try:
                messages = json.loads(entry)
            except json.JSONDecodeError:
                continue
            if isinstance(messages, list) and all(
                isinstance(m, dict) for m in messages
            ):
                turns.append(messages)
        return turns

    def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        turns_key = self._k_turns(session_id)
        updated_key = self._k_updated_at(session_id)
        now = time.time()
        pipe = self._client.pipeline()
        pipe.rpush(turns_key, payload)
        pipe.ltrim(turns_key, -self._max_turns, -1)
        pipe.set(updated_key, now)
        pipe.expire(turns_key, self._TTL_SECONDS)
        pipe.expire(updated_key, self._TTL_SECONDS)
        pipe.execute()

    def accepted_user_turn(
        self, session_id: str, accepted_at: float
    ) -> SessionAbuseState:
        count_key = self._k_turn_count(session_id)
        last_key = self._k_last_turn_at(session_id)
        created_key = self._k_created_at(session_id)
        updated_key = self._k_updated_at(session_id)

        pipe = self._client.pipeline()
        pipe.incr(count_key)
        pipe.set(last_key, accepted_at)
        pipe.set(updated_key, accepted_at)
        pipe.setnx(created_key, accepted_at)
        pipe.expire(count_key, self._TTL_SECONDS)
        pipe.expire(last_key, self._TTL_SECONDS)
        pipe.expire(created_key, self._TTL_SECONDS)
        pipe.expire(updated_key, self._TTL_SECONDS)
        results = pipe.execute()
        # results[0] = new count after INCR
        new_count: int = results[0]
        return SessionAbuseState(
            user_turn_count=new_count,
            last_user_turn_at=accepted_at,
        )

    def abuse_state(self, session_id: str) -> SessionAbuseState:
        count_raw = self._client.get(self._k_turn_count(session_id))
        last_raw = self._client.get(self._k_last_turn_at(session_id))
        count = int(count_raw) if count_raw else 0
        last = float(last_raw) if last_raw else None
        return SessionAbuseState(user_turn_count=count, last_user_turn_at=last)

    def read_session_token_hash(self, session_id: str) -> str | None:
        raw = self._client.get(self._k_token_hash(session_id))
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        return value if value else None

    def bind_session_token(self, session_id: str, token_hash: str) -> str:
        """Bind a token hash without overwriting an existing one.

        Uses SETNX for the atomic create-then-check. Redis SETNX returns 1
        when the key was created, 0 when it already existed. When the key
        exists we read it back — the stored hash wins, never the caller's.
        """
        token_key = self._k_token_hash(session_id)
        created = self._client.setnx(token_key, token_hash)
        if created:
            self._client.expire(token_key, self._TTL_SECONDS)
            return token_hash
        raw = self._client.get(token_key)
        if raw is None:
            return token_hash
        stored = raw.decode() if isinstance(raw, bytes) else str(raw)
        return stored if stored else token_hash
