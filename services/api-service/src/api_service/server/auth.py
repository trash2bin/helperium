"""Authorization dependency for the API control plane."""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status


async def require_api_bearer(
    authorization: str | None = Header(default=None),
) -> None:
    """Require the configured bearer token for non-public API routes.

    The public chat, health and widget-config routes intentionally do not use
    this dependency. All control-plane and conversation-evidence routes fail
    closed: a missing server token is a configuration error, never anonymous
    access.
    """

    expected_token = os.environ.get("API_BEARER_TOKEN", "")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API control plane is not configured.",
        )

    scheme, _, supplied_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied_token:
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


__all__ = ["require_api_bearer"]
