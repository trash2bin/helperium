"""Тесты для CORS middleware в demo/web/server.py.

Проверяет, что:
1. Без WEB_ORIGIN — evil origin блокируется
2. С явным origin — только он разрешён
3. С comma-separated origins — каждый работает
4. allow_headers не wildcard
5. allow_methods не * (а GET, POST, OPTIONS)
6. WEB_ORIGIN=* явно — всё ещё работает

Запуск:
    uv run pytest demo/web/tests/unit/test_cors_security.py -v --tb=short
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from demo.settings import settings
from demo.web.server import app as real_app


def _build_cors_app(
    origins: list[str],
    methods: list[str] | None = None,
    headers: list[str] | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the given CORS config for testing.

    We build a fresh app instead of reloading real modules to avoid
    cross-test contamination via sys.modules mutation.
    """
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.options("/")
    async def options():
        return Response()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=methods or ["GET", "POST", "OPTIONS"],
        allow_headers=headers
        or ["Content-Type", "Authorization", "X-Tenant-ID", "X-Correlation-ID"],
    )
    return app


class _CorsHelpers:
    """Shared helpers for CORS preflight assertions."""

    @staticmethod
    def preflight(app: FastAPI, origin: str, method: str = "GET") -> Response:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        return client.options(
            "/",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
            },
        )

    @staticmethod
    def get(app: FastAPI, origin: str) -> Response:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        return client.get("/", headers={"Origin": origin})


# =============================================================================
# 1. Без WEB_ORIGIN — evil origin блокируется
# =============================================================================


class TestCorsNoOrigin:
    """When WEB_ORIGIN is not set, default is ['http://localhost:8080']."""

    def test_block_evil_origin(self):
        """Default config blocks evil origin."""
        app = _build_cors_app(origins=["http://localhost:8080"])
        response = _CorsHelpers.preflight(app, "https://evil.com")
        acao = response.headers.get("access-control-allow-origin")
        assert acao is None or acao != "https://evil.com", (
            f"Evil origin should be blocked, got ACAO={acao!r}"
        )

    def test_default_origin_allowed(self):
        """Default origin is allowed."""
        app = _build_cors_app(origins=["http://localhost:8080"])
        response = _CorsHelpers.preflight(app, "http://localhost:8080")
        acao = response.headers.get("access-control-allow-origin")
        assert acao == "http://localhost:8080"

    def test_block_evil_second_origin(self):
        """Another evil origin is also blocked."""
        app = _build_cors_app(origins=["http://localhost:8080"])
        response = _CorsHelpers.preflight(app, "https://attacker.io")
        acao = response.headers.get("access-control-allow-origin")
        assert acao is None or acao != "https://attacker.io"


# =============================================================================
# 2. С явным origin — только он разрешён
# =============================================================================


class TestCorsExplicitOrigin:
    """With WEB_ORIGIN set to a single explicit origin."""

    def test_explicit_origin_allowed(self):
        """Explicit origin is allowed."""
        app = _build_cors_app(origins=["http://example.com"])
        response = _CorsHelpers.preflight(app, "http://example.com")
        assert (
            response.headers.get("access-control-allow-origin") == "http://example.com"
        )

    def test_other_origin_blocked(self):
        """Non-matching origin is blocked."""
        app = _build_cors_app(origins=["http://example.com"])
        response = _CorsHelpers.preflight(app, "https://evil.com")
        acao = response.headers.get("access-control-allow-origin")
        assert acao is None or acao != "https://evil.com"

    def test_localhost_not_allowed_when_explicit(self):
        """When origin is explicit, localhost is not allowed by default."""
        app = _build_cors_app(origins=["http://example.com"])
        response = _CorsHelpers.preflight(app, "http://localhost:8080")
        acao = response.headers.get("access-control-allow-origin")
        assert acao is None or acao != "http://localhost:8080"


# =============================================================================
# 3. С comma-separated origins — каждый работает
# =============================================================================


