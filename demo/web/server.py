"""
FastAPI-based web server with reverse proxy to API service.
Stage 0.4: Translated from Starlette to FastAPI + /api/* reverse proxy + SSE-proxy.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable
from uuid import uuid4

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from demo.settings import PROJECT_ROOT, settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("demo.web.server")


STATIC_DIR = PROJECT_ROOT / "demo" / "web" / "static"

# API proxy configuration
API_BASE_URL = f"http://{settings.api_host}:{settings.api_port}"


def _build_api_url(path: str) -> str:
    """Build full API URL from path."""
    return f"{API_BASE_URL}{path}" if path.startswith("/") else f"{API_BASE_URL}/{path}"


async def _get_proxy_headers(request: Request) -> dict[str, str]:
    """Build headers for proxying to API, including auth token and correlation ID."""
    headers = {
        "user-agent": request.headers.get("user-agent", "demo-web-proxy"),
        "accept": request.headers.get("accept", "*/*"),
        "accept-language": request.headers.get("accept-language", ""),
        "accept-encoding": request.headers.get("accept-encoding", ""),
    }

    # Forward X-Tenant-ID if present in the browser request OR in request.state
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id and hasattr(request.state, "tenant_id"):
        tenant_id = request.state.tenant_id
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id

    # Пробрасываем correlation ID для трассировки запроса через все сервисы
    correlation_id = request.headers.get("x-correlation-id")
    if correlation_id:
        headers["x-correlation-id"] = correlation_id

    # Forward content-type for requests with body
    if request.method in ("POST", "PUT", "PATCH"):
        ct = request.headers.get("content-type")
        if ct:
            headers["content-type"] = ct

    # Add bearer token if configured
    if settings.api_bearer_token:
        headers["authorization"] = f"Bearer {settings.api_bearer_token}"

    return headers


async def _proxy_to_api(
    request: Request,
    api_path: str,
    stream: bool = False,
) -> Response | StreamingResponse:
    """Proxy request to API service."""
    http_client = request.app.state.http_client
    url = _build_api_url(api_path)
    headers = await _get_proxy_headers(request)

    body = await request.body() if request.method != "GET" else None

    logger.debug(f"Proxy {request.method} {api_path} -> {url}")
    logger.debug(f"Proxy headers: {headers}")
    if body:
        logger.debug(f"Proxy body size: {len(body)} bytes")

    try:
        proxy_req = http_client.build_request(
            request.method,
            url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )

        if stream:
            response = await http_client.send(proxy_req, stream=True)

            if response.status_code != 200:
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )

            async def stream_gen():
                async for chunk in response.aiter_bytes():
                    yield chunk

            # Filter out hop-by-hop headers
            response_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("transfer-encoding", "connection")
            }

            return StreamingResponse(
                stream_gen(),
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )
        else:
            response = await http_client.send(proxy_req, stream=False)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )

    except httpx.ConnectError as exc:
        logger.error(f"API connection error: {exc}")
        return Response(
            content=b"API service unavailable",
            status_code=502,
            headers={"content-type": "text/plain"},
        )
    except httpx.HTTPStatusError as exc:
        return Response(
            content=exc.response.content,
            status_code=exc.response.status_code,
            headers=dict(exc.response.headers),
        )
    except Exception as exc:
        logger.error(f"Proxy error: {exc}")
        return Response(
            content=str(exc).encode(),
            status_code=500,
            headers={"content-type": "text/plain"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Create HTTP client for API proxying
    http_client = httpx.AsyncClient(timeout=settings.web_proxy_timeout)
    app.state.http_client = http_client

    # Startup
    logger.info(f"Web server starting on {settings.web_host}:{settings.web_port}")
    logger.info(f"API URL: {API_BASE_URL}")
    if settings.api_bearer_token:
        logger.info("Bearer token configured")

    yield

    # Shutdown
    logger.info("Web server shutting down")
    await http_client.aclose()


# Create FastAPI app
app = FastAPI(
    title="Agent-Tutor Web Frontend",
    description="Web server that serves the static frontend and acts as a reverse proxy to the Core API.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
# allow_origins from env: comma-separated, or "*" for all (dev only)
cors_origins_raw = settings.web_origin
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Correlation ID middleware ---
@app.middleware("http")
async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# --- Routes ---


@app.get("/")
async def index() -> FileResponse:
    """Serve the main index.html page."""
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Web service health check."""
    return {
        "web": "ok",
        "api_base_url": API_BASE_URL,
        "token_configured": bool(settings.api_bearer_token),
    }


