"""Regression tests: upload filename sanitization (path traversal).

The upload endpoint builds save_path from the client-controlled filename:

    safe_filename = os.path.basename(file.filename or "uploaded_document")
    save_path = os.path.join(upload_dir, safe_filename)

os.path.basename stops traversal like '../../etc/passwd', but it has edge
cases that are NOT sanitized:

    basename('..')  -> '..'   -> save_path = upload_dir/'..'  (a directory!)
    basename('.')   -> '.'    -> save_path = upload_dir/'.'   (a directory!)
    basename('')    -> ''     -> save_path = upload_dir itself

open(save_path, 'wb') then raises IsADirectoryError, which surfaces as an
unhandled 500 instead of a 422 validation error.

Contract under test:
  1. traversal names must be neutralized (file written INSIDE upload_dir);
  2. filenames that sanitize to '' / '.' / '..' must be rejected with 422,
     never leak an unhandled 500.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from rag.service import app, state
from helperium_sdk.rag.models import Document


@pytest.fixture(autouse=True)
def mock_state():
    with (
        patch.object(state, "get_pipeline") as mock_pipe,
        patch.object(state, "get_db") as mock_db,
    ):
        pipeline = MagicMock()
        pipeline.config.embedding_model = "test-model"
        pipeline.import_document.return_value = MagicMock(
            document=Document(
                id="doc-1",
                title="t",
                source_path="x",
                mime_type="text/plain",
                created_at="2026-01-01T00:00:00Z",
            ),
            chunks_count=1,
        )
        mock_pipe.return_value = pipeline
        mock_db.return_value = MagicMock()
        yield pipeline


@pytest.fixture()
def _mkdtemp_spy(tmp_path):
    """Patch tempfile.mkdtemp so every upload lands in our watched tmpdir."""
    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(*a, **kw):
        kw["dir"] = str(tmp_path)
        return real_mkdtemp(*a, **kw)

    with patch("rag.service.tempfile.mkdtemp", side_effect=fake_mkdtemp):
        yield


@pytest.mark.asyncio
async def test_traversal_filename_is_neutralized(mock_state, tmp_path):
    """../../etc/passwd must resolve INSIDE the upload dir, not outside."""
    pipeline = mock_state
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/documents/upload",
            files={"file": ("../../etc/passwd", b"hello", "text/plain")},
        )
    assert resp.status_code == 201, resp.text
    saved = Path(pipeline.import_document.call_args.kwargs["path"])
    # basename('..')/'.'/'' would normpath OUTSIDE the watched tmpdir root
    assert saved.parent.name.startswith("rag-upload-"), (
        f"save_path escaped the upload dir: {saved}"
    )
    assert saved.name == "passwd", f"traversal survived: {saved}"
    assert not (tmp_path.parent / "etc" / "passwd").exists()
    assert not Path("/tmp/passwd").exists()


@pytest.mark.asyncio
async def test_absolute_filename_is_neutralized(mock_state, tmp_path):
    pipeline = mock_state
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/documents/upload",
            files={"file": ("/abs/path/doc.txt", b"hello", "text/plain")},
        )
    assert resp.status_code == 201, resp.text
    saved = Path(pipeline.import_document.call_args.kwargs["path"])
    assert saved.parent.name.startswith("rag-upload-"), (
        f"save_path escaped the upload dir: {saved}"
    )
    assert saved.name == "doc.txt"
    assert not Path("/abs/doc.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["..", ".", ""])
async def test_dotdot_and_empty_filename_not_500(filename):
    """basename('..'/'.'/'') collapses to the upload dir itself -> must be 422.

    Currently FAILS: open(upload_dir/'..', 'wb') raises IsADirectoryError,
    the generic handler turns it into an unhandled 500.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/documents/upload",
            files={"file": (filename, b"hello", "text/plain")},
        )
    assert resp.status_code != 500, (
        f"filename={filename!r} leaks an unhandled 500 (got {resp.status_code}); "
        "sanitize to a 422 rejection"
    )
    assert resp.status_code in (200, 422)
