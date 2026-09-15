"""Authorization dependency for the API control plane."""

from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import Header, HTTPException, Query, status

from api_service.sessions import session_store

from .session_capability import (
    SESSION_TOKEN_HEADER,
    effective_session_id,
    token_matches,
)


def _expected_bearer() -> str:
    """Control-plane token from the environment (empty = not configured)."""
    return os.environ.get("API_BEARER_TOKEN", "")


def _presented_bearer(authorization: str | None) -> str | None:
    """Token from an ``Authorization: Bearer <token>`` header, if well formed.

    Returns None for a missing header, another scheme, or an empty credential,
    so callers can answer 401 without duplicating the parsing.
    """
    scheme, _, supplied_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied_token:
        return None
    return supplied_token


async def require_api_bearer(
    authorization: str | None = Header(default=None),
) -> None:
    """Require the configured bearer token for non-public API routes.

    The public chat, health and widget-config routes intentionally do not use
    this dependency. All control-plane and conversation-evidence routes fail
    closed: a missing server token is a configuration error, never anonymous
    access.
    """

    expected_token = _expected_bearer()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API control plane is not configured.",
        )

    supplied_token = _presented_bearer(authorization)
    if supplied_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token.",
        )


async def require_session_history_access(
    session_id: str = Query("default"),
    agent_name: str | None = Query(None),
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None, alias=SESSION_TOKEN_HEADER),
) -> None:
    """Authenticate a transcript read by bearer OR session capability.

    The bearer alone was not enough: demo/web injects its server bearer on
    every proxied call, which reduced a transcript's confidentiality to the
    secrecy of its session_id. The session capability token (the same
    contract chat turns already enforce) authenticates the session owner;
    the control-plane bearer stays valid for the operator/dashboard path.
    """

    expected_token = _expected_bearer()

    if expected_token:
        supplied_token = _presented_bearer(authorization)
        if supplied_token and hmac.compare_digest(supplied_token, expected_token):
            return

    if x_session_token:
        effective = effective_session_id(session_id, agent_name)
        stored_hash = await asyncio.to_thread(
            session_store.session_token_hash, effective
        )
        if token_matches(stored_hash, x_session_token):
            return

    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API control plane is not configured.",
        )
    if authorization:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token.",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Bearer authentication is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


__all__ = ["require_api_bearer", "require_session_history_access"]