# --- API Reverse Proxy Routes ---


# --- Data-service reverse proxy (read-only обзор для web UI) ---

DATA_SERVICE_URL = settings.data_service_url


async def _proxy_to_data_service(
    request: Request,
    data_path: str,
) -> Response:
    """Proxy GET request to data-service напрямую (минуя api-service).

    data-service — единственный владелец БД. Web не должен идти к data-service
    через api-service, потому что это лишний hop и api-service не должен заниматься
    трансляцией данных (его ответственность — агент).
    """
    http_client = request.app.state.http_client
    url = f"{DATA_SERVICE_URL}{data_path}"
    headers = await _get_proxy_headers(request)
    logger.debug("data-service proxy: %s -> %s", request.method, url)
    response = await http_client.get(url, headers=headers)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={"Content-Type": response.headers.get("Content-Type", "application/json")},
    )


@app.get("/api/data/stats")
async def proxy_data_stats(request: Request) -> Response:
    return await _proxy_to_data_service(request, "/stats")


@app.get("/api/manifest")
async def proxy_manifest(request: Request) -> Response:
    """Proxy to data-service /mcp/manifest — единый источник метаданных."""
    return await _proxy_to_data_service(request, "/mcp/manifest")


@app.get("/api/data/{entity_name:path}")
async def proxy_data_entity(request: Request, entity_name: str) -> Response:
    """Generic data-service proxy: /api/data/students -> GET /students, etc."""
    return await _proxy_to_data_service(request, f"/{entity_name}")


# --- RAG reverse proxy (документы для web UI) ---

RAG_SERVICE_URL = settings.rag_service_url


async def _proxy_to_rag(
    request: Request,
    rag_path: str,
    method: str = "GET",
    json_body: dict | None = None,
) -> Response:
    """Proxy к RAG-сервису напрямую."""
    http_client = request.app.state.http_client
    url = f"{RAG_SERVICE_URL}{rag_path}"
    headers = await _get_proxy_headers(request)
    if json_body is not None:
        response = await getattr(http_client, method.lower())(url, json=json_body, headers=headers)
    else:
        response = await getattr(http_client, method.lower())(url, headers=headers)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={"Content-Type": response.headers.get("Content-Type", "application/json")},
    )


@app.get("/api/rag/documents")
async def proxy_rag_documents(request: Request) -> Response:
    """GET-обёртка над POST /documents/list в RAG-сервисе."""
    return await _proxy_to_rag(request, "/documents/list", method="POST", json_body={})


# --- API Reverse Proxy Routes (только агент: chat, sessions, backlog) ---


