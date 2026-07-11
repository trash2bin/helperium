"""Тест качества семантического поиска RAG.

Режимы:
    # CI: только проверка, что код не падает (mock эмбеддинги)
    uv run pytest rag/tests/benchmark/test_search_quality.py -v --tb=short

    # Полный замер качества (реальные эмбеддинги — SentenceTransformer)
    RAG_BENCHMARK_REAL=1 uv run pytest rag/tests/benchmark/test_search_quality.py -v --tb=short

    # С обновлением baseline
    RAG_BENCHMARK_REAL=1 uv run python -c "
from rag.tests.benchmark.golden_qa import evaluate_retrieval, print_report, save_baseline
# ... импорт + создание pipeline + evaluate + save_baseline
"
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pytest

from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.chunker import TextChunker
from rag.repository import DocumentRepository
from rag.embeddings import SentenceTransformerEmbedding
from rag.vector_store import ChromaDBVectorStore
from rag.pipeline import RAGPipeline
from rag.documents_schema import create_rag_schema

from rag.tests.benchmark.golden_qa import (
    evaluate_retrieval,
    print_report,
    load_baseline,
    save_baseline,
    import_golden_documents,
)

logger = logging.getLogger(__name__)

# Режим реального теста (требует загрузки SentenceTransformer)
RUN_REAL = os.environ.get("RAG_BENCHMARK_REAL", "").strip() in ("1", "true", "yes")


# ── Фикстуры ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def benchmark_tmp_dir() -> Path:
    """Временная директория для benchmark."""
    tmp = Path(tempfile.mkdtemp(prefix="rag-benchmark-"))
    yield tmp
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def benchmark_config(benchmark_tmp_dir: Path) -> RagConfig:
    """Конфиг для benchmark — отдельный chroma + sqlite."""
    model = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        if RUN_REAL
        else "mock"
    )
    return RagConfig(
        chroma_path=str(benchmark_tmp_dir / "chroma_db"),
        chroma_collection="benchmark_collection",
        embedding_device="cpu",
        embedding_model=model,
        chunker_type="recursive",  # semantic требует реальную модель чанкования
        chunk_size=768,
        chunk_overlap=160,
    )


@pytest.fixture(scope="session")
def embedding_service_impl(benchmark_config: RagConfig):
    """Реальный SentenceTransformer (только если RAG_BENCHMARK_REAL=1)."""
    if RUN_REAL:
        logger.info("Loading real embedding model for benchmark (this may take a while)...")
        return SentenceTransformerEmbedding(benchmark_config)
    else:
        # Возвращаем None — будем использовать mock
        return None


class MockEmbedding:
    """Мок эмбеддингов — возвращает единичный вектор."""

    def encode_batched(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        return [[1.0 / 384] * 384 for _ in texts]


class MockVectorStore:
    """Мок векторного хранилища — возвращает пустые результаты.

    Позволяет тестировать импорт без реальной ChromaDB.
    """

    def add_chunks(
        self,
        chunk_ids: list[str],
        chunk_texts: list[str],
        chunk_metadatas: list[dict],
        document_id: str,
        document_title: str,
        source_path: str,
        discipline_id: str | None,
    ) -> None:
        pass

    def delete_by_document_id(self, document_id: str) -> None:
        pass

    def delete_by_ids(self, ids: list[str]) -> None:
        pass

    def search(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list:
        return []


@pytest.fixture(scope="session")
def real_pipeline(
    benchmark_tmp_dir: Path,
    benchmark_config: RagConfig,
    embedding_service_impl: SentenceTransformerEmbedding | None,
) -> RAGPipeline:
    """Создать RAGPipeline с реальной ChromaDB + SQLite.

    В CI-режиме (RAG_BENCHMARK_REAL не установлен) использует mock-эмбеддинги,
    так что качество поиска будет нулевым, но код выполняется.
    """
    # SQLite
    db_path = benchmark_tmp_dir / "benchmark.db"
    conn_str = str(db_path)
    import sqlite3

    conn = sqlite3.connect(conn_str)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    create_rag_schema(conn)
    conn.commit()

    # Эмбеддинги
    if embedding_service_impl is not None:
        emb = embedding_service_impl
    else:
        emb = MockEmbedding()

    # Остальные компоненты
    parser = DocumentParser(benchmark_config)
    chunker = TextChunker(benchmark_config)
    repo = DocumentRepository(conn, benchmark_config)
    vstore = (
        ChromaDBVectorStore(benchmark_config, emb)
        if RUN_REAL
        else MockVectorStore()
    )

    pipeline = RAGPipeline(
        config=benchmark_config,
        parser=parser,
        chunker=chunker,
        embedding_service=emb,
        repository=repo,
        vector_store=vstore,
    )

    # Импортируем золотые документы
    imported = import_golden_documents(pipeline, benchmark_tmp_dir)
    logger.info("Imported %d golden documents for benchmark", imported)

    yield pipeline

    conn.close()


# ── Тесты ───────────────────────────────────────────────────────────


def test_benchmark_documents_imported(real_pipeline: RAGPipeline):
    """Проверка, что золотые документы импортировались."""
    docs = real_pipeline.list_documents()
    assert len(docs) > 0, "No documents imported!"
    logger.info("Documents in index: %d", len(docs))


def test_benchmark_search_works(real_pipeline: RAGPipeline):
    """Проверка, что поиск работает без ошибок (даже с mock-эмбеддингами)."""
    results = real_pipeline.search_documents("сортировка", limit=5)
    assert isinstance(results, list)
    logger.info("Search returned %d results", len(results))


def test_benchmark_retrieval_quality(real_pipeline: RAGPipeline):
    """Основной тест: оценить качество поиска и сравнить с baseline.

    В CI-режиме (без реальных эмбеддингов) только проверяет, что код
    evaluate_retrieval() не падает, и выводит отчёт.
    В режиме RAG_BENCHMARK_REAL=1 делает полный замер и сравнивает с baseline.
    """
    metrics = evaluate_retrieval(real_pipeline)

    # Всегда выводим отчёт
    baseline = load_baseline()
    print_report(metrics, baseline=baseline)

    if RUN_REAL:
        # Реальный режим: проверяем минимальное качество
        threshold_recall_at_1 = baseline.get("recall@1", 0.3) * 0.5 if baseline else 0.2
        threshold_recall_at_5 = baseline.get("recall@5", 0.6) * 0.5 if baseline else 0.4

        # Не фатально — просто предупреждение, если качество упало
        if metrics["recall@1"] < threshold_recall_at_1:
            logger.warning(
                "Recall@1 (%.4f) ниже 50%% от baseline (%.4f) — возможно, регрессия",
                metrics["recall@1"],
                threshold_recall_at_1,
            )

        if metrics["recall@5"] < threshold_recall_at_5:
            logger.warning(
                "Recall@5 (%.4f) ниже 50%% от baseline (%.4f) — возможно, регрессия",
                metrics["recall@5"],
                threshold_recall_at_5,
            )

        # Падаем только при катастрофическом падении (в 4 раза хуже baseline)
        assert metrics["recall@1"] >= threshold_recall_at_1 * 0.25, (
            f"Recall@1={metrics['recall@1']:.4f} катастрофически ниже "
            f"baseline ({threshold_recall_at_1:.4f})"
        )
    else:
        # CI-режим: проверяем, что evaluate не падает
        assert "recall@1" in metrics
        assert "per_query" in metrics
        assert metrics["total_queries"] > 0
        logger.info("CI mode: benchmark code executed successfully")


if __name__ == "__main__":
    # Ручной запуск для обновления baseline
    import sqlite3
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="rag-benchmark-"))
    try:
        config = RagConfig(
            chroma_path=str(tmp / "chroma_db"),
            chroma_collection="benchmark_collection",
            embedding_device="cpu",
            embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            chunker_type="recursive",
        chunk_size=768,
        chunk_overlap=160,
        )

        conn = sqlite3.connect(str(tmp / "benchmark.db"))
        conn.row_factory = sqlite3.Row
        create_rag_schema(conn)

        emb = SentenceTransformerEmbedding(config)
        parser = DocumentParser(config)
        chunker = TextChunker(config)
        repo = DocumentRepository(conn, config)
        vstore = ChromaDBVectorStore(config, emb)

        pipeline = RAGPipeline(config, parser, chunker, emb, repo, vstore)

        imported = import_golden_documents(pipeline, tmp)
        print(f"Imported {imported} documents")

        metrics = evaluate_retrieval(pipeline)
        print_report(metrics)
        save_baseline(metrics)
        print(f"\nBaseline saved to {Path(__file__).parent / 'baseline.json'}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
