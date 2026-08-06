"""OpenTelemetry tracing setup for all Helperium Python services.

Usage::

    from helperium_sdk.tracing import setup_opentelemetry, get_tracer,
        instrument_fastapi, add_span_attributes, shutdown

    setup_opentelemetry("api-service")

    # Instrument FastAPI app (call after all routes/middleware registered)
    instrument_fastapi(app, "api-service")

    # Use tracer directly for manual spans
    tracer = get_tracer("api-service")
    with tracer.start_as_current_span("do_work") as span:
        span.set_attribute("tenant.id", tenant_id)

    # Quick attribute set on current span
    add_span_attributes({"tenant.id": tenant_id, "entity": "students"})

    # Graceful shutdown
    shutdown()

Environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT — OTLP HTTP endpoint (default: http://localhost:4318)
    OTEL_SERVICE_NAME — override service name (default: helperium-{service_name})
    OTEL_ENABLED — set to ``false`` to disable tracing entirely (default: ``true``)
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

_tracer_provider = None


def setup_opentelemetry(service_name: str) -> bool:
    """Configure OpenTelemetry SDK with OTLP HTTP export.

    Returns True if tracing is enabled, False if disabled via ``OTEL_ENABLED=false``
    or if required packages are not installed.

    Idempotent: safe to call multiple times across module reloads.
    Will not override an already-active global tracer provider.
    """
    global _tracer_provider  # noqa: PLW0603

    if os.environ.get("OTEL_ENABLED", "true").lower() in ("false", "0", "no"):
        logger.info("OpenTelemetry tracing disabled via OTEL_ENABLED=false")
        _tracer_provider = None
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # Check if a global provider is already set (avoid override warning)
        current_provider = trace.get_tracer_provider()
        if current_provider and not isinstance(current_provider, type(None)):
            try:
                # Attempt to detect if TracerProvider is already ours
                if (
                    _tracer_provider is not None
                    and _tracer_provider is current_provider
                ):
                    logger.debug(
                        "OTel already initialized for %s, skipping", service_name
                    )
                    return True
            except Exception:
                pass

        resource = Resource(
            attributes={
                SERVICE_NAME: os.environ.get(
                    "OTEL_SERVICE_NAME", f"helperium-{service_name}"
                ),
            }
        )

        otlp_endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
        )
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        processor = BatchSpanProcessor(exporter)

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        # Auto-instrument httpx for outbound HTTP calls
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception:
            logger.debug("HTTPX already instrumented, skipping")

        logger.info(
            "OpenTelemetry initialized for %s, exporting to %s",
            service_name,
            otlp_endpoint,
        )
        return True

    except ImportError as e:
        logger.warning(
            "OpenTelemetry packages not installed — tracing disabled (%s). "
            "Install: opentelemetry-distro[otlp] opentelemetry-instrumentation-fastapi",
            e,
        )
        return False
    except Exception as e:
        logger.warning("Failed to initialize OpenTelemetry: %s", e)
        return False


def get_tracer(component: str = "default"):
    """Get a named tracer instance from the global provider."""
    from opentelemetry import trace

    return trace.get_tracer(component)


def instrument_fastapi(app, service_name: str) -> None:
    """Instrument a FastAPI application with OpenTelemetry.

    Call this after ``setup_opentelemetry()`` and after all routes/middleware
    are registered, so OTel captures the full route list.

    If OTEL_ENABLED=false, this is a no-op.
    """
    if _tracer_provider is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI instrumented for %s", service_name)
    except ImportError as e:
        logger.warning("FastAPI instrumentation not available: %s", e)
    except Exception as e:
        logger.warning("Failed to instrument FastAPI: %s", e)


def get_current_trace_id() -> str:
    """Return the current trace ID as hex string, or empty string if no span is active."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            sc = span.get_span_context()
            if sc and sc.trace_id:
                return format(sc.trace_id, "032x")
    except Exception:
        pass
    return ""


def add_span_attributes(attributes: dict[str, str | int | float]) -> None:
    """Add key-value attributes to the currently active span (if any).

    Safe to call even when tracing is disabled — does nothing in that case.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            for key, value in attributes.items():
                span.set_attribute(key, value)
    except Exception:
        pass


def shutdown() -> None:
    """Flush pending spans and shut down the tracer provider.

    Call during application shutdown (e.g. in a FastAPI lifespan handler).
    """
    global _tracer_provider  # noqa: PLW0603
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
        except Exception:
            pass