@app.api_route("/api/tenant/{tenant_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_tenant_api(request: Request, tenant_id: str, path: str):
    """
    Special demo route: allows specifying tenant in URL.
    Example: /api/tenant/school-a/chat -> proxies to /api/chat with X-Tenant-ID: school-a
    """
    # Store tenant_id in request.state so proxy functions can access it
    request.state.tenant_id = tenant_id
    
    # Determine if we should proxy to data-service, rag, or api based on the path
    if path.startswith("data/"):
        return await _proxy_to_data_service(request, f"/{path.replace('data/', '', 1)}")
    elif path.startswith("rag/"):
        rag_subpath = path.replace('rag/', '', 1)
        # Special case: rag/documents -> POST /documents/list (matching /api/rag/documents behavior)
        if rag_subpath == "documents":
            return await _proxy_to_rag(request, "/documents/list", method="POST", json_body={})
        else:
            return await _proxy_to_rag(request, f"/{rag_subpath}")
    else:
        # Default to API
        is_sse = path == "chat" and request.method == "POST"
        # If path already starts with 'api/', use it directly (e.g. api/health -> /health)
        # Otherwise prepend 'api/' (e.g. chat -> /api/chat)
        if path.startswith("api/"):
            api_path = path.replace("api/", "", 1)
        else:
            api_path = f"api/{path}"
        return await _proxy_to_api(request, f"/{api_path}", stream=is_sse)


@app.get("/api/tenants")
async def get_tenants(request: Request) -> Response:
    """Return list of available tenants from data-service health endpoint.
    
    Falls back to [DEFAULT_TENANT_ID] if data-service returns single-tenant response.
    Also checks DEMO_TENANTS env var (comma-separated) as explicit override.
    """
    import json

    # Explicit override via env var (via settings)
    explicit = settings.demo_tenants.strip()
    if explicit:
        return Response(
            content=json.dumps({"tenants": [t.strip() for t in explicit.split(",") if t.strip()]}),
            media_type="application/json",
        )

    # Try to discover from data-service /health
    default_tenant = settings.default_tenant_id
    try:
        http_client = request.app.state.http_client
        url = f"{DATA_SERVICE_URL}/health"
        ds_resp = await http_client.get(url, timeout=5.0)
        if ds_resp.status_code == 200:
            import json
            data = ds_resp.json()
            if "tenants" in data and isinstance(data["tenants"], list):
                tenant_ids = [t["id"] for t in data["tenants"] if isinstance(t, dict) and "id" in t]
                if tenant_ids:
                    return Response(
                        content=json.dumps({"tenants": tenant_ids}),
                        media_type="application/json",
                    )
    except Exception:
        logger.debug("Failed to discover tenants from data-service", exc_info=True)

    # Fallback
    import json
    return Response(
        content=json.dumps({"tenants": [default_tenant]}),
        media_type="application/json",
    )


@app.get("/api/health")
async def proxy_health(request: Request) -> Response:
    return await _proxy_to_api(request, "/health")


@app.get("/api/backlog")
async def proxy_backlog(request: Request) -> Response:
    return await _proxy_to_api(request, "/api/backlog")


@app.get("/api/backlog/{session_id}")
async def proxy_backlog_detail(request: Request, session_id: str) -> Response:
    return await _proxy_to_api(request, f"/api/backlog/{session_id}")


@app.get("/api/session/history")
async def proxy_session_history(request: Request) -> Response:
    session_id = request.query_params.get("session_id", "")
    agent_name = request.query_params.get("agent_name")
    path = f"/api/session/history?session_id={session_id}"
    if agent_name:
        path += f"&agent_name={agent_name}"
    return await _proxy_to_api(request, path)


@app.post("/api/chat", response_model=None)
async def proxy_chat(request: Request):
    """Proxy the SSE chat endpoint."""
    return await _proxy_to_api(request, "/api/chat", stream=True)


@app.post("/api/chat/{agent_name}", response_model=None)
async def proxy_chat_by_agent(request: Request, agent_name: str):
    """Proxy SSE chat for a named agent."""
    return await _proxy_to_api(request, f"/api/chat/{agent_name}", stream=True)


# ── Embed widget proxy (from api-service /embed) ──


@app.get("/embed/{embed_path:path}")
async def proxy_embed(request: Request, embed_path: str):
    """Proxy embed widget static files from api-service."""
    return await _proxy_to_api(request, f"/embed/{embed_path}", stream=False)


# Catch-all for any other /api/* path
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], response_model=None)
async def proxy_api_any(request: Request, path: str):
    """Catch-all proxy for any undefined /api/* route."""
    is_sse = path == "chat" and request.method == "POST"
    return await _proxy_to_api(request, f"/api/{path}", stream=is_sse)


def main() -> None:
    """Run the web server."""
    import uvicorn

    uvicorn.run(
        "demo.web.server:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
