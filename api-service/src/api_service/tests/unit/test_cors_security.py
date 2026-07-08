"""CORS security tests for api-service.

Verifies that CORS defaults are fail-secure:
- Without env override → evil origin is blocked
- With explicit origins → only those origins are allowed
- allow_headers is specific, not wildcard
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _reload_app(monkeypatch, cors_origins: str | None = None):
    """Reload api_service.server with a specific CORS_ALLOW_ORIGINS value.

    Since CORS config is read at module level, we need to reload the
    module after setting the env var.
    """
    if cors_origins is None:
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", cors_origins)

    import api_service.server as sv

    # Clean up the old app's state
    if hasattr(sv, "app"):
        del sv.app

    importlib.reload(sv)
    return sv.app


class TestCorsDefaults:
    """When CORS_ALLOW_ORIGINS is NOT set — default should be fail-secure."""

    def test_preflight_from_evil_origin_is_blocked(self, monkeypatch):
        """Preflight OPTIONS from evil.com must NOT return Access-Control-Allow-Origin."""
        app = _reload_app(monkeypatch, cors_origins=None)
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow != "*", (
            "Default CORS should NOT be wildcard — evil.com must not be allowed"
        )

    def test_simple_request_from_evil_origin_has_no_cors_header(self, monkeypatch):
        """A real GET from evil origin should not include CORS allow header."""
        app = _reload_app(monkeypatch, cors_origins=None)
        with TestClient(app) as client:
            resp = client.get(
                "/api/agents",
                headers={"Origin": "http://evil.com"},
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow != "*", (
            "Default CORS should NOT be wildcard on simple requests either"
        )

    def test_allow_headers_is_not_wildcard(self, monkeypatch):
        """allow_headers should be specific, not ['*']."""
        app = _reload_app(monkeypatch, cors_origins=None)
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization, content-type",
                },
            )
        # Access-Control-Allow-Headers should not be "*"
        aclh = resp.headers.get("access-control-allow-headers", "")
        assert aclh != "", "There should be some Allow-Headers"
        assert aclh != "*", "allow_headers should be specific, not wildcard"


class TestCorsWithExplicitOrigin:
    """When CORS_ALLOW_ORIGINS is explicitly set."""

    def test_allowed_origin_receives_cors_header(self, monkeypatch):
        """Good origin from the list should get Access-Control-Allow-Origin."""
        app = _reload_app(monkeypatch, cors_origins="http://good.com")
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://good.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow == "http://good.com"

    def test_evil_origin_blocked_when_explicit_list(self, monkeypatch):
        """Evil origin NOT in the list should NOT get CORS header."""
        app = _reload_app(monkeypatch, cors_origins="http://good.com")
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow != "http://evil.com"

    def test_multiple_origins_all_returned_correctly(self, monkeypatch):
        """With comma-separated origins, each allowed origin must match."""
        app = _reload_app(monkeypatch, cors_origins="http://app1.com,http://app2.com")
        with TestClient(app) as client:
            resp1 = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://app1.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            resp2 = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://app2.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            resp3 = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp1.headers.get("access-control-allow-origin") == "http://app1.com"
        assert resp2.headers.get("access-control-allow-origin") == "http://app2.com"
        assert resp3.headers.get("access-control-allow-origin") != "http://app1.com"


class TestCorsEnvVarOverride:
    """Explicit CORS_ALLOW_ORIGINS=* should still work (for embed/production)."""

    def test_wildcard_still_works_when_explicit(self, monkeypatch):
        """Setting CORS_ALLOW_ORIGINS=* explicitly should allow all."""
        app = _reload_app(monkeypatch, cors_origins="*")
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://any-origin.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow == "*"

    def test_empty_cors_env_falls_to_secure_default(self, monkeypatch):
        """CORS_ALLOW_ORIGINS='' should be equivalent to not set (fail-secure)."""
        app = _reload_app(monkeypatch, cors_origins="")
        with TestClient(app) as client:
            resp = client.options(
                "/api/agents",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert allow != "*"
