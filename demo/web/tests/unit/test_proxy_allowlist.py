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
from demo.web.server import _get_base_proxy_headers as _get_proxy_headers, app

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


class TestAgentsPublicProjection:
    """Pentest F1: /api/agents through the demo edge must be a public projection.

    The proxy used to forward the full upstream payload (system prompt, LLM
    provider/model, masked key, provider priority) with its server bearer.
    The demo UI only consumes agent names.
    """

    UPSTREAM_BODY = {
        "agents": [
            {
                "name": "autoparts-assistant",
                "tenant_ids": ["autoparts"],
                "llm_config": {
                    "provider": "nvidia_nim",
                    "api_key": "nvapi-secret-value",
                    "model": "nvidia_nim/test-model",
                },
                "system_prompt": "SECRET SYSTEM PROMPT",
                "provider_priority": ["primary"],
            }
        ]
    }

    @respx.mock
    def test_agents_response_is_projected_to_public_fields(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream = respx.get("http://127.0.0.1:8081/api/agents").mock(
                return_value=httpx.Response(200, json=self.UPSTREAM_BODY)
            )
            response = client.get("/api/agents")

        assert response.status_code == 200
        assert upstream.called
        body = response.json()
        assert [a["name"] for a in body["agents"]] == ["autoparts-assistant"]
        text = response.text
        for secret_marker in ("llm_config", "system_prompt", "api_key", "provider_priority"):
            assert secret_marker not in text, secret_marker


class TestSessionHistoryCapabilityForwarding:
    """Pentest F4: history reads ride the session capability, not the server bearer.

    The proxy must not attach its control-plane bearer to transcript reads
    (that made any session_id readable anonymously) and must forward the
    browser's X-Session-Token so the capability check happens upstream.
    """

    @respx.mock
    def test_history_never_injects_bearer_and_forwards_session_token(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream = respx.get("http://127.0.0.1:8081/api/session/history").mock(
                return_value=httpx.Response(200, json={"messages": []})
            )
            response = client.get(
                "/api/session/history?session_id=sess-1&agent_name=demo",
                headers={"X-Session-Token": "cap-token-123"},
            )

        assert response.status_code == 200
        request = upstream.calls.last.request
        assert request.headers.get("authorization") is None, (
            "server bearer must not authenticate transcript reads"
        )
        assert request.headers.get("x-session-token") == "cap-token-123"

    @respx.mock
    def test_history_without_session_token_still_has_no_bearer(self, client):
        with patch.object(settings, "api_bearer_token", "secret-token-xyz"):
            upstream = respx.get("http://127.0.0.1:8081/api/session/history").mock(
                return_value=httpx.Response(401, json={"detail": "no"})
            )
            response = client.get("/api/session/history?session_id=sess-1")

        assert response.status_code == 401
        assert upstream.calls.last.request.headers.get("authorization") is None


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
