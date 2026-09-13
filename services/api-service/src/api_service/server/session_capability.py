"""Capability tokens for chat sessions.

``session_id`` alone is only a transcript key: anyone who guesses or observes
another visitor's session id can append to (and steer the context of) that
conversation. The chat routes therefore bind every session to a capability
token: it is issued once when the session is created, delivered through the
first SSE ``session`` event and the ``X-Session-Token`` response header, and
verified on every subsequent turn via the ``X-Session-Token`` request header.

Tokens are 256-bit random values; the session store keeps only a SHA-256
hash, so a database leak does not expose usable capabilities.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

SESSION_TOKEN_HEADER = "X-Session-Token"


def generate_session_token() -> str:
    """Return a fresh 256-bit url-safe capability token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a token for at-rest storage (the token itself is the secret)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(stored_hash: str | None, presented: str | None) -> bool:
    """Constant-time check of a presented token against the stored hash."""
    if not stored_hash or not presented:
        return False
    return hmac.compare_digest(stored_hash, hash_session_token(presented))
