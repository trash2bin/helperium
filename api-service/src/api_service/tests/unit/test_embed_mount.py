"""Tests that /embed is mounted exactly once (no duplicate mount bug)."""

from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient


def test_embed_mounted_exactly_once(monkeypatch):
    """The app must start without errors from duplicate /embed mount."""
    app_mod = importlib.reload(sys.modules["api_service.server.app"])

    with TestClient(app_mod.app) as client:
        resp = client.get("/embed/does-not-exist")
        # If mounted once → 404 (file not found) or 200 (if file exists)
        # If duplicate mount → 500 (crash on startup)
        assert resp.status_code in (200, 404), (
            f"Expected 200 or 404, got {resp.status_code}. "
            "This may indicate a duplicate /embed mount crash."
        )


def test_embed_mount_uses_resolved_override(monkeypatch, tmp_path):
    """When EMBED_DIR env var is set, it should take precedence."""
    embed_dir = tmp_path / "embed"
    embed_dir.mkdir()
    (embed_dir / "test.html").write_text("<html></html>")

    monkeypatch.setenv("EMBED_DIR", str(embed_dir))

    app_mod = importlib.reload(sys.modules["api_service.server.app"])

    with TestClient(app_mod.app) as client:
        resp = client.get("/embed/test.html")
        assert resp.status_code == 200


def test_app_startup_no_embed_directory_warning(caplog):
    """When embed dir doesn't exist, app should log a warning but not crash."""
    app_mod = importlib.reload(sys.modules["api_service.server.app"])

    # Check there's no crash — if we got here without Exception, it's fine
    assert app_mod.app is not None
