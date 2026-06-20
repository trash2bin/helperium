"""HTTP-сервис RAG.

Запускается как `python -m rag.service` (или через entrypoint `rag-service`).
Предоставляет типизированный HTTP-фасад над `RAGPipeline` с использованием FastAPI.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from db.database import Database
from rag import create_rag_pipeline
from rag.http_models import (
    ContextRequest,
    ContextResponse,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    ErrorResponse,
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
        self._db: Database | None = None
        self._pipeline = None

    def get_db(self) -> Database:
        if self._db is None:
            self._db = Database()
        return self._db

    def get_pipeline(self):
        if self._pipeline is None:
            self._pipeline = create_rag_pipeline(self.get_db().connector)
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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Эндпоинты ===

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка состояния сервиса",
    description="Проверяет доступность SQLite, ChromaDB и загрузку embedding-модели.",
)
async def health() -> HealthResponse:
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

    overall_ok = (
        db_status["status"] == "ok"
        and chroma_status["status"] == "ok"
    )
    
    if not overall_ok:
        # We return 503 if degraded, but still provide the HealthResponse body
        # FastAPI's response_model doesn't automatically change status code.
        # We can use raise HTTPException or just return the model and let the caller see status.
        # However, for /health, returning a 503 is standard for load balancers.
        # To do this while keeping the body, we can use a custom response or just let it be 200
        # and let the 'status' field indicate degradation. 
        # Let's stick to 200 but with "degraded" status to avoid breaking simple clients,
        # OR use a custom response. Let's use 200 and rely on the 'status' field.
        pass

    return HealthResponse(
        status="ok" if overall_ok else "degraded",
        database=db_status,
        chroma=chroma_status,
        embedding=embedding_status,
    )


@app.post(
    "/documents/list",
    response_model=ListDocumentsResponse,
    summary="Список документов",
    description="Возвращает список документов в индексе с фильтрацией по дисциплине.",
)
async def list_documents(req: ListDocumentsRequest) -> ListDocumentsResponse:
    try:
        docs = state.get_pipeline().list_documents(
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return ListDocumentsResponse(
            documents=[doc.model_dump(mode="json") for doc in docs],
            count=len(docs),
        )
    except Exception as exc:
        logger.exception("list_documents failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to list documents: {exc}"
        )


@app.get(
    "/documents/list",
    response_model=ListDocumentsResponse,
    summary="Список документов (GET)",
    description="Аналог POST /documents/list, но через query-параметры.",
)
async def list_documents_get(
    discipline_id: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=1000),
) -> ListDocumentsResponse:
    try:
        docs = state.get_pipeline().list_documents(
            discipline_id=discipline_id,
            limit=limit,
        )
        return ListDocumentsResponse(
            documents=[doc.model_dump(mode="json") for doc in docs],
            count=len(docs),
        )
    except Exception as exc:
        logger.exception("list_documents_get failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to list documents: {exc}"
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
        result = state.get_pipeline().import_document(
            path=req.path,
            discipline_id=req.discipline_id,
            title=req.title,
        )
        return ImportDocumentResponse(
            document=result.document.model_dump(mode="json"),
            chunks_count=result.chunks_count,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("import_document failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to import document: {exc}"
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
            detail="Provide `path` or `document_id`"
        )

    try:
        pipeline = state.get_pipeline()
        repo = pipeline.repository
        row = repo.find_document_for_delete(
            source_path=req.path,
            document_id=req.document_id,
        )
        
        if not row:
            logger.info("Delete requested for non-existent document (path=%s, document_id=%s)", req.path, req.document_id)
            return DeleteDocumentResponse(
                deleted=None, 
                title=None, 
                message="Document not found, already deleted or never existed"
            )

        doc_id = row["id"]
        try:
            pipeline.delete_document_vectors(doc_id)
        except Exception as exc:
            logger.warning("Vector deletion failed for %s: %s", doc_id, exc)
        
        repo.delete_document_record(doc_id, commit=True)
        return DeleteDocumentResponse(deleted=doc_id, title=row["title"])
    except Exception as exc:
        logger.exception("delete_document failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to delete document: {exc}"
        )


@app.post(
    "/search",
    response_model=SearchResponse,
    summary="Семантический поиск",
    description="Ищет наиболее релевантные фрагменты документов по текстовому запросу.",
)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        results = state.get_pipeline().search_documents(
            query=req.query,
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return SearchResponse(
            results=[r.model_dump(mode="json") for r in results],
            count=len(results),
        )
    except Exception as exc:
        logger.exception("search failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Search failed: {exc}"
        )


@app.post(
    "/context",
    response_model=ContextResponse,
    summary="Сборка контекста",
    description="Формирует итоговую строку контекста для передачи в LLM.",
)
async def context(req: ContextRequest) -> ContextResponse:
    try:
        rag_context = state.get_pipeline().build_rag_context(
            query=req.query,
            discipline_id=req.discipline_id,
            limit=req.limit,
        )
        return ContextResponse(**rag_context.model_dump(mode="json"))
    except Exception as exc:
        logger.exception("context failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Context build failed: {exc}"
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