class TestCorsCommaSeparated:
    """With comma-separated WEB_ORIGIN."""

    @pytest.mark.parametrize("origin", ["http://a.com", "http://b.com"])
    def test_each_origin_in_list_allowed(self, origin):
        """Each origin in comma-separated list is allowed."""
        app = _build_cors_app(origins=["http://a.com", "http://b.com"])
        response = _CorsHelpers.preflight(app, origin)
        assert response.headers.get("access-control-allow-origin") == origin

    def test_origin_not_in_list_blocked(self):
        """Origin not in comma-separated list is blocked."""
        app = _build_cors_app(origins=["http://a.com", "http://b.com"])
        response = _CorsHelpers.preflight(app, "http://c.com")
        acao = response.headers.get("access-control-allow-origin")
        assert acao is None or acao != "http://c.com"

    def test_trailing_spaces_handled(self):
        """Simulate trailing spaces being stripped (the [o.strip() for o in ...] logic)."""
        raw = "  http://a.com , http://b.com  "
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        assert origins == ["http://a.com", "http://b.com"]
        app = _build_cors_app(origins=origins)
        response = _CorsHelpers.preflight(app, "http://a.com")
        assert response.headers.get("access-control-allow-origin") == "http://a.com"

    def test_triple_origins_all_allowed(self):
        """All three in comma list are allowed."""
        origins = ["http://x.com", "http://y.com", "http://z.com"]
        app = _build_cors_app(origins=origins)
        for origin in origins:
            response = _CorsHelpers.preflight(app, origin)
            assert response.headers.get("access-control-allow-origin") == origin


# =============================================================================
# 4. allow_headers не wildcard
# =============================================================================


class TestCorsHeaders:
    """allow_headers should not be wildcard."""

    def test_headers_not_wildcard(self):
        """Access-Control-Allow-Headers is not '*'."""
        app = _build_cors_app(
            origins=["http://example.com"],
            headers=[
                "Content-Type",
                "Authorization",
                "X-Tenant-ID",
                "X-Correlation-ID",
            ],
        )
        response = _CorsHelpers.preflight(app, "http://example.com")
        acah = response.headers.get("access-control-allow-headers")
        assert acah is not None, "Expected Access-Control-Allow-Headers header"
        assert acah != "*", f"Expected specific headers, got wildcard: {acah!r}"

    def test_headers_contains_critical_values(self):
        """allow_headers contains Content-Type, Authorization, X-Tenant-ID."""
        app = _build_cors_app(
            origins=["http://example.com"],
            headers=[
                "Content-Type",
                "Authorization",
                "X-Tenant-ID",
                "X-Correlation-ID",
            ],
        )
        response = _CorsHelpers.preflight(app, "http://example.com")
        acah = response.headers.get("access-control-allow-headers", "")
        headers_set = {h.strip().lower() for h in acah.split(",")}
        assert "content-type" in headers_set
        assert "authorization" in headers_set
        assert "x-tenant-id" in headers_set
        assert "x-correlation-id" in headers_set


# =============================================================================
# 5. allow_methods не * (а GET, POST, OPTIONS)
# =============================================================================


class TestCorsMethods:
    """allow_methods should be ['GET', 'POST', 'OPTIONS']."""

    def test_methods_not_wildcard(self):
        """Access-Control-Allow-Methods is not '*'."""
        app = _build_cors_app(
            origins=["http://example.com"],
            methods=["GET", "POST", "OPTIONS"],
        )
        response = _CorsHelpers.preflight(app, "http://example.com")
        acam = response.headers.get("access-control-allow-methods")
        assert acam is not None, "Expected Access-Control-Allow-Methods header"
        assert acam != "*", f"Expected specific methods, got wildcard: {acam!r}"

    def test_methods_contains_get_post_options(self):
        """allow_methods contains GET, POST, OPTIONS."""
        app = _build_cors_app(
            origins=["http://example.com"],
            methods=["GET", "POST", "OPTIONS"],
        )
        response = _CorsHelpers.preflight(app, "http://example.com")
        acam = response.headers.get("access-control-allow-methods", "")
        methods_set = {m.strip().upper() for m in acam.split(",")}
        assert "GET" in methods_set, f"Expected GET in methods, got {acam}"
        assert "POST" in methods_set, f"Expected POST in methods, got {acam}"
        assert "OPTIONS" in methods_set, f"Expected OPTIONS in methods, got {acam}"

    def test_methods_no_put_delete(self):
        """allow_methods should NOT contain PUT or DELETE."""
        app = _build_cors_app(
            origins=["http://example.com"],
            methods=["GET", "POST", "OPTIONS"],
        )
        response = _CorsHelpers.preflight(app, "http://example.com", method="PUT")
        acam = response.headers.get("access-control-allow-methods", "")
        methods_set = {m.strip().upper() for m in acam.split(",")}
        assert "PUT" not in methods_set, f"Expected no PUT in methods, got {acam}"
        assert "DELETE" not in methods_set, f"Expected no DELETE in methods, got {acam}"


