"""FastAPI application — wiring, lifespan, middleware."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.staticfiles import StaticFiles

from api_service.prometheus_metrics import init_metrics
from api_service.log_config import configure_logging
from .auth import require_api_bearer
from .rate_limit import limiter

try:
    from helperium_sdk.tracing import (
        setup_opentelemetry,
        instrument_fastapi,
        shutdown as otel_shutdown,
    )
except ImportError:
    setup_opentelemetry = None
    instrument_fastapi = None
    otel_shutdown = None

from .deps import get_agent, get_agent_instance, get_agent_store, _sync_pool_from_store
from .middleware.correlation import add_correlation_id as correlation_middleware
from .middleware.embed import add_embed_security_headers as embed_security_middleware
from .routes import chat, agents, admin, backlog, health, voice

configure_logging()
logger = logging.getLogger("api_service.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("API server starting up")

    # Warm up LLM agent
    try:
        get_agent()
        logger.info("LLM agent ready")
    except Exception as exc:
        logger.warning("Agent warmup failed (will retry on first request): %s", exc)

    # Seed default agent (used by embed widget data-agent="default")
    # so the widget works out-of-the-box on a fresh deployment.
    try:
        store = get_agent_store()
        if store.get_agent("default") is None:
            store.create_agent(
                name="default",
                description="Default assistant for embed widget",
                tenant_ids=["default"],
                widget_config={
                    "title": "Assistant",
                    "greeting": "How can I help?",
                    "position": "right",
                },
            )
            logger.info("Seeded default agent")
    except Exception as exc:
        logger.warning("Default agent seed failed: %s", exc)

    # Sync ProviderPool from ProviderStore
    try:
        await _sync_pool_from_store()
    except Exception as exc:
        logger.warning("ProviderPool sync failed: %s", exc)

    # Setup OpenTelemetry tracing
    if setup_opentelemetry is not None:
        try:
            setup_opentelemetry("api-service")
            logger.info("OpenTelemetry tracing initialized")
        except Exception as exc:
            logger.warning("OTel setup failed: %s", exc)

    yield

    # Shutdown
    logger.info("API server shutting down")

    # Shutdown OpenTelemetry
    if otel_shutdown is not None:
        try:
            otel_shutdown()
        except Exception as exc:
            logger.warning("OTel shutdown failed: %s", exc)
    agent = get_agent_instance()
    if agent is not None and agent.mcp_client is not None:
        try:
            await agent.mcp_client.close()
        except Exception as exc:
            logger.warning("MCP client close failed: %s", exc)

    from api_service.agent.orchestrator import _pool as _provider_pool

    try:
        await _provider_pool.stop_health_checks()
    except Exception as exc:
        logger.warning("ProviderPool shutdown failed: %s", exc)

    logger.info("Shutdown complete")


app = FastAPI(
    title="Helperium API",
    description="LLM agent orchestration service",
    version="1.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Prometheus metrics
init_metrics(app)

# Instrument FastAPI with OpenTelemetry
if instrument_fastapi is not None:
    try:
        instrument_fastapi(app, "api-service")
    except Exception as exc:
        logger.warning("OTel FastAPI instrumentation failed: %s", exc)

# Rate limiter
app.state.limiter = limiter


def _helperium_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Return a client-actionable response when the public chat limit is hit.

    SlowAPI emits a valid 429 but does not add ``Retry-After`` by default.  The
    rate item's expiry is a conservative upper bound for the caller to retry,
    and is preferable to making the web widget guess or immediately retry.
    """

    response = _rate_limit_exceeded_handler(request, exc)
    if "retry-after" not in response.headers:
        limit = getattr(exc, "limit", None)
        rate_item = getattr(limit, "limit", None)
        retry_after = rate_item.get_expiry() if rate_item is not None else 60
        response.headers["Retry-After"] = str(retry_after)
    return response


app.add_exception_handler(RateLimitExceeded, _helperium_rate_limit_handler)  # pyright: ignore[reportArgumentType]
app.add_middleware(SlowAPIMiddleware)

# CORS. Browser-facing deployments must enumerate trusted origins.
# A wildcard would let an arbitrary website drive the public chat endpoint and
# consume the tenant's LLM budget, so reject it before the service starts.
cors_origins_raw = os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:8080")
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()] or [
    "http://localhost:8080"
]
if "*" in cors_origins:
    raise RuntimeError(
        "CORS_ALLOW_ORIGINS must list explicit origins; wildcard '*' is not allowed."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Tenant-ID", "X-Correlation-ID"],
)

# Custom middleware
app.middleware("http")(embed_security_middleware)
app.middleware("http")(correlation_middleware)

# Mount embed widget static files
embed_override = os.environ.get("EMBED_DIR")
if embed_override:
    embed_path = Path(embed_override)
else:
    embed_path = Path(__file__).resolve().parent.parent.parent.parent / "embed" / "dist"
if embed_path.is_dir():
    app.mount("/embed", StaticFiles(directory=str(embed_path)), name="embed")
    logger.info("Embed widget mounted at /embed from %s", embed_path)

# Public allowlist: browser-facing chat, widget bootstrap/assets and liveness only.
app.include_router(chat.router)
app.include_router(agents.public_router)
app.include_router(health.router)

# Every other API route is private by construction. New control-plane routers
# must be included here, so they cannot become public by omitted per-route auth.
private_router = APIRouter(dependencies=[Depends(require_api_bearer)])
private_router.include_router(agents.router)
private_router.include_router(admin.router)
private_router.include_router(backlog.router)
private_router.include_router(voice.router)


@private_router.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(private_router)


def main() -> None:
    """Run the API server (CLI entry point)."""
    from api_service.backlog import backlog
    from api_service.abuse_live import get_live_abuse_provider
    from helperium_sdk.settings import settings

    # Cleanup old backlog files on each startup
    try:
        backlog.cleanup_old()
        logger.info("Backlog cleanup completed")
    except Exception as exc:
        logger.warning("Backlog cleanup failed: %s", exc)

    # Apply runtime settings from live abuse config
    try:
        get_live_abuse_provider().apply_runtime_settings()
        logger.info("Runtime settings applied from abuse config")
    except Exception as exc:
        logger.warning("Failed to apply runtime settings: %s", exc)

    uvicorn.run(
        "api_service.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


# Backward-compatible imports for the old `server.py` pattern
# Used by demo/web and tests that do `from api_service.server import app`
