"""X-Correlation-ID middleware."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import Request

from helperium_sdk.tracing import add_span_attributes

logger = logging.getLogger("api_service.server")


async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id

    # Enrich OTel span with request metadata
    tenant_id = request.headers.get("X-Tenant-ID", "")
    if tenant_id:
        add_span_attributes({"tenant.id": tenant_id})
    add_span_attributes(
        {
            "correlation_id": correlation_id,
            "http.method": request.method,
            "http.target": request.url.path,
        }
    )

    logger.info(
        "Request started",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    logger.info(
        "Request completed",
        extra={
            "correlation_id": correlation_id,
            "status_code": response.status_code,
        },
    )
    return response
