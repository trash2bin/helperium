from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import uuid
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TypedDict

import chromadb
from chromadb.api.types import Embedding, Embeddings, Metadata

from sentence_transformers import SentenceTransformer

# Docling
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc.document import TextItem  # точный тип текстового элемента

# Chunking
from chonkie import SemanticChunker

from typing import cast, List

from db.database import Database
from db.models import Document, DocumentImportResult, RagContext, RagSearchResult


TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
PROJECT_ROOT = Path(__file__).parent.parent

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80


ProgressCallback = Callable[..., None]


class PageDict(TypedDict):
    page: int | None
    text: str


class ChunkDict(TypedDict):
    page: int | None
    content: str


logger = logging.getLogger(__name__)


class RagTools:
    """Инструменты RAG: парсинг документов, чанкинг, эмбеддинги, поиск."""

    def __init__(
        self,
        db: Database,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        chroma_path: str | None = None,
        embedding_model_name: str | None = None,
    ) -> None:
        self.db = db
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self.embedding_model_name = (
            embedding_model_name
            or os.environ.get("RAG_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        self.local_files_only = os.environ.get("RAG_LOCAL_FILES_ONLY", "0") == "1"
        self.device = os.environ.get("RAG_DEVICE", "cpu")

        self.chroma_path = chroma_path or os.environ.get(
            "CHROMA_PATH", str(PROJECT_ROOT / "chroma_db")
        )

        # Ленивые поля
        self._embedding_model: SentenceTransformer | None = None
        self._doc_converter: DocumentConverter | None = None
        self._chunker: SemanticChunker | None = None

        # ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name="university_documents",
            metadata={"hnsw:space": "cosine"},
        )


    def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        title: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> DocumentImportResult:
        """Загрузить документ в SQLite + ChromaDB."""
        source_path = self._validate_path(path)

        # Парсинг
        pages = self._extract_pages(source_path)
        if on_progress:
            on_progress("chunk")

        # Чанкинг
        chunks = self._chunk_pages(pages)
        if on_progress:
            on_progress("embed")

        if not chunks:
            raise ValueError(f"Document has no readable text: {source_path}")

        # Сохранение в БД (SQLite + ChromaDB)
        document = self._save_document(
            source_path=source_path,
            chunks=chunks,
            discipline_id=discipline_id,
            title=title,
        )

        if on_progress:
            on_progress("done", n=len(chunks))

        return DocumentImportResult(document=document, chunks_count=len(chunks))

    def list_documents(self, discipline_id: str | None = None) -> list[Document]:
        """Список загруженных документов (опционально фильтруется по discipline_id)."""
        cursor = self.db.conn.cursor()
        if discipline_id:
            rows = cursor.execute(
                """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents
                WHERE discipline_id = ?
                ORDER BY created_at DESC
                """,
                (discipline_id,),
            ).fetchall()
        else:
            rows = cursor.execute(
                """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents
                ORDER BY created_at DESC
                """
            ).fetchall()

        return [self._document_from_row(row) for row in rows]

    def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск по чанкам."""
        normalized_query = query.strip()
        if not normalized_query:
            return []

        limit = max(1, min(limit, 20))

        query_result = self.collection.query(
            query_embeddings=self._embed_batch([normalized_query]),
            n_results=limit,
            where={"discipline_id": discipline_id} if discipline_id else None,
            include=["documents", "metadatas", "distances"],
        )

        flat_ids: list[str] = (query_result.get("ids") or [[]])[0]
        flat_docs: list[str] = (query_result.get("documents") or [[]])[0]
        flat_metas: list[Metadata] = (query_result.get("metadatas") or [[]])[0]
        flat_dists: list[float] = (query_result.get("distances") or [[]])[0]

        results: list[RagSearchResult] = []
        for chunk_id, content, metadata, distance in zip(
            flat_ids, flat_docs, flat_metas, flat_dists
        ):
            page = self._meta_int(metadata["page"])
            discipline = self._meta_str(metadata.get("discipline_id"))
            results.append(
                RagSearchResult(
                    document_id=self._meta_str(metadata["document_id"]),
                    document_title=self._meta_str(metadata["document_title"]),
                    source_path=self._meta_str(metadata["source_path"]),
                    discipline_id=discipline or None,
                    chunk_id=str(chunk_id),
                    chunk_index=self._meta_int(metadata["chunk_index"]),
                    page=page if page >= 0 else None,
                    score=round(max(0.0, 1.0 - float(distance)), 6),
                    content=str(content),
                )
            )

        return results

    def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        """Собрать RagContext для LLM: запрос + найденные чанки + инструкция."""
        chunks = self.search_documents(
            query=query, discipline_id=discipline_id, limit=limit
        )
        return RagContext(
            query=query,
            answer_instruction=(
                "Ответь на вопрос только по найденным фрагментам документов. "
                "Если в контексте нет ответа, прямо скажи, что данных в документах недостаточно. "
                "Ссылайся на название документа и страницу, когда page заполнен."
            ),
            chunks=chunks,
        )

    def _delete_document_vectors(self, document_id: str) -> None:
        """Удалить векторы одного документа из ChromaDB."""
        self.collection.delete(where={"document_id": document_id})


    @staticmethod
    def _validate_path(path: str) -> Path:
        source_path = Path(path).expanduser()
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        source_path = source_path.resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"Document not found: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Document path is not a file: {source_path}")
        return source_path

    def _save_document(
        self,
        source_path: Path,
        chunks: list[ChunkDict],
        discipline_id: str | None,
        title: str | None,
    ) -> Document:
        """Единая транзакция: SQLite + ChromaDB. При сбое ChromaDB — откат SQLite."""
        document_id = str(uuid.uuid4())
        mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        document_title = title or source_path.stem
        created_at = datetime.now(timezone.utc).isoformat()

        cursor = self.db.conn.cursor()

        # Удаляем старую версию документа, если она есть
        existing = cursor.execute(
            "SELECT id FROM documents WHERE source_path = ?",
            (str(source_path),),
        ).fetchone()
        if existing:
            self._remove_existing_document(cursor, existing["id"])

        # Вставляем запись о документе
        cursor.execute(
            """
            INSERT INTO documents (
                id, title, source_path, mime_type, discipline_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                document_title,
                str(source_path),
                mime_type,
                discipline_id,
                created_at,
                json.dumps(
                    {
                        "vector_store": "chromadb",
                        "embedding_model": self.embedding_model_name,
                        "chroma_collection": self.collection.name,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        # Сохраняем чанки в SQLite и готовим данные для ChromaDB
        chunk_ids, chunk_texts, chunk_metadatas = self._save_chunks_to_db(
            cursor=cursor,
            document_id=document_id,
            document_title=document_title,
            source_path=source_path,
            discipline_id=discipline_id,
            chunks=chunks,
        )

        try:
            # Сохраняем в ChromaDB
            self._save_chunks_to_chroma(chunk_ids, chunk_texts, chunk_metadatas)
            self.db.conn.commit()
        except Exception:
            # Откатываем SQLite, если ChromaDB упал
            self.db.conn.rollback()
            raise

        return Document(
            id=document_id,
            title=document_title,
            source_path=str(source_path),
            mime_type=mime_type,
            discipline_id=discipline_id,
            created_at=created_at,
        )

    def _remove_existing_document(self, cursor, existing_id: str) -> None:
        """Удалить старую версию документа из SQLite и ChromaDB."""
        try:
            self._delete_document_vectors(existing_id)
        except Exception as exc:
            logger.warning("Failed to delete vectors for %s: %s", existing_id, exc)
        cursor.execute("DELETE FROM document_chunks WHERE document_id = ?", (existing_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (existing_id,))

    def _save_chunks_to_db(
        self,
        cursor,
        document_id: str,
        document_title: str,
        source_path: Path,
        discipline_id: str | None,
        chunks: list[ChunkDict],
    ) -> tuple[list[str], list[str], list[Metadata]]:
        """Сохранить чанки в SQLite, вернуть данные для ChromaDB."""
        chunk_ids: list[str] = []
        chunk_texts: list[str] = []
        chunk_metadatas: list[dict[str, str | int | float | bool]] = []

        for index, chunk in enumerate(chunks):
            content = chunk["content"]
            chunk_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, chunk_index, page, content, embedding_json, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    index,
                    chunk["page"],
                    content,
                    "[]",  # векторы хранятся в ChromaDB
                    len(self._tokenize(content)),
                ),
            )
            chunk_ids.append(chunk_id)
            chunk_texts.append(content)
            chunk_metadatas.append(
                {
                    "document_id": document_id,
                    "document_title": document_title,
                    "source_path": str(source_path),
                    "discipline_id": discipline_id or "",
                    "chunk_index": index,
                    "page": int(chunk["page"]) if chunk["page"] is not None else -1,
                }
            )

        return chunk_ids, chunk_texts, cast(list[Metadata], chunk_metadatas)

    def _save_chunks_to_chroma(
        self,
        chunk_ids: list[str],
        chunk_texts: list[str],
        chunk_metadatas: list[Metadata],
    ) -> None:
        """Загрузить чанки в ChromaDB вместе с эмбеддингами."""
        if not chunk_texts:
            return
        self.collection.add(
            ids=chunk_ids,
            documents=chunk_texts,
            embeddings=self._embed_batch(chunk_texts),
            metadatas=chunk_metadatas,
        )


    def _extract_pages(self, source_path: Path) -> list[PageDict]:
        """Извлечь текст постранично из файла."""
        suffix = source_path.suffix.lower()

        # Простые текстовые форматы — читаем напрямую
        if suffix in {".txt", ".md", ".markdown", ".csv", ".json", ".py"}:
            return [{"page": None, "text": source_path.read_text(encoding="utf-8")}]

        # PDF и другие сложные форматы — через Docling
        result = self.doc_converter.convert(str(source_path))
        dl_doc = result.document

        # Собираем текст по страницам: page_no → list[str]
        page_lines: dict[int, list[str]] = {}
        for item in dl_doc.iterate_items():
            # Берём только реальные текстовые блоки
            if not isinstance(item, TextItem):
                continue
            text = item.text
            if not text.strip():
                continue

            # Определяем номер страницы через prov (provenance)
            page_no = 0
            if item.prov:
                page_no = item.prov[0].page_no
            page_lines.setdefault(page_no, []).append(text)

        # Если TextItem'ов не нашли — fallback на markdown-экспорт
        if not page_lines:
            md_text = dl_doc.export_to_markdown()
            if md_text.strip():
                return [{"page": None, "text": md_text}]
            return []

        # Собираем результат, сортируя по номеру страницы
        result_pages: list[PageDict] = []
        for page_no in sorted(page_lines):
            result_pages.append({
                "page": page_no if page_no > 0 else None,
                "text": "\n".join(page_lines[page_no]),
            })
        return result_pages


    def _chunk_pages(self, pages: Iterable[PageDict]) -> list[ChunkDict]:
        """Склеить страницы, разбить на семантические чанки, привязать к страницам."""
        # Собираем полный текст и параллельно — интервальный индекс страниц:
        #   boundaries = [end_0, end_1, ..., end_N]
        #   page_for_boundary[i] = page для символов [boundaries[i-1], boundaries[i])
        parts: list[str] = []
        boundaries: list[int] = []       # конечные индексы страниц в full_text
        page_for_boundary: list[int | None] = []

        cursor_pos = 0
        for page in pages:
            text = self._normalize_text(str(page.get("text") or ""))
            if not text:
                continue
            parts.append(text)
            cursor_pos += len(text) + 1   # +1 под разделитель
            boundaries.append(cursor_pos)
            page_for_boundary.append(page.get("page"))

        if not parts:
            return []

        full_text = " ".join(parts).strip()
        if not full_text:
            return []

        chonkie_chunks = self.chunker.chunk(full_text)

        chunks: list[ChunkDict] = []
        for ch in chonkie_chunks:
            content = ch.text.strip()
            if not content:
                continue
            start = getattr(ch, "start_index", 0)
            page = self._find_page_for_index(start, boundaries, page_for_boundary)
            chunks.append({"page": page, "content": content})

        return chunks

    @staticmethod
    def _find_page_for_index(
        index: int,
        boundaries: list[int],
        page_for_boundary: list[int | None],
    ) -> int | None:
        """Бинарный поиск страницы по индексу символа в склеенном тексте."""
        if not boundaries:
            return None
        pos = bisect_right(boundaries, index)
        if pos >= len(page_for_boundary):
            return page_for_boundary[-1]
        return page_for_boundary[pos]


    def _embed_batch(self, texts: list[str]) -> Embeddings:
        """Векторизовать список строк. Пустой список возвращает пустой список."""
        if not texts:
            return []
        return cast(Embeddings, self.embedding_model.encode(texts, normalize_embeddings=True))


    @property
    def embedding_model(self) -> SentenceTransformer:
        if self._embedding_model is None:
            try:
                self._embedding_model = SentenceTransformer(
                    self.embedding_model_name,
                    local_files_only=self.local_files_only,
                    device=self.device
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load RAG embedding model '{self.embedding_model_name}'. "
                    "Check internet access for the first download, or set "
                    "RAG_EMBEDDING_MODEL to a local model path. "
                    "If the model is already cached, set RAG_LOCAL_FILES_ONLY=1."
                ) from exc
        return self._embedding_model

    @property
    def doc_converter(self) -> DocumentConverter:
        if self._doc_converter is None:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.accelerator_options.device = self.device
            self._doc_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
        return self._doc_converter

    @property
    def chunker(self) -> SemanticChunker:
        if self._chunker is None:
            # ВАЖНО: передаём ИМЯ модели (строку), а не объект SentenceTransformer.
            # Иначе chonkie не сможет корректно инициализировать свой wrapper.
            self._chunker = SemanticChunker(
                embedding_model=self.embedding_model_name,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap, # type: ignore[arg-type]
            )
        return self._chunker


    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(text)]

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [line.strip() for line in text.replace("\x00", " ").splitlines()]
        return re.sub(r"\s+", " ", " ".join(line for line in lines if line)).strip()

    @staticmethod
    def _document_from_row(row) -> Document:
        return Document(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            mime_type=row["mime_type"],
            discipline_id=row["discipline_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _meta_int(val: object) -> int:
        if isinstance(val, (int, float, str)):
            return int(val)
        raise TypeError(f"Expected int-convertible metadata value, got {type(val)}")

    @staticmethod
    def _meta_str(val: object) -> str:
        return str(val) if val is not None else ""
