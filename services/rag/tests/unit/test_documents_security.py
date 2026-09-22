"""Pentest C2 regressions: RAG document surface security.

Mutating endpoints (/documents/import, /documents/upload, /documents/delete)
must be admin-token protected (fail-closed like /admin/*), and path-based
import must be confined to RAG_IMPORT_ROOT — arbitrary server paths were an
unauthenticated arbitrary-file-read (the .data/providers.json exfil).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from helperium_sdk.rag.models import Document
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


ADMIN_TOKEN = "secret-token"


# ── Auth (fail-closed) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_documents_import_failclosed_without_token(mock_state):
    pipeline, _ = mock_state
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post("/documents/import", json={"path": "x.txt"})
    assert response.status_code == 403
    pipeline.import_document.assert_not_called()


@pytest.mark.asyncio
async def test_documents_import_requires_valid_token(mock_state):
    pipeline, _ = mock_state
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": "x.txt"},
                headers={"X-Admin-Token": "wrong"},
            )
    assert response.status_code == 403
    pipeline.import_document.assert_not_called()


@pytest.mark.asyncio
async def test_documents_upload_failclosed_without_token(mock_state):
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/upload",
                files={"file": ("a.txt", b"hello", "text/plain")},
            )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_documents_delete_failclosed_without_token(mock_state):
    pipeline, _ = mock_state
    with patch("rag.service.ADMIN_API_TOKEN", ""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post("/documents/delete", json={"document_id": "x"})
    assert response.status_code == 403
    pipeline.repository.find_document_for_delete.assert_not_called() if hasattr(
        pipeline, "repository"
    ) else None


# ── Path confinement (RAG_IMPORT_ROOT) ──────────────────────────────


@pytest.mark.asyncio
async def test_documents_import_failclosed_when_root_unset(mock_state, monkeypatch):
    """Без RAG_IMPORT_ROOT path-based import недоступен даже с токеном."""
    pipeline, _ = mock_state
    monkeypatch.delenv("RAG_IMPORT_ROOT", raising=False)
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": "whatever.txt"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
    assert response.status_code in (403, 422)
    pipeline.import_document.assert_not_called()


@pytest.mark.asyncio
async def test_documents_import_rejects_absolute_path_outside_root(
    mock_state, monkeypatch, tmp_path
):
    """Абсолютный путь вне root (исторический эксплойт /etc/hosts, providers.json)."""
    pipeline, _ = mock_state
    monkeypatch.setenv("RAG_IMPORT_ROOT", str(tmp_path))
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": "/etc/hosts"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
    assert response.status_code == 422
    pipeline.import_document.assert_not_called()


@pytest.mark.asyncio
async def test_documents_import_rejects_traversal_escape(
    mock_state, monkeypatch, tmp_path
):
    """../ за пределы root — тоже отказ (resolve() после expanduser())."""
    pipeline, _ = mock_state
    monkeypatch.setenv("RAG_IMPORT_ROOT", str(tmp_path))
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": str(tmp_path / ".." / "outside.txt")},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
    assert response.status_code == 422
    pipeline.import_document.assert_not_called()


@pytest.mark.asyncio
async def test_documents_import_rejection_detail_is_sanitized(
    mock_state, monkeypatch, tmp_path
):
    """422 при выходе за root не раскрывает серверные filesystem-пути
    (контракт sanitized public errors)."""
    pipeline, _ = mock_state
    monkeypatch.setenv("RAG_IMPORT_ROOT", str(tmp_path))
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": "/etc/hosts"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
    assert response.status_code == 422
    body = response.text
    assert str(tmp_path) not in body
    assert "/etc/hosts" not in body


@pytest.mark.asyncio
async def test_documents_import_accepts_path_inside_root(
    mock_state, monkeypatch, tmp_path
):
    """Файл внутри root импортируется (контракт не теряем)."""
    pipeline, _ = mock_state
    monkeypatch.setenv("RAG_IMPORT_ROOT", str(tmp_path))
    mock_doc = Document(
        id="doc1",
        title="Doc",
        source_path="doc.txt",
        mime_type="text/plain",
        discipline_id=None,
        created_at="now",
    )
    pipeline.import_document.return_value = MagicMock(document=mock_doc, chunks_count=3)
    with patch("rag.service.ADMIN_API_TOKEN", ADMIN_TOKEN):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/documents/import",
                json={"path": str(tmp_path / "doc.txt")},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
    assert response.status_code == 201
    pipeline.import_document.assert_called_once()


# ── Parser allow-list ───────────────────────────────────────────────


def test_parser_plain_text_suffixes_exclude_config_formats():
    """.json/.py не должны парситься как plain text (pentest C2: чтение
    runtime-конфигов и исходников)."""
    from rag.parser.parser import PLAIN_TEXT_SUFFIXES

    assert ".json" not in PLAIN_TEXT_SUFFIXES
    assert ".py" not in PLAIN_TEXT_SUFFIXES
    assert ".txt" in PLAIN_TEXT_SUFFIXES
    assert ".md" in PLAIN_TEXT_SUFFIXES
    assert ".csv" in PLAIN_TEXT_SUFFIXES
