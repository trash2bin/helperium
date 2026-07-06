"""HTTP-сервис RAG.

Запускается как `python -m rag.service` (или через entrypoint `rag-service`).
Предоставляет типизированный HTTP-фасад над `RAGPipeline` с использованием FastAPI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Callable, Awaitable
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from rag.db import RagDB
from rag import create_rag_pipeline
from rag.http_models import (
    ContextRequest,
    ContextResponse,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    HealthResponse,
    ImportDocumentRequest,
    ImportDocumentResponse,
    ListDocumentsRequest,
    ListDocumentsResponse,
    SearchRequest,
    SearchResponse,
)

logger = logging.getLogger("rag.service")

# === Конфигурация сервиса (env) ===

RAG_HOST: str = os.environ.get("RAG_HOST", "127.0.0.1")
RAG_PORT: int = int(os.environ.get("RAG_PORT", "8082"))


# === Состояние сервиса (singleton) ===


class ServiceState:
    """Lazy-инициализируемое состояние процесса RAG-сервиса."""

    def __init__(self) -> None:
        self._db: RagDB | None = None
        self._pipeline = None

    def get_db(self):
        if self._db is None:
            self._db = RagDB()
        return self._db

    def get_pipeline(self):
        if self._pipeline is None:
            self._pipeline = create_rag_pipeline(self.get_db().conn)
        return self._pipeline

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
            self._pipeline = None


state = ServiceState()


# === Приложение ===

app = FastAPI(
    title="RAG Service",
    description="HTTP API for RAG pipeline (indexing and semantic search)",
    version="0.1.0",
    swagger_ui_parameters={"tryItOutEnabled": True},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Correlation ID middleware ---
@app.middleware("http")
async def add_correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    request.state.correlation_id = correlation_id
    logger.info(
        "Request started",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# === Эндпоинты ===


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка состояния сервиса",
    description="Проверяет доступность SQLite, ChromaDB и загрузку embedding-модели.",
)
async def health() -> JSONResponse:
    db_status: dict[str, Any] = {"status": "ok", "error": None}
    chroma_status: dict[str, Any] = {"status": "ok", "error": None}
    embedding_status: dict[str, Any] = {"status": "ok", "error": None, "model": None}

    # SQLite
    try:
        state.get_db().ping()
    except Exception as exc:
        db_status = {"status": "error", "error": str(exc)}

    # ChromaDB
    try:
        pipeline = state.get_pipeline()
        pipeline.list_documents(limit=1)
        embedding_status["model"] = pipeline.config.embedding_model
    except Exception as exc:
        chroma_status = {"status": "error", "error": str(exc)}
        embedding_status = {"status": "error", "error": str(exc), "model": None}

    overall_ok = db_status["status"] == "ok" and chroma_status["status"] == "ok"

    if not overall_ok:
        return JSONResponse(
            status_code=503,
            content=HealthResponse(
                status="degraded",
                database=db_status,
                chroma=chroma_status,
                embedding=embedding_status,
            ).model_dump(),
        )

    return JSONResponse(
        content=HealthResponse(
            status="ok",
            database=db_status,
            chroma=chroma_status,
            embedding=embedding_status,
        ).model_dump(),
    )


@app.post(
    "/documents/list",
    response_model=ListDocumentsResponse,
    summary="Список документов",
    description="Возвращает список документов в индексе с фильтрацией по дисциплине.",
)
async def list_documents(req: ListDocumentsRequest) -> ListDocumentsResponse:
    try:
        # Sync SQLite + ChromaDB → в thread pool, чтобы не блокировать event loop
        docs = await run_in_threadpool(
            state.get_pipeline().list_documents,
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return ListDocumentsResponse(
            documents=list(docs),
            count=len(docs),
        )
    except Exception as exc:
        logger.exception("list_documents failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list documents: {exc}",
        )


@app.post(
    "/documents/import",
    response_model=ImportDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Импорт документа",
    description="Загружает файл в RAG-индекс, разбивает на чанки и индексирует векторы.",
)
async def import_document(req: ImportDocumentRequest) -> ImportDocumentResponse:
    try:
        # Долгая операция: парсинг + embedding + ChromaDB + SQLite — в thread pool
        result = await run_in_threadpool(
            state.get_pipeline().import_document,
            path=req.path,
            discipline_id=req.discipline_id,
            discipline_name=req.discipline_name,
            title=req.title,
        )
        return ImportDocumentResponse(
            document=result.document,
            chunks_count=result.chunks_count,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except Exception as exc:
        logger.exception("import_document failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import document: {exc}",
        )




@app.post(
    "/documents/upload",
    response_model=ImportDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузка файла и импорт",
    description="Принимает multipart-файл, сохраняет во временную директорию и импортирует в RAG-индекс.",
)
async def upload_document(
    file: UploadFile = File(..., description="Файл документа (PDF, DOCX, TXT, MD, HTML)"),
    title: str | None = Form(None, description="Человекочитаемое название"),
    discipline_id: str | None = Form(None, description="ID дисциплины для привязки"),
    discipline_name: str | None = Form(None, description="Название дисциплины"),
) -> ImportDocumentResponse:
    """
    Принимает файл через multipart/form-data, сохраняет во временную директорию
    и передаёт в пайплайн импорта (парсинг → чанкинг → эмбеддинг → индексация).
    """
    upload_dir = tempfile.mkdtemp(prefix="rag-upload-")
    save_path = os.path.join(upload_dir, file.filename or "uploaded_document")

    try:
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        result = await run_in_threadpool(
            state.get_pipeline().import_document,
            path=save_path,
            discipline_id=discipline_id,
            discipline_name=discipline_name,
            title=title,
        )
        return ImportDocumentResponse(
            document=result.document,
            chunks_count=result.chunks_count,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except Exception as exc:
        logger.exception("upload_document failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload document: {exc}",
        )


@app.post(
    "/documents/delete",
    response_model=DeleteDocumentResponse,
    summary="Удаление документа",
    description="Удаляет документ и его векторы из индекса по пути или ID. Идемпотентно.",
)
async def delete_document(req: DeleteDocumentRequest) -> DeleteDocumentResponse:
    if not req.path and not req.document_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide `path` or `document_id`",
        )

    def _do_delete() -> DeleteDocumentResponse:
        pipeline = state.get_pipeline()
        repo = pipeline.repository
        row = repo.find_document_for_delete(
            source_path=req.path,
            document_id=req.document_id,
        )

        if not row:
            logger.info(
                "Delete requested for non-existent document (path=%s, document_id=%s)",
                req.path,
                req.document_id,
            )
            return DeleteDocumentResponse(
                deleted=None,
                title=None,
                message="Document not found, already deleted or never existed",
            )

        doc_id = row["id"]
        try:
            pipeline.delete_document_vectors(doc_id)
        except Exception as exc:
            logger.warning("Vector deletion failed for %s: %s", doc_id, exc)

        repo.delete_document_record(doc_id, commit=True)
        return DeleteDocumentResponse(deleted=doc_id, title=row["title"])

    try:
        return await run_in_threadpool(_do_delete)
    except Exception as exc:
        logger.exception("delete_document failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {exc}",
        )


@app.post(
    "/search",
    response_model=SearchResponse,
    summary="Семантический поиск",
    description="Ищет наиболее релевантные фрагменты документов по текстовому запросу.",
)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        results = await run_in_threadpool(
            state.get_pipeline().search_documents,
            query=req.query,
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return SearchResponse(
            results=list(results),
            count=len(results),
        )
    except Exception as exc:
        logger.exception("search failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {exc}",
        )


@app.post(
    "/context",
    response_model=ContextResponse,
    summary="Сборка контекста",
    description="Формирует итоговую строку контекста для передачи в LLM.",
)
async def context(req: ContextRequest) -> ContextResponse:
    try:
        rag_context = await run_in_threadpool(
            state.get_pipeline().build_rag_context,
            query=req.query,
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return ContextResponse(
            context=rag_context.answer_instruction,
            sources=list(rag_context.chunks),
        )
    except Exception as exc:
        logger.exception("context failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Context build failed: {exc}",
        )


def main() -> None:
    """Запустить RAG HTTP-сервис."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info("Starting RAG service on %s:%s", RAG_HOST, RAG_PORT)
    uvicorn.run(
        "rag.service:app",
        host=RAG_HOST,
        port=RAG_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