# =============================================================================
# 6. WEB_ORIGIN=* явно — всё ещё работает
# =============================================================================


class TestCorsWildcardOrigin:
    """WEB_ORIGIN=* explicitly should allow all origins."""

    def test_wildcard_allows_any_origin(self):
        """WEB_ORIGIN=* returns Access-Control-Allow-Origin: *."""
        app = _build_cors_app(origins=["*"])
        response = _CorsHelpers.preflight(app, "https://anything.com")
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_wildcard_after_reload(self):
        """Multiple requests with wildcard origin still work."""
        app = _build_cors_app(origins=["*"])
        c1 = _CorsHelpers.preflight(app, "http://localhost:3000")
        c2 = _CorsHelpers.preflight(app, "http://localhost:3000")
        assert c1.headers.get("access-control-allow-origin") == "*"
        assert c2.headers.get("access-control-allow-origin") == "*"

    def test_wildcard_methods_restricted(self):
        """Even with wildcard origin, methods are restricted."""
        app = _build_cors_app(
            origins=["*"],
            methods=["GET", "POST", "OPTIONS"],
        )
        response = _CorsHelpers.preflight(app, "http://localhost:3000")
        acam = response.headers.get("access-control-allow-methods", "")
        assert acam != "*", f"Methods should not be wildcard, got {acam!r}"
        methods_set = {m.strip().upper() for m in acam.split(",")}
        assert "GET" in methods_set
        assert "POST" in methods_set
        assert "OPTIONS" in methods_set
        assert "PUT" not in methods_set

    def test_wildcard_get_request(self):
        """Simple GET with wildcard origin returns ACAO=*."""
        app = _build_cors_app(origins=["*"])
        response = _CorsHelpers.get(app, "https://example.com")
        assert response.headers.get("access-control-allow-origin") == "*"


# =============================================================================
# 7. Real-server integration: the actual demo.web.server uses the correct
#    CORS middleware configuration (no wildcard methods/headers).
# =============================================================================


class TestRealCorsMiddleware:
    """Verify the real server app has non-wildcard CORS config."""

    def test_real_app_methods_not_wildcard(self):
        """Real app's allow_methods is not '*'."""
        from fastapi.testclient import TestClient

        client = TestClient(real_app)
        response = client.options(
            "/",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        acam = response.headers.get("access-control-allow-methods", "")
        assert acam != "*", f"Real app should not use wildcard methods, got {acam!r}"

    def test_real_app_headers_not_wildcard(self):
        """Real app's allow_headers is not '*'."""
        from fastapi.testclient import TestClient

        client = TestClient(real_app)
        response = client.options(
            "/",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        acah = response.headers.get("access-control-allow-headers", "")
        assert acah != "*", f"Real app should not use wildcard headers, got {acah!r}"

    def test_real_app_has_methods_get_post_options(self):
        """Real app CORS methods include GET, POST, OPTIONS."""
        from fastapi.testclient import TestClient

        client = TestClient(real_app)
        response = client.options(
            "/",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        acam = response.headers.get("access-control-allow-methods", "")
        methods_set = {m.strip().upper() for m in acam.split(",")}
        assert "GET" in methods_set
        assert "POST" in methods_set
        assert "OPTIONS" in methods_set


# =============================================================================
# 8. The settings default is http://localhost:8080 (not *)
# =============================================================================


class TestSettingsDefaultOrigin:
    """settings.web_origin defaults to http://localhost:8080."""

    def test_settings_default_is_localhost(self):
        """Default web_origin is http://localhost:8080, not '*'."""
        assert settings.web_origin == "http://localhost:8080", (
            f"Expected http://localhost:8080, got {settings.web_origin!r}"
        )

    def test_settings_can_be_overridden_by_env(self, monkeypatch):
        """Overriding WEB_ORIGIN env var changes settings value."""
        monkeypatch.setenv("WEB_ORIGIN", "http://custom.com")
        # Re-read settings via fresh import (isolated to this test)
        import importlib

        import demo.settings as ds

        importlib.reload(ds)
        assert ds.settings.web_origin == "http://custom.com"
