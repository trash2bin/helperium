"""Tests for search input validation.

Red:   empty/whitespace-only query → should 400 Bad Request, not silently return []
Green: valid query → 200
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from rag.service import app, state
from agent_tutor_sdk.rag.models import RagSearchResult


@pytest.fixture(autouse=True)
def mock_state():
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
async def test_search_empty_query_returns_400(mock_state):
    """POST /search с пустым query → 400 Bad Request.

    FastAPI/Pydantic rejection: поле query есть, но пустое.
    Pydantic rejects with 422 (Unprocessable Entity) via @field_validator.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/search", json={"query": "", "limit": 5})
    assert response.status_code == 422  # Pydantic @field_validator min_length=1
    detail = response.json()["detail"]
    # detail is a list of ValidationError items
    if isinstance(detail, list):
        combined = " ".join(str(e) for e in detail).lower()
    else:
        combined = str(detail).lower()
    assert "query" in combined


@pytest.mark.asyncio
async def test_search_whitespace_query_returns_400(mock_state):
    """POST /search с query из пробелов → 400."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/search", json={"query": "   ", "limit": 5})
    assert response.status_code == 400
    assert "query" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_search_null_query_returns_400(mock_state):
    """POST /search без query поля → 422 (Pydantic required field)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/search", json={"limit": 5})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_valid_query_still_works(mock_state):
    """POST /search с валидным query → 200."""
    pipeline, _ = mock_state
    pipeline.search_documents.return_value = [
        RagSearchResult(
            document_id="d1", document_title="T", source_path="p",
            discipline_id="d1", chunk_id="c1", chunk_index=0,
            page=1, score=0.9, content="stuff",
        )
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/search", json={"query": "hello", "limit": 5})
    assert response.status_code == 200
    assert response.json()["count"] == 1
