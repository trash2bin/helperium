"""Public widget problem-report endpoint (``POST /api/reports``).

The payload is stored verbatim for operator review — it is never fed to the
LLM, so report text cannot steer model behaviour. Deliberately does not run
the chat anti-abuse pipeline: a complaint must not consume the reporter's
user-turn quota or mark the session as abusive. Abuse resistance here is the
per-IP SlowAPI limit plus strict payload caps on the DTO.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api_service.http_models import ReportCreateRequest
from api_service.prometheus_metrics import (
    report_store_errors_total,
    reports_total,
)
from api_service.server.rate_limit import get_client_ip, limiter, reports_rate_limit
from api_service.server.session_capability import effective_session_id

logger = logging.getLogger("api_service.server")

router = APIRouter()


@router.post("/api/reports", status_code=201)
@limiter.limit(reports_rate_limit)
async def create_report(request: Request, body: ReportCreateRequest) -> JSONResponse:
    """Accept a problem report from the embed widget."""
    from api_service.reports import get_report_store

    correlation_id = getattr(request.state, "correlation_id", None)
    record = {
        "id": str(uuid.uuid4()),
        "status": "new",
        "agent": body.agent,
        "session_id": body.session_id,
        # Same effective key the chat routes use for history/backlog lookup.
        "session_key": effective_session_id(body.session_id, body.agent),
        "lang": body.lang,
        "message_kind": body.message.kind,
        "message_text": body.message.text,
        "message_tools": body.message.tools,
        "display_names": body.message.display_names,
        "transcript": [item.model_dump() for item in body.transcript],
        "comment": body.comment,
        "last_error_text": body.last_error.text if body.last_error else None,
        "last_error_correlation_id": (
            body.last_error.correlation_id if body.last_error else None
        ),
        "page_url": body.page_url,
        "correlation_id": correlation_id,
        "client_ip": get_client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:512],
    }

    try:
        get_report_store().insert(record)
    except Exception:
        report_store_errors_total.inc()
        logger.warning(
            "Report store write failed",
            extra={"correlation_id": correlation_id, "agent": body.agent},
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Failed to store the report. Try again later."},
        )

    reports_total.labels("accepted").inc()
    logger.info(
        "Report submitted",
        extra={
            "correlation_id": correlation_id,
            "report_id": record["id"],
            "agent": body.agent,
            "session_key": record["session_key"],
        },
    )
    return JSONResponse(
        status_code=201,
        content={"status": "accepted", "id": record["id"]},
    )
