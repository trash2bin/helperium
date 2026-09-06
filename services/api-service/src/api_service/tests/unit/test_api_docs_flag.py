"""Regression tests for the API_ENABLE_DOCS opt-in Swagger/OpenAPI flag.

Docs, ReDoc and the OpenAPI HTTP routes must stay off by default
(control-plane hardening, bd508c1) and only come back when the flag is set.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_default_app(monkeypatch):
    """Leave api_service.server.app in its default (docs-off) state."""
    yield
    monkeypatch.delenv("API_ENABLE_DOCS", raising=False)
    importlib.reload(importlib.import_module("api_service.server.app"))


def _reload_app(monkeypatch, enable: str | None):
    # ``import api_service.server.app as app_mod`` would bind the FastAPI
    # instance re-exported by the package __init__, not the submodule.
    app_mod = importlib.import_module("api_service.server.app")
    if enable is None:
        monkeypatch.delenv("API_ENABLE_DOCS", raising=False)
    else:
        monkeypatch.setenv("API_ENABLE_DOCS", enable)
    return importlib.reload(app_mod).app


def test_docs_routes_absent_by_default(monkeypatch):
    app = _reload_app(monkeypatch, None)
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_falsy_flag_values_keep_docs_off(monkeypatch):
    for value in ("0", "false", "no", "off", "", "  ", "unexpected"):
        app = _reload_app(monkeypatch, value)
        client = TestClient(app)
        assert client.get("/docs").status_code == 404, repr(value)


def test_docs_routes_served_when_flag_enabled(monkeypatch):
    app = _reload_app(monkeypatch, "1")
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Helperium API"
