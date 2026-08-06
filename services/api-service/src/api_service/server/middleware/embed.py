"""Embed security headers middleware."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import Request

from api_service.prometheus_metrics import embed_widget_requests

logger = logging.getLogger("api_service.server")


async def add_embed_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    response = await call_next(request)
    if request.url.path.startswith("/embed/"):
        embed_widget_requests.labels(endpoint=request.url.path).inc()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Cache static assets
        if request.url.path.endswith((".js", ".css")):
            # Widget JS/CSS — versioned by path, safe to cache 1 hour
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            # Non-asset embed files: short cache to avoid stale 404s
            response.headers.setdefault("Cache-Control", "no-cache")
    return response
