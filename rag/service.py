"""HTTP-сервис RAG.

Запускается как `python -m rag.service` (или через entrypoint `rag-service`).
Предоставляет тонкий HTTP-фасад над `RAGPipeline`:

    GET  /health
    POST /documents/list
    POST /documents/import
    POST /documents/delete
    POST /search
    POST /context

Использует Starlette (FastAPI нет в зависимостях, чтобы не раздувать их
на этом этапе). Когда придёт Этап 2 (контейнеризация) — FastAPI можно
добавить одной строкой в `pyproject.toml` и мигрировать роуты.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from db.database import Database
from rag import create_rag_pipeline

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


# === Утилиты ответов ===

def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _err(message: str, status: int = 400, detail: str | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    if detail:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=status)


async def _parse_json(request: Request) -> dict[str, Any]:
    """Прочитать JSON-тело запроса (или вернуть пустой dict для GET)."""
    if request.method == "GET":
        return dict(request.query_params)
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"Missing required field: {key}")
    return payload[key]


# === Эндпоинты ===

async def health(_: Request) -> JSONResponse:
    """Состояние RAG-сервиса: SQLite + ChromaDB + embedding-модель."""
    db_status: dict[str, Any] = {"status": "ok", "error": None}
    chroma_status: dict[str, Any] = {"status": "ok", "error": None}
    embedding_status: dict[str, Any] = {"status": "ok", "error": None, "model": None}

    # SQLite
    try:
        state.get_db().ping()
    except Exception as exc:  # pragma: no cover — defensive
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
    return _ok(
        {
            "status": "ok" if overall_ok else "degraded",
            "database": db_status,
            "chroma": chroma_status,
            "embedding": embedding_status,
        },
        status=200 if overall_ok else 503,
    )


async def list_documents(request: Request) -> JSONResponse:
    """Список документов в RAG-индексе."""
    try:
        payload = await _parse_json(request)
    except ValueError as exc:
        return _err(str(exc), status=400)

    discipline_id = payload.get("discipline_id")
    limit = payload.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _err("`limit` must be an integer", status=400)

    try:
        docs = state.get_pipeline().list_documents(
            discipline_id=discipline_id,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("list_documents failed")
        return _err("Failed to list documents", status=500, detail=str(exc))

    return _ok(
        {
            "documents": [doc.model_dump(mode="json") for doc in docs],
            "count": len(docs),
        }
    )


async def import_document(request: Request) -> JSONResponse:
    """Импортировать документ в RAG-индекс."""
    try:
        payload = await _parse_json(request)
    except ValueError as exc:
        return _err(str(exc), status=400)

    try:
        path = _require(payload, "path")
    except ValueError as exc:
        return _err(str(exc), status=400)

    try:
        result = state.get_pipeline().import_document(
            path=path,
            discipline_id=payload.get("discipline_id"),
            title=payload.get("title"),
        )
    except FileNotFoundError as exc:
        return _err("Document not found", status=404, detail=str(exc))
    except ValueError as exc:
        return _err("Invalid document", status=422, detail=str(exc))
    except Exception as exc:
        logger.exception("import_document failed")
        return _err("Failed to import document", status=500, detail=str(exc))

    return _ok(
        {
            "document": result.document.model_dump(mode="json"),
            "chunks_count": result.chunks_count,
        },
        status=201,
    )


async def delete_document(request: Request) -> JSONResponse:
    """Удалить документ из RAG-индекса (по пути или ID)."""
    try:
        payload = await _parse_json(request)
    except ValueError as exc:
        return _err(str(exc), status=400)

    path = payload.get("path")
    document_id = payload.get("document_id")
    if not path and not document_id:
        return _err("Provide `path` or `document_id`", status=400)

    try:
        pipeline = state.get_pipeline()
        repo = pipeline.repository
        row = repo.find_document_for_delete(
            source_path=path,
            document_id=document_id,
        )
        if not row:
            return _err("Document not found", status=404)

        doc_id = row["id"]
        try:
            pipeline.delete_document_vectors(doc_id)
        except Exception as exc:
            logger.warning("Vector deletion failed for %s: %s", doc_id, exc)
        repo.delete_document_record(doc_id, commit=True)
    except Exception as exc:
        logger.exception("delete_document failed")
        return _err("Failed to delete document", status=500, detail=str(exc))

    return _ok({"deleted": doc_id, "title": row["title"]})


async def search(request: Request) -> JSONResponse:
    """Семантический поиск по фрагментам документов."""
    try:
        payload = await _parse_json(request)
    except ValueError as exc:
        return _err(str(exc), status=400)

    try:
        query = _require(payload, "query")
    except ValueError as exc:
        return _err(str(exc), status=400)

    limit = payload.get("limit", 5)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _err("`limit` must be an integer", status=400)

    try:
        results = state.get_pipeline().search_documents(
            query=str(query),
            discipline_id=payload.get("discipline_id"),
            limit=limit,
        )
    except Exception as exc:
        logger.exception("search failed")
        return _err("Search failed", status=500, detail=str(exc))

    return _ok(
        {
            "results": [r.model_dump(mode="json") for r in results],
            "count": len(results),
        }
    )


async def context(request: Request) -> JSONResponse:
    """Готовый RAG-контекст для LLM-ответа."""
    try:
        payload = await _parse_json(request)
    except ValueError as exc:
        return _err(str(exc), status=400)

    try:
        query = _require(payload, "query")
    except ValueError as exc:
        return _err(str(exc), status=400)

    limit = payload.get("limit", 5)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _err("`limit` must be an integer", status=400)

    try:
        rag_context = state.get_pipeline().build_rag_context(
            query=str(query),
            discipline_id=payload.get("discipline_id"),
            limit=limit,
        )
    except Exception as exc:
        logger.exception("context failed")
        return _err("Context build failed", status=500, detail=str(exc))

    return _ok(rag_context.model_dump(mode="json"))


# === Приложение ===

app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/documents/list", list_documents, methods=["GET", "POST"]),
        Route("/documents/import", import_document, methods=["POST"]),
        Route("/documents/delete", delete_document, methods=["POST"]),
        Route("/search", search, methods=["POST"]),
        Route("/context", context, methods=["POST"]),
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
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
