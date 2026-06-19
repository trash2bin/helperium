"""Фасад RagTools для обратной совместимости.

Этот модуль оставлен для совместимости с fixtures/document_generator.py.
Новый код должен использовать rag.client.RagClient напрямую.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from rag.client import RagClient, RAG_SERVICE_URL

logger = logging.getLogger(__name__)


class RagTools:
    """Фасад для обратной совместимости. Работает через HTTP-клиент к RAG-сервису.
    
    Используется только в fixtures/document_generator.py.
    """

    def __init__(
        self,
        db: Any,
        **kwargs,
    ) -> None:
        # db игнорируется - используется HTTP-клиент
        self._client = RagClient(RAG_SERVICE_URL)
        self.pipeline = _FakePipeline(self._client)

    def import_document(self, path, discipline_id=None, title=None, on_progress=None):
        return self._client.import_document_sync(
            path=path, 
            discipline_id=discipline_id, 
            title=title,
        )

    def list_documents(self, discipline_id=None):
        return self._client.list_documents_sync(discipline_id=discipline_id)

    def _delete_document_vectors(self, document_id):
        self._client.delete_document_sync(document_id=document_id)

    def search_documents(self, query, discipline_id=None, limit=5):
        return self._client.search_documents_sync(query, discipline_id, limit)

    def build_rag_context(self, query, discipline_id=None, limit=5):
        return self._client.build_rag_context_sync(query, discipline_id, limit)


class _FakePipeline:
    """Фейковый пайплайн для совместимости с document_generator.py."""
    
    def __init__(self, client: RagClient):
        self.client = client
        # Создаем минимальные заглушки для document_generator
        self.repository = _FakeRepository(client)
        self.chunker = _FakeChunker()
        self.vector_store = _FakeVectorStore()


class _FakeRepository:
    """Фейковый репозиторий для совместимости."""
    
    def __init__(self, client: RagClient):
        self.client = client
    
    def get_materials(self, discipline_id: str | None = None):
        """Возвращает материалы как Discipline материалы."""
        from db.models import Material
        docs = self.client.list_documents_sync(discipline_id=discipline_id)
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
    
    def list_generated_document_rows(self, path_marker: str, discipline_id: str | None = None):
        """Возвращает строки документов."""
        docs = self.client.list_documents_sync(discipline_id=discipline_id)
        return [
            {"id": doc.id, "source_path": doc.source_path, "title": doc.title}
            for doc in docs
            if path_marker in doc.source_path
        ]
    
    def delete_document_record(self, document_id: str, commit: bool = True):
        """Удаляет запись документа."""
        self.client.delete_document_sync(document_id=document_id)
    
    def save_document_with_chunks(self, source_path, chunks, discipline_id, title, vector_store):
        """Сохраняет документ с чанками - через HTTP клиент."""
        # Импортируем документ
        try:
            import os
            from pathlib import Path
            
            # Определяем mime_type по расширению
            ext = Path(source_path).suffix.lower()
            mime_types = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".txt": "text/plain",
                ".md": "text/markdown",
                ".html": "text/html",
            }
            mime_type = mime_types.get(ext, "text/plain")
            
            # Chunker для текстовых файлов
            text_chunks = []
            for chunk in chunks:
                text_chunks.append({
                    "page": chunk.get("page"),
                    "content": chunk.get("content", ""),
                })
            
            # Для совместимости просто сохраняем как текстовый файл
            # и импортируем через HTTP
            result = self.client.import_document_sync(
                path=source_path,
                discipline_id=discipline_id,
                title=title,
            )
            return result
        except Exception as e:
            logger.warning(f"Failed to save document via HTTP: {e}")
            raise
    
    def save_generated_document_fallback(self, path, discipline_id, title, text):
        """Резервное сохранение документа."""
        from pathlib import Path
        import json
        
        # Сохраняем текст в файл
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(text, encoding="utf-8")
        
        # Пробуем импортировать через HTTP
        self.client.import_document_sync(
            path=path,
            discipline_id=discipline_id,
            title=title,
        )


class _FakeChunker:
    """Фейковый чанкер для совместимости."""
    
    def chunk_pages(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Возвращает чанки из страниц."""
        chunks = []
        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page")
            # Разбиваем текст на чанки по ~512 токенам (примерно)
            # Для простоты возвращаем одну страницу как один чанк
            if text:
                chunks.append({"page": page_num, "content": text})
        return chunks


class _FakeVectorStore:
    """Фейковый векторный store для совместимости."""
    pass
