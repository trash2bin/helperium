"""Structured JSON logging configuration for all Python services.

Replaces stdlib logging.basicConfig with structlog for JSON output.
Compatible with Prometheus/Loki/Grafana log aggregation.

Usage:
    from api_service.log_config import configure_logging
    configure_logging()

Then use standard ``import logging; logger = logging.getLogger(__name__)``
— structlog patches stdlib logging automatically via ``structlog.stdlib.LoggerFactory``.
"""

from __future__ import annotations

import os
import logging
import sys

import structlog


def configure_logging(log_level: str | None = None) -> None:
    """Configure structured JSON logging.

    Args:
        log_level: Override LOG_LEVEL env var. Defaults to ``LOG_LEVEL`` env or ``INFO``.
    """
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()

    # Set stdlib logging level so structlog's filtering works
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, level, logging.INFO))

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if os.getenv("STRUCTLOG_CONSOLE")
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
