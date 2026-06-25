"""HTTP-фасад над RagClientSync для document_generator.py.

Шим оставлен потому, что document_generator.py был написан до того, как RAG
стал HTTP-сервисом: код ожидает атрибуты pipeline.repository / chunker /
vector_store. Здесь эти атрибуты фейковые — каждый метод делает один
HTTP-вызов к rag-сервису.

Если когда-нибудь захочется убрать шим — document_generator.py можно
переписать на прямые вызовы RagClientSync (import_document, delete_document,
list_documents). Сейчас шим минимизирует diff.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent_tutor_sdk.rag.client import RagClientSync, RAG_SERVICE_URL

logger = logging.getLogger(__name__)


class RagTools:
    """Фасад для document_generator.py. Работает через HTTP-клиент к RAG-сервису."""

    def __init__(self, db: Any = None) -> None:
        # Параметр db оставлен для обратной совместимости со старыми вызовами.
        self._client = RagClientSync(RAG_SERVICE_URL)
        self.pipeline = _FakePipeline(self._client)

    def import_document(self, path, discipline_id=None, title=None, on_progress=None):
        return self._client.import_document(
            path=path,
            discipline_id=discipline_id,
            title=title,
        )

    def list_documents(self, discipline_id=None):
        return self._client.list_documents(discipline_id=discipline_id)

    def _delete_document_vectors(self, document_id):
        self._client.delete_document(document_id=document_id)

    def search_documents(self, query, discipline_id=None, limit=5):
        return self._client.search_documents(query, discipline_id, limit)

    def build_rag_context(self, query, discipline_id=None, limit=5):
        return self._client.build_rag_context(query, discipline_id, limit)


class _FakePipeline:
    """Фейковый пайплайн для совместимости с document_generator.py."""

    def __init__(self, client: RagClientSync):
        self.client = client
        self.repository = _FakeRepository(client)
        self.chunker = _FakeChunker()
        self.vector_store = _FakeVectorStore()


class _FakeRepository:
    """Фейковый репозиторий для совместимости."""

    def __init__(self, client: RagClientSync):
        self.client = client

    def get_materials(self, discipline_id: str | None = None):
        from docgen._material import Material

        docs = self.client.list_documents(discipline_id=discipline_id)
        return [
            Material(
                id=doc.id,
                discipline_id=doc.discipline_id or "",
                type="document",
                title=doc.title,
                file_name=doc.source_path.split("/")[-1],
                source_path=doc.source_path,
                mime_type=doc.mime_type,
                content="",
            )
            for doc in docs
        ]

    def list_generated_document_rows(
        self, path_marker: str, discipline_id: str | None = None
    ):
        docs = self.client.list_documents(discipline_id=discipline_id)
        return [
            {"id": doc.id, "source_path": doc.source_path, "title": doc.title}
            for doc in docs
            if path_marker in doc.source_path
        ]

    def delete_document_record(self, document_id: str, commit: bool = True):
        self.client.delete_document(document_id=document_id)

    def save_document_with_chunks(
        self, source_path, chunks, discipline_id, title, vector_store
    ):
        try:
            result = self.client.import_document(
                path=source_path,
                discipline_id=discipline_id,
                title=title,
            )
            return result
        except Exception as e:
            logger.warning(f"Failed to save document via HTTP: {e}")
            raise

    def save_generated_document_fallback(self, path, discipline_id, title, text):
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(text, encoding="utf-8")
        self.client.import_document(
            path=path,
            discipline_id=discipline_id,
            title=title,
        )


class _FakeChunker:
    """Фейковый чанкер для совместимости."""

    def chunk_pages(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks = []
        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page")
            if text:
                chunks.append({"page": page_num, "content": text})
        return chunks


class _FakeVectorStore:
    """Фейковый векторный store для совместимости."""

    pass
