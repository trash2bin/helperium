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
    http_client = httpx.AsyncClient(timeout=30.0)
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
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

DATA_SERVICE_URL = f"http://{settings.api_host.replace('api', 'data-service')}:8084"
# Параметр api_host='api' в docker-compose — web ходит к api через DEMO_API_HOST.
# Для data-service: тот же hostname с заменой api→data-service.
# В docker-compose оба сервиса в одной сети, поэтому web видит data-service напрямую.


async def _proxy_to_data_service(
    request: Request,
    data_path: str,
) -> Response:
    """Proxy GET request to data-service напрямую (минуя demo/api).

    data-service — единственный владелец БД. Web не должен идти к data-service
    через demo/api, потому что это лишний hop и demo/api не должен заниматься
    трансляцией данных (его ответственность — агент).
    """
    http_client = request.app.state.http_client
    url = f"{DATA_SERVICE_URL}{data_path}"
    correlation_id = getattr(request.state, "correlation_id", "")
    headers = {
        "accept": "application/json",
        "x-correlation-id": correlation_id,
    }
    if settings.api_bearer_token:
        headers["authorization"] = f"Bearer {settings.api_bearer_token}"
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

RAG_SERVICE_URL = f"http://{settings.api_host.replace('api', 'rag')}:8082"


async def _proxy_to_rag(
    request: Request,
    rag_path: str,
    method: str = "GET",
    json_body: dict | None = None,
) -> Response:
    """Proxy к RAG-сервису напрямую."""
    http_client = request.app.state.http_client
    url = f"{RAG_SERVICE_URL}{rag_path}"
    correlation_id = getattr(request.state, "correlation_id", "")
    headers = {
        "accept": "application/json",
        "x-correlation-id": correlation_id,
    }
    if settings.api_bearer_token:
        headers["authorization"] = f"Bearer {settings.api_bearer_token}"
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
    path = f"/api/session/history?session_id={session_id}"
    return await _proxy_to_api(request, path)


@app.post("/api/chat", response_model=None)
async def proxy_chat(request: Request):
    """Proxy the SSE chat endpoint."""
    return await _proxy_to_api(request, "/api/chat", stream=True)


# Catch-all for any other /api/* path
@app.api_route("/api/{path:path}", methods=["*"], response_model=None)
async def proxy_api_any(request: Request, path: str):
    """Catch-all proxy for any undefined /api/* route."""
    is_sse = path == "chat" and request.method == "POST"
    return await _proxy_to_api(request, f"/{path}", stream=is_sse)


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
