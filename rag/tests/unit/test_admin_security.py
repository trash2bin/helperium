"""Tests for Admin API security (fail-closed token protection).

Red:   ADMIN_API_TOKEN not configured → admin endpoints should be disabled (fail-closed)
Green: ADMIN_API_TOKEN configured → valid token succeeds, wrong token/missing token → 403
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from rag.service import app, state


@pytest.fixture(autouse=True)
def mock_state():
    """Мокаем состояние сервиса — без реального SQLite/ChromaDB."""
    with (
        patch.object(state, "get_pipeline") as mock_pipe,
        patch.object(state, "get_db") as mock_db,
    ):
        pipeline = MagicMock()
        db = MagicMock()
        mock_pipe.return_value = pipeline
        mock_db.return_value = db
        yield pipeline, db


@pytest.mark.asyncio
async def test_admin_config_failclosed_when_no_token_configured(mock_state):
    """ADMIN_API_TOKEN не задан → /admin/config должен вернуть 403 (fail-closed)."""
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/admin/config")
    assert response.status_code == 403
    assert "ADMIN_API_TOKEN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_admin_config_failclosed_when_no_token_configured_put(mock_state):
    """ADMIN_API_TOKEN не задан → PUT /admin/config тоже 403."""
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.put("/admin/config", json={"chunk_size": 512})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_stats_failclosed_when_no_token(mock_state):
    """ADMIN_API_TOKEN не задан → /admin/stats 403."""
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/admin/stats")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_config_requires_token_when_configured(mock_state):
    """ADMIN_API_TOKEN задан, но не передан → 403."""
    with patch("rag.service.ADMIN_API_TOKEN", "super-secret"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/admin/config")
    assert response.status_code == 403
    assert "X-Admin-Token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_admin_config_rejects_wrong_token(mock_state):
    """ADMIN_API_TOKEN задан, передан неверный токен → 403."""
    with patch("rag.service.ADMIN_API_TOKEN", "super-secret"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/admin/config", headers={"X-Admin-Token": "wrong-token"}
            )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_config_accepts_valid_token(mock_state):
    """ADMIN_API_TOKEN задан, передан корректный токен → 200."""
    with patch("rag.service.ADMIN_API_TOKEN", "super-secret"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/admin/config", headers={"X-Admin-Token": "super-secret"}
            )
    assert response.status_code == 200
    data = response.json()
    assert "embedding_provider" in data
