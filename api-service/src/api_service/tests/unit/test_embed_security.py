"""Tests for embed widget security headers.

Expected security posture for /embed/* files:
  - X-Content-Type-Options: nosniff    (prevents MIME sniffing)
  - X-Frame-Options: DENY              (prevents framing)
  - Cache-Control with max-age >= 3600 (JS/CSS must be cacheable)
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _get_client():
    import api_service.server as sv

    if hasattr(sv, "app"):
        del sv.app

    importlib.reload(sv)
    return TestClient(sv.app)


def test_embed_js_has_x_content_type_options():
    """GET /embed/embed.js must return X-Content-Type-Options: nosniff."""
    with _get_client() as client:
        resp = client.get("/embed/embed.js")
        assert resp.status_code == 200
        assert resp.headers.get("x-content-type-options") == "nosniff", (
            f"Expected X-Content-Type-Options: nosniff, got {resp.headers.get('x-content-type-options')}"
        )


def test_embed_js_has_x_frame_options():
    """GET /embed/embed.js must return X-Frame-Options: DENY."""
    with _get_client() as client:
        resp = client.get("/embed/embed.js")
        assert resp.status_code == 200
        assert resp.headers.get("x-frame-options") == "DENY", (
            f"Expected X-Frame-Options: DENY, got {resp.headers.get('x-frame-options')}"
        )


def test_embed_js_has_cache_control():
    """GET /embed/embed.js must return Cache-Control with max-age >= 3600."""
    with _get_client() as client:
        resp = client.get("/embed/embed.js")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "max-age=" in cc, f"Cache-Control missing max-age, got: {cc}"
        # Extract max-age value
        parts = cc.split("max-age=")
        assert len(parts) > 1, f"Cannot parse max-age from: {cc}"
        max_age_str = parts[1].split(",")[0].strip()
        max_age = int(max_age_str)
        assert max_age >= 3600, f"max-age={max_age} is too low, need >= 3600"


def test_embed_css_has_x_content_type_options():
    """GET /embed/embed.css must return X-Content-Type-Options: nosniff."""
    with _get_client() as client:
        resp = client.get("/embed/embed.css")
        assert resp.status_code == 200
        assert resp.headers.get("x-content-type-options") == "nosniff"


def test_embed_css_has_x_frame_options():
    """GET /embed/embed.css must return X-Frame-Options: DENY."""
    with _get_client() as client:
        resp = client.get("/embed/embed.css")
        assert resp.status_code == 200
        assert resp.headers.get("x-frame-options") == "DENY"


def test_embed_css_has_cache_control():
    """GET /embed/embed.css must return Cache-Control with max-age >= 3600."""
    with _get_client() as client:
        resp = client.get("/embed/embed.css")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "max-age=" in cc, f"Cache-Control missing max-age, got: {cc}"
        parts = cc.split("max-age=")
        max_age = int(parts[1].split(",")[0].strip())
        assert max_age >= 3600, f"max-age={max_age} too low, need >= 3600"


def test_embed_nonexistent_returns_404():
    """GET /embed/nonexistent must return 404, not 500 or expose internals."""
    with _get_client() as client:
        resp = client.get("/embed/this-file-does-not-exist.xyz")
        assert resp.status_code == 404, (
            f"Expected 404 for nonexistent embed file, got {resp.status_code}: {resp.text[:200]}"
        )
        # Response must not expose server internals
        assert "traceback" not in resp.text.lower()
        assert "file not found" in resp.text.lower() or "not found" in resp.text.lower()


def test_embed_nonexistent_no_security_headers_leak():
    """404 response for nonexistent embed file should also get security headers."""
    with _get_client() as client:
        resp = client.get("/embed/this-file-does-not-exist.xyz")
        assert resp.status_code == 404
        # Security headers should still be present even on 404
        assert resp.headers.get("x-content-type-options") == "nosniff", (
            "404 should still have X-Content-Type-Options"
        )
        assert resp.headers.get("x-frame-options") == "DENY", (
            "404 should still have X-Frame-Options"
        )
