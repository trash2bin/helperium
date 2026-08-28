"""Structured JSON logging configuration for all Python services.

Replaces stdlib logging.basicConfig with structlog for JSON output.
Compatible with Prometheus/Loki/Grafana log aggregation.

Usage:
    from api_service.log_config import configure_logging
    configure_logging()

Then use standard ``import logging; logger = logging.getLogger(__name__)``.
Stdlib-originated records (the majority of the codebase) are rendered by a
``ProcessorFormatter`` installed on the root handler, so structlog processors —
including correlation_id and trace_id injection — apply to them as well.
Events logged through structlog's own API share the same formatter.
"""

from __future__ import annotations

import contextvars
import os
import logging
import sys

import structlog


# Correlation id of the chat turn currently driving agent/MCP work. Routes
# generate the id and set this contextvar; the SSE producer task is created
# with a copied contextvars.Context(), so every log line inside that turn —
# including MCP client, agent loop and provider calls — automatically carries
# the same correlation_id without threading the value through signatures.
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def _add_correlation_id(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Structlog processor: inject correlation_id from the current context."""
    cid = correlation_id_var.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _add_trace_id(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Structlog processor: inject trace_id from the current OTel span."""
    try:
        from helperium_sdk.tracing import get_current_trace_id

        tid = get_current_trace_id()
        if tid:
            event_dict["trace_id"] = tid
    except Exception:
        pass
    return event_dict


def _shared_processors() -> list[structlog.types.Processor]:
    """Processors applied to both structlog-native and stdlib-origin records."""
    return [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_correlation_id,
        _add_trace_id,
    ]


def build_log_formatter() -> structlog.stdlib.ProcessorFormatter:
    """Formatter for stdlib logging handlers (used in production and tests)."""
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if os.getenv("STRUCTLOG_CONSOLE")
        else structlog.processors.JSONRenderer()
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )


def configure_logging(log_level: str | None = None) -> None:
    """Configure structured JSON logging.

    Args:
        log_level: Override LOG_LEVEL env var. Defaults to ``LOG_LEVEL`` env or ``INFO``.
    """
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()

    # All stdlib records (including plain logging.getLogger(...) callers) are
    # rendered through the shared ProcessorFormatter so JSON output — with
    # correlation_id/trace_id — is uniform across the service. The handler
    # writes to stderr; note basicConfig forbids combining `stream` with
    # `handlers`.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(build_log_formatter())
    logging.basicConfig(handlers=[handler], level=getattr(logging, level, logging.INFO))

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
