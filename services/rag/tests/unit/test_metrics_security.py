"""Pentest M1 regressions: RAG /metrics must be token-protected.

Metrics expose tenant labels and RAG statistics (recon for an attacker), so
the endpoint is gated by the same ADMIN_API_TOKEN as the admin endpoints —
fail-closed: without a token it answers 403. Both X-Admin-Token (curl/scripts)
and Authorization: Bearer (prometheus credentials_file) are accepted.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from rag.service import app

ADMIN_TOKEN = "secret-token"


@pytest.mark.asyncio
async def test_metrics_failclosed_without_token():
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/metrics")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_metrics_requires_token():
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/metrics")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_metrics_rejects_wrong_token():
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/metrics", headers={"X-Admin-Token": "wrong"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_metrics_accepted_with_x_admin_token():
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/metrics", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.text


@pytest.mark.asyncio
async def test_metrics_accepted_with_bearer_token():
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/metrics", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
            )
    assert response.status_code == 200
    assert response.text
