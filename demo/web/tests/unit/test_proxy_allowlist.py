"""Pentest H1: demo/web proxy must not expose the admin control plane.

- /api/backlog routes are removed (conversation evidence is not for the demo).
- The tenant-route default branch is allowlisted (chat/reports/health/embed
  only) so /api/tenant/{id}/agents|backlog|voice-config|admin/* are 404.
- X-Full-Keys is never forwarded upstream (api-service secrets stay masked).
- /api/tenants no longer discovers the tenant inventory via data-service /health.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from demo.settings import settings
from demo.web.server import _get_proxy_headers, app

from starlette.requests import Request


async def _receive() -> dict:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def _make_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/agents",
            "raw_path": b"/api/agents",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive=_receive,
    )


@pytest.mark.asyncio
async def test_proxy_headers_never_forward_x_full_keys() -> None:
    """A browser cannot ask api-service for unmasked secrets via the demo."""
    request = _make_request([(b"x-full-keys", b"1")])
    headers = await _get_proxy_headers(request)
    assert "x-full-keys" not in headers


@pytest.mark.asyncio
async def test_proxy_headers_never_forward_other_full_keys_variants() -> None:
    """Capitalization/whitespace variants are dropped too (headers are lowercased)."""
    request = _make_request([(b"X-Full-Keys", b"true"), (b"X-Full-Keys", b"yes")])
    headers = await _get_proxy_headers(request)
    assert "x-full-keys" not in headers


@pytest.fixture
def client():
    with respx.mock:
        http_client = httpx.AsyncClient(timeout=30.0)
        app.state.http_client = http_client
        test_client = TestClient(app)
        yield test_client
        test_client.close()


class TestBacklogRemoved:
    """Conversation evidence routes no longer exist on demo/web."""

    @respx.mock
    def test_backlog_list_is_not_proxied(self, client):
        assert client.get("/api/backlog").status_code == 404

    @respx.mock
    def test_backlog_detail_is_not_proxied(self, client):
        assert client.get("/api/backlog/some-session").status_code == 404


class TestTenantRouteAllowlist:
    """The tenant route must not be a generic admin proxy."""

    @respx.mock
    def test_tenant_route_blocks_agents(self, client):
        assert client.get("/api/tenant/shop-a/agents").status_code == 404

    @respx.mock
    def test_tenant_route_blocks_backlog(self, client):
        assert client.get("/api/tenant/shop-a/backlog").status_code == 404

    @respx.mock
    def test_tenant_route_blocks_admin_provider_routes(self, client):
        assert (
            client.get("/api/tenant/shop-a/admin/llm-providers").status_code == 404
        )

    @respx.mock
    def test_tenant_route_blocks_plain_admin_path(self, client):
        assert (
            client.get("/api/tenant/shop-a/api/backlog").status_code == 404
        )

    @respx.mock
    def test_tenant_route_still_proxies_chat(self, client):
        """The demo's whole reason to exist: tenant-scoped chat."""
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream_route = respx.post(
                "http://127.0.0.1:8081/api/chat"
            ).mock(return_value=httpx.Response(200, content=b"ok"))
            response = client.post("/api/tenant/shop-a/chat", json={"message": "hi"})

        assert response.status_code == 200
        assert upstream_route.calls.last.request.headers.get("x-tenant-id") == "shop-a"

    @respx.mock
    def test_tenant_route_still_proxies_agent_chat(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream_route = respx.post(
                "http://127.0.0.1:8081/api/chat/autoparts-assistant"
            ).mock(return_value=httpx.Response(200, content=b"ok"))
            response = client.post(
                "/api/tenant/shop-a/chat/autoparts-assistant",
                json={"message": "hi"},
            )

        assert response.status_code == 200
        assert upstream_route.calls.last.request.url.path.endswith(
            "/api/chat/autoparts-assistant"
        )


class TestTenantsNoDiscovery:
    """/api/tenants must not enumerate the data-service inventory."""

    @respx.mock
    def test_tenants_uses_demo_tenants_env_only(self, client):
        with patch.object(settings, "demo_tenants", "shop-a, shop-b"):
            data_health = respx.get("http://127.0.0.1:8084/health").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "tenants": [
                            {"id": "e2e-secret-tenant"},
                            {"id": "test-fg"},
                        ]
                    },
                )
            )
            response = client.get("/api/tenants")

        assert response.status_code == 200
        assert response.json() == {"tenants": ["shop-a", "shop-b"]}
        assert not data_health.called, (
            "data-service /health must not be queried for tenant discovery"
        )

    @respx.mock
    def test_tenants_falls_back_to_default(self, client):
        with (
            patch.object(settings, "demo_tenants", ""),
            patch.object(settings, "default_tenant_id", "shop-a"),
        ):
            data_health = respx.get("http://127.0.0.1:8084/health").mock(
                return_value=httpx.Response(200, json={"tenants": []})
            )
            response = client.get("/api/tenants")

        assert response.status_code == 200
        assert response.json() == {"tenants": ["shop-a"]}
        assert not data_health.called
