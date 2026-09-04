"""Тесты для reverse-proxy функций demo/web.

demo/web — это тонкий reverse-proxy + статический сервер.
Он проксирует:
  /api/data/*       -> data-service:8084 (read-only данные)
  /api/rag/documents -> rag:8082        (документы)
  /api/{chat,backlog,session/history} -> api-service:8081 (агент)

Эти тесты проверяют, что:
1. Endpoint'ы правильно проксируют на upstream-сервис
2. Bearer token пробрасывается если настроен
3. Correlation ID пробрасывается в upstream
4. Status code и body от upstream передаются клиенту без изменений
5. URL формируется правильно (api_host -> data-service / rag)

Запуск:
    uv run pytest demo/web/tests/unit/test_proxy.py -v
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from demo.settings import settings
from demo.web.server import app


@pytest.fixture
def client():
    """TestClient для FastAPI app demo/web.

    demo/web использует request.app.state.http_client для outbound HTTP.
    Подменяем его на httpx.AsyncClient с MockTransport — это позволяет
    respx перехватывать запросы через обычные @respx.mock декораторы.
    """
    with respx.mock:
        # Создаём клиент с явным transport (respx его подменяет через .mock)
        http_client = httpx.AsyncClient(timeout=30.0)
        app.state.http_client = http_client
        test_client = TestClient(app)
        yield test_client
        test_client.close()
        # respx context manager сам почистит моки


# === TENANT ROUTING PROXY ===


class TestTenantRoutingProxy:
    """Тесты для /api/tenant/{tenant_id}/{path:path} с пробросом X-Tenant-ID."""

    @respx.mock
    def test_proxy_tenant_data_routes_to_data_service(self, client):
        """GET /api/tenant/tenant-a/data/students -> data-service /students с X-Tenant-ID."""
        upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(200, json=[{"id": "1", "name": "Alice"}])
        )
        response = client.get("/api/tenant/tenant-a/data/students")
        assert response.status_code == 200
        assert response.json() == [{"id": "1", "name": "Alice"}]
        # Проверяем что X-Tenant-ID пробросился в upstream
        tenant_id_header = upstream_route.calls.last.request.headers.get("x-tenant-id")
        assert tenant_id_header == "tenant-a"

    @respx.mock
    def test_proxy_tenant_rag_routes_to_rag_service(self, client):
        """GET /api/tenant/tenant-b/rag/documents -> rag /documents/list с X-Tenant-ID."""
        upstream_route = respx.post("http://127.0.0.1:8082/documents/list").mock(
            return_value=httpx.Response(200, json={"documents": [{"id": "doc1"}]})
        )
        response = client.get("/api/tenant/tenant-b/rag/documents")
        assert response.status_code == 200
        assert "documents" in response.json()
        tenant_id_header = upstream_route.calls.last.request.headers.get("x-tenant-id")
        assert tenant_id_header == "tenant-b"

    @respx.mock
    def test_proxy_tenant_api_routes_to_api_service(self, client):
        """GET /api/tenant/tenant-c/api/health -> api /health с X-Tenant-ID."""
        upstream_route = respx.get("http://127.0.0.1:8081/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        response = client.get("/api/tenant/tenant-c/api/health")
        assert response.status_code == 200
        tenant_id_header = upstream_route.calls.last.request.headers.get("x-tenant-id")
        assert tenant_id_header == "tenant-c"

    @respx.mock
    def test_proxy_tenant_chat_uses_sse(self, client):
        """POST /api/tenant/tenant-d/chat -> api /api/chat с SSE и X-Tenant-ID."""
        upstream_route = respx.post("http://127.0.0.1:8081/api/chat").mock(
            return_value=httpx.Response(200, text="data: test\n\n")
        )
        response = client.post("/api/tenant/tenant-d/chat", json={"message": "hi"})
        assert response.status_code == 200
        tenant_id_header = upstream_route.calls.last.request.headers.get("x-tenant-id")
        assert tenant_id_header == "tenant-d"

    @respx.mock
    def test_proxy_tenant_passes_correlation_id(self, client):
        """Correlation ID пробрасывается вместе с tenant ID."""
        upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(200, json=[])
        )
        response = client.get(
            "/api/tenant/tenant-e/data/students",
            headers={"x-correlation-id": "test-corr-123"},
        )
        assert response.status_code == 200
        assert (
            upstream_route.calls.last.request.headers.get("x-correlation-id")
            == "test-corr-123"
        )
        assert (
            upstream_route.calls.last.request.headers.get("x-tenant-id") == "tenant-e"
        )


# === DATA SERVICE PROXY ===


class TestDataServiceProxy:
    """Тесты для /api/data/* -> data-service:8084."""

    @respx.mock
    def test_proxy_data_stats(self, client):
        """GET /api/data/stats -> GET data-service:8084/stats."""
        respx.get("http://127.0.0.1:8084/stats").mock(
            return_value=httpx.Response(200, json={"students": 42, "teachers": 15})
        )
        response = client.get("/api/data/stats")
        assert response.status_code == 200
        assert response.json() == {"students": 42, "teachers": 15}

    @respx.mock
    @pytest.mark.parametrize(
        "entity_key,upstream_path",
        [
            ("students", "/students"),
            ("teachers", "/teachers"),
            ("disciplines", "/disciplines"),
            ("schedule", "/schedule"),
            ("grades", "/grades"),
        ],
    )
    def test_proxy_generic_data_collection(self, client, entity_key, upstream_path):
        """Generic /api/data/{entity} proshens correct upstream path."""
        respx.get(f"http://127.0.0.1:8084{upstream_path}").mock(
            return_value=httpx.Response(200, json=[])
        )
        response = client.get(f"/api/data/{entity_key}")
        assert response.status_code == 200
        assert response.json() == []

    @respx.mock
    def test_proxy_passes_correlation_id(self, client):
        """Correlation ID из middleware пробрасывается в upstream."""
        upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(200, json=[])
        )
        response = client.get(
            "/api/data/students", headers={"x-correlation-id": "test-corr-id"}
        )
        assert response.status_code == 200
        # Проверяем что header был передан upstream
        assert (
            upstream_route.calls.last.request.headers.get("x-correlation-id")
            == "test-corr-id"
        )

    @respx.mock
    def test_proxy_upstream_5xx_passes_through(self, client):
        """Если data-service вернул 500 — прокси возвращает 500 клиенту."""
        respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(500, json={"detail": "db error"})
        )
        response = client.get("/api/data/students")
        assert response.status_code == 500
        assert response.json() == {"detail": "db error"}

    @respx.mock
    def test_proxy_upstream_404_passes_through(self, client):
        """Если data-service вернул 404 — прокси возвращает 404."""
        respx.get("http://127.0.0.1:8084/unknown").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        # Используем произвольный endpoint чтобы прокси ушёл на /unknown
        # Прямого endpoint'а для /unknown нет, поэтому мокаем любой путь и
        # дёргаем существующий endpoint — тестируем что код upstream'а передаётся
        respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        response = client.get("/api/data/students")
        assert response.status_code == 404

    @respx.mock
    def test_proxy_returns_json_content_type(self, client):
        """Response Content-Type из upstream сохраняется."""
        respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(200, json=[{"id": "1"}])
        )
        response = client.get("/api/data/students")
        assert response.headers["content-type"] == "application/json"

    @respx.mock
    def test_proxy_passes_tenant_id_to_data_service(self, client):
        """X-Tenant-ID из входящего запроса пробрасывается в data-service."""
        upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
            return_value=httpx.Response(200, json=[])
        )
        response = client.get(
            "/api/data/students", headers={"X-Tenant-ID": "tenant-xyz"}
        )
        assert response.status_code == 200
        tenant_id_header = upstream_route.calls.last.request.headers.get("x-tenant-id")
        assert tenant_id_header == "tenant-xyz"


# === MANIFEST PROXY ===


class TestManifestProxy:
    """Тест /api/manifest -> data-service /mcp/manifest."""

    @respx.mock
    def test_proxy_manifest(self, client):
        """GET /api/manifest проксирует на data-service GET /mcp/manifest."""
        manifest = {
            "entities": [{"name": "student", "fields": []}],
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/students",
                    "entity": "student",
                    "op": "strategy",
                }
            ],
            "custom_queries": {},
            "mcp_tools": [],
        }
        respx.get("http://127.0.0.1:8084/mcp/manifest").mock(
            return_value=httpx.Response(200, json=manifest)
        )
        response = client.get("/api/manifest")
        assert response.status_code == 200
        assert response.json()["entities"][0]["name"] == "student"

    @respx.mock
    def test_proxy_manifest_upstream_error(self, client):
        """Если data-service упал — возвращаем ошибку клиенту."""
        respx.get("http://127.0.0.1:8084/mcp/manifest").mock(
            return_value=httpx.Response(500, json={"detail": "error"})
        )
        response = client.get("/api/manifest")
        assert response.status_code == 500


# === RAG PROXY ===


class TestRagProxy:
    """Тесты для /api/rag/documents -> rag:8082."""

    @respx.mock
    def test_proxy_rag_documents_uses_post(self, client):
        """GET /api/rag/documents -> POST rag:8082/documents/list."""
        upstream_route = respx.post("http://127.0.0.1:8082/documents/list").mock(
            return_value=httpx.Response(
                200, json={"documents": [{"id": "doc1", "title": "T1"}]}
            )
        )
        response = client.get("/api/rag/documents")
        assert response.status_code == 200
        body = response.json()
        assert "documents" in body
        assert body["documents"][0]["id"] == "doc1"
        # Подтверждаем что ушёл именно POST
        assert upstream_route.calls.last.request.method == "POST"

    @respx.mock
    def test_proxy_rag_documents_empty_body(self, client):
        """POST в upstream уходит с пустым телом {} — это требование rag API."""
        upstream_route = respx.post("http://127.0.0.1:8082/documents/list").mock(
            return_value=httpx.Response(200, json={"documents": []})
        )
        response = client.get("/api/rag/documents")
        assert response.status_code == 200
        # Проверяем тело запроса
        sent_body = json.loads(upstream_route.calls.last.request.content)
        assert sent_body == {}

    @respx.mock
    def test_proxy_rag_upstream_error(self, client):
        """Если rag вернул 503 — прокси возвращает 503."""
        respx.post("http://127.0.0.1:8082/documents/list").mock(
            return_value=httpx.Response(503, json={"detail": "service unavailable"})
        )
        response = client.get("/api/rag/documents")
        assert response.status_code == 503


# === BEARER TOKEN PROPAGATION ===


class TestBearerTokenPropagation:
    """Если api_bearer_token настроен — он пробрасывается в upstream."""

    @respx.mock
    def test_bearer_token_passed_to_data_service(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
                return_value=httpx.Response(200, json=[])
            )
            response = client.get("/api/data/students")
            assert response.status_code == 200
            auth_header = upstream_route.calls.last.request.headers.get("authorization")
            assert auth_header == "Bearer secret-token-xyz"

    @respx.mock
    def test_bearer_token_passed_to_rag(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream_route = respx.post("http://127.0.0.1:8082/documents/list").mock(
                return_value=httpx.Response(200, json={"documents": []})
            )
            response = client.get("/api/rag/documents")
            assert response.status_code == 200
            auth_header = upstream_route.calls.last.request.headers.get("authorization")
            assert auth_header == "Bearer secret-token-xyz"

    @respx.mock
    def test_no_bearer_token_when_not_configured(self, client):
        """Если api_bearer_token is None — Authorization header не отправляется."""
        with patch.object(settings, "api_bearer_token", None):
            upstream_route = respx.get("http://127.0.0.1:8084/students").mock(
                return_value=httpx.Response(200, json=[])
            )
            response = client.get("/api/data/students")
            assert response.status_code == 200
            assert "authorization" not in upstream_route.calls.last.request.headers


# === HEALTH ENDPOINT ===


class TestHealthEndpoint:
    """Health-check самого demo/web."""

    def test_health_endpoint(self, client):
        """GET /health возвращает статус web-сервиса."""
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["web"] == "ok"
        assert "api_base_url" in body
        assert "token_configured" in body


# === STATIC ===


class TestStaticServing:
    """demo/web отдаёт статические файлы (HTML, CSS, JS)."""

    def test_serves_index_html(self, client):
        """GET / -> index.html из demo/web/static."""
        response = client.get("/")
        assert response.status_code == 200
        # Должен вернуть HTML
        assert "text/html" in response.headers["content-type"]
        # Проверяем что в HTML есть что-то от app
        assert (
            b"<html" in response.content.lower()
            or b"<!doctype" in response.content.lower()
        )


class TestApiRoutes:
    """demo-web exposes only explicitly supported API proxy routes."""

    @respx.mock
    def test_agents_list_is_forwarded_to_api_service(self, client):
        """The demo UI can load named agents through the explicit proxy route."""
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream_route = respx.get("http://127.0.0.1:8081/api/agents").mock(
                return_value=httpx.Response(
                    200, json={"agents": [{"name": "autoparts-assistant"}]}
                )
            )
            response = client.get("/api/agents")

        assert response.status_code == 200
        assert response.json() == {"agents": [{"name": "autoparts-assistant"}]}
        assert (
            upstream_route.calls.last.request.headers.get("authorization")
            == "Bearer secret-token-xyz"
        )

    def test_unknown_api_path_is_not_forwarded_when_bearer_is_configured(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            response = client.get("/api/unknown")

        assert response.status_code == 404
