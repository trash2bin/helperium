"""CORS security tests for rag/service.py.

Tests that CORS configuration is secure by default and follows
the principle of least privilege.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module", autouse=True)
def _preserve_and_restore_service_module():
    """Save and restore the original rag.service module __dict__.

    CORS tests reload the module with different env variables which
    creates new app/state objects. This fixture saves and restores
    the complete module namespace so other test files (test_service.py)
    are not affected by the in-place mutation from importlib.reload.
    """
    import rag.service as sv

    saved = dict(sv.__dict__)
    yield
    sv.__dict__.clear()
    sv.__dict__.update(saved)
    # Ensure __name__ etc. are correct
    sv.__name__ = saved.get("__name__", "rag.service")
    sv.__package__ = saved.get("__package__", "rag")


def _reload_app(monkeypatch, cors_origins=None):
    """Reload rag.service with a given CORS_ALLOW_ORIGINS env var.

    Uses monkeypatch to set/delete the env var, then triggers a full
    module reload so the module-level CORS middleware is rebuilt.
    """
    if cors_origins is None:
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", cors_origins)
    import rag.service as sv

    # Clean up module-level app so reload rebuilds it
    if hasattr(sv, "app"):
        del sv.app
    importlib.reload(sv)
    return sv.app


# ---------------------------------------------------------------------------
# No env → default origin = http://localhost:8080
# ---------------------------------------------------------------------------


class TestCORSDefaults:
    """Без CORS_ALLOW_ORIGINS — default http://localhost:8080, evil origin blocked."""

    def test_default_origin_allowed(self, monkeypatch):
        app = _reload_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://localhost:8080"})
        assert (
            resp.headers.get("access-control-allow-origin") == "http://localhost:8080"
        )

    def test_evil_origin_blocked_on_get(self, monkeypatch):
        app = _reload_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://evil.com"})
        acao = resp.headers.get("access-control-allow-origin", "MISSING")
        assert acao not in ("http://evil.com", "*")

    def test_evil_origin_blocked_on_preflight(self, monkeypatch):
        app = _reload_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.options(
                "/health",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        acao = resp.headers.get("access-control-allow-origin", "MISSING")
        assert acao not in ("http://evil.com", "*")


# ---------------------------------------------------------------------------
# Explicit single origin
# ---------------------------------------------------------------------------


class TestCORSExplicitOrigin:
    """CORS_ALLOW_ORIGINS=http://example.com — только этот origin разрешён."""

    def test_allowed_origin(self, monkeypatch):
        app = _reload_app(monkeypatch, "http://example.com")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://example.com"})
        assert resp.headers.get("access-control-allow-origin") == "http://example.com"

    def test_evil_origin_blocked(self, monkeypatch):
        app = _reload_app(monkeypatch, "http://example.com")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://evil.com"})
        acao = resp.headers.get("access-control-allow-origin", "MISSING")
        assert acao not in ("http://evil.com", "*", "http://example.com")


# ---------------------------------------------------------------------------
# Comma-separated origins
# ---------------------------------------------------------------------------


class TestCORSCommaSeparated:
    """CORS_ALLOW_ORIGINS=http://a.com,http://b.com — оба работают."""

    @pytest.mark.parametrize("origin", ["http://a.com", "http://b.com"])
    def test_allowed_origins(self, monkeypatch, origin):
        app = _reload_app(monkeypatch, "http://a.com,http://b.com")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": origin})
        assert resp.headers.get("access-control-allow-origin") == origin

    def test_evil_origin_blocked(self, monkeypatch):
        app = _reload_app(monkeypatch, "http://a.com,http://b.com")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://evil.com"})
        acao = resp.headers.get("access-control-allow-origin", "MISSING")
        assert acao not in ("http://evil.com", "*")


# ---------------------------------------------------------------------------
# allow_headers — не wildcard
# ---------------------------------------------------------------------------


class TestCORSAllowHeadersSpecific:
    """Проверка, что allow_headers — конкретные, не *."""

    def test_allow_headers_has_expected_values(self, monkeypatch):
        app = _reload_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert "*" not in allow_headers
        expected = ["content-type", "authorization", "x-tenant-id", "x-correlation-id"]
        for h in expected:
            assert h in allow_headers.lower(), (
                f"Expected header {h!r} in access-control-allow-headers, "
                f"got {allow_headers!r}"
            )


# ---------------------------------------------------------------------------
# allow_methods — не wildcard
# ---------------------------------------------------------------------------


class TestCORSAllowMethodsSpecific:
    """Проверка, что allow_methods — конкретные, не *."""

    def test_allow_methods_has_expected_values(self, monkeypatch):
        app = _reload_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "*" not in allow_methods
        for m in ["GET", "POST", "OPTIONS"]:
            assert m in allow_methods, (
                f"Expected method {m!r} in access-control-allow-methods, "
                f"got {allow_methods!r}"
            )


# ---------------------------------------------------------------------------
# Явный CORS_ALLOW_ORIGINS=*
# ---------------------------------------------------------------------------


class TestCORSStar:
    """CORS_ALLOW_ORIGINS=* — всё ещё работает (production/embed override)."""

    def test_any_origin_allowed(self, monkeypatch):
        app = _reload_app(monkeypatch, "*")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://anything.com"})
        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Пустой CORS_ALLOW_ORIGINS="" — fail-secure (не *, а fallback)
# ---------------------------------------------------------------------------


class TestCORSEmptyString:
    """CORS_ALLOW_ORIGINS="" — fail-secure: не *, а default localhost:8080."""

    def test_evil_origin_blocked(self, monkeypatch):
        app = _reload_app(monkeypatch, "")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://evil.com"})
        acao = resp.headers.get("access-control-allow-origin", "MISSING")
        assert acao not in ("http://evil.com", "*")

    def test_default_localhost_allowed(self, monkeypatch):
        app = _reload_app(monkeypatch, "")
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": "http://localhost:8080"})
        assert (
            resp.headers.get("access-control-allow-origin") == "http://localhost:8080"
        )
