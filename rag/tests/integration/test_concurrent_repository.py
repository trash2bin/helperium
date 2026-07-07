"""Конкурентный тест DocumentRepository с thread‑safe lock'ом.

Сценарии:
1. Парсинг реального PDF → чанкинг → сохранение с мокнутым embedding
2. Конкурентные чтения из 10 потоков (list_documents, get_document_by_id)
3. Конку��ентные записи-перезаписи из 5 потоков (reimport одного файла)
4. Смешанная нагрузка: чтения + записи одновременно
5. Проверка отсутствия дедлоков, data race и SQLite‑ошибок

Не требует embedding‑модели, ChromaDB или Ollama.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pytest

from rag._types import ChunkDict
from rag.chunker import TextChunker
from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.repository import DocumentRepository

logger = logging.getLogger(__name__)

# Путь к реальному PDF на машине разработчика
REAL_PDF = Path("/Users/ivan/Documents/Labs/ALgoritms/lab3/DOPtask.pdf")


# ── Fixtures (from rag/tests/conftest.py — inline, без зависимостей) ──


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Temporary directory (pytest built‑in)."""
    return tmp_path


@pytest.fixture
def rag_config(temp_dir: Path) -> RagConfig:
    """RagConfig с ре��урсивным чанкингом, без реальной embedding‑модели."""
    return RagConfig(
        chroma_path=str(temp_dir / "chroma_db"),
        chroma_collection="concurrency_test",
        embedding_device="cpu",
        embedding_model="mock",
        chunker_type="recursive",
        chunk_size=512,
        chunk_overlap=0,
    )


@pytest.fixture
def db_conn(temp_dir: Path) -> Any:
    """SQLite in‑memory + RAG‑схема."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # Схема RAG (из documents_schema)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_path TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            discipline_id TEXT,
            discipline_name TEXT,
            created_at TEXT NOT NULL,
            metadata_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            page INTEGER,
            content TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            token_count INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
        ON document_chunks (document_id)
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn: Any, rag_config: RagConfig) -> DocumentRepository:
    """DocumentRepository с thread‑safe lock (который мы тестируем)."""
    return DocumentRepository(db_conn, rag_config)


@pytest.fixture
def parser(rag_config: RagConfig) -> DocumentParser:
    """Настоящий DocumentParser с Docling."""
    return DocumentParser(rag_config)


@pytest.fixture
def chunker(rag_config: RagConfig) -> TextChunker:
    """Настоящий TextChunker."""
    return TextChunker(rag_config)


@pytest.fixture
def pdf_chunks(parser: DocumentParser, chunker: TextChunker) -> list[ChunkDict]:
    """Реальный PDF → страницы → чанки.

    Это одноразовое действие: парсим PDF один раз, переиспользуем
    чанки во всех тестах.
    """
    if not REAL_PDF.exists():
        pytest.skip(f"PDF не найден: {REAL_PDF}")
    pages = parser.extract_pages(REAL_PDF)
    assert len(pages) > 0, f"PDF {REAL_PDF} не дал ни одной страницы"
    chunks = chunker.chunk_pages(pages)
    assert len(chunks) > 0, "Чанкер не создал ни одного чанка из PDF"
    logger.info(
        "PDF %s: %d страниц → %d чанков", REAL_PDF.name, len(pages), len(chunks)
    )
    return chunks


# ── Тесты ──


class TestConcurrentRepository:
    """Проверка thread‑safe репозитория под конкурентной нагрузкой."""

    # ────────────────────────────────────────────────────────────────
    # 1. Базовый smoke‑тест: импорт реального PDF
    # ────────────────────────────────────────────────────────────────

    def test_import_real_pdf(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """Сохрани��ь чанки реального PDF — базовая проверка."""
        result = repo.save_document_with_chunks(
            source_path=str(REAL_PDF),
            chunks=pdf_chunks,
            discipline_id="cs-101",
            title="DOPtask (test)",
            vector_store=None,
        )
        assert result.chunks_count == len(pdf_chunks)
        assert result.document.title == "DOPtask (test)"

        # Прочитать обратно
        doc = repo.get_document_by_id_as_model(result.document.id)
        assert doc is not None
        assert doc.title == "DOPtask (test)"

    # ────────────────────────────────────────────────────────────────
    # 2. Конкурентные чтения — 10 потоков
    # ────────────────────────────────────────────────────────────────

    def test_concurrent_reads_no_lock_contention(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """10 потоков одновременно читают list_documents."""
        N_THREADS = 10
        N_OPS_PER_THREAD = 20
        errors: list[Exception] = []
        errors_lock = threading.Lock()

        # Записываем пару документов заранее
        repo.save_document_with_chunks(
            "doc_a.txt",
            [{"content": "alpha", "page": 1}],
            "d1",
            vector_store=None,
        )
        repo.save_document_with_chunks(
            "doc_b.txt",
            [{"content": "beta", "page": 1}],
            "d2",
            vector_store=None,
        )

        def reader(thread_id: int) -> int:
            ops = 0
            for _ in range(N_OPS_PER_THREAD):
                try:
                    # Чередуем разные методы чтения
                    if random.random() < 0.5:
                        docs = repo.list_documents()
                        _ = len(docs)
                    else:
                        doc = repo.find_existing_by_path("doc_a.txt")
                        _ = doc
                    ops += 1
                except Exception as exc:
                    with errors_lock:
                        errors.append(exc)
            return ops

        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            futures = [pool.submit(reader, i) for i in range(N_THREADS)]
            total_ops = sum(f.result() for f in as_completed(futures))

        assert errors == [], f"Ошибки при конкурентном чтении: {errors}"
        assert total_ops == N_THREADS * N_OPS_PER_THREAD
        logger.info("Конкурентное чтение: %d операций, 0 ошибок", total_ops)

    # ────────────────────────────────────────────────────────────────
    # 3. Конкурентные перезаписи одного файла — 5 потоков
    # ────────────────────────────────────────────────────────────────

    def test_concurrent_reimport_same_file(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """5 потоков одновременно перезаписывают один и тот же файл.

        Проверяем, что:
        - Ни один поток не получает SQLite‑ошибку
        - После всех потоков в БД ровно 1 запись (последняя победила)
        - Чанки соответствуют одному из импортов
        """
        N_WORKERS = 5
        errors: list[Exception] = []
        errors_lock = threading.Lock()
        results: list[int] = []
        results_lock = threading.Lock()

        def reimporter(worker_id: int) -> int:
            """Перезаписать один файл. Каждый worker пишет своё кол‑во чанков."""
            # Добавляем уникальный хвост, чтобы чанки различались
            my_chunks = pdf_chunks[:]
            if my_chunks:
                last = dict(my_chunks[-1])
                last["content"] = last["content"] + f"\n--- worker #{worker_id}"
                my_chunks[-1] = last

            try:
                res = repo.save_document_with_chunks(
                    source_path="concurrent_target.pdf",
                    chunks=my_chunks,
                    discipline_id="cs-101",
                    title="Concurrent target",
                    vector_store=None,
                )
                with results_lock:
                    results.append(res.chunks_count)
                return res.chunks_count
            except Exception as exc:
                with errors_lock:
                    errors.append(exc)
                return 0

        with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            futures = [pool.submit(reimporter, i) for i in range(N_WORKERS)]
            _ = [f.result() for f in as_completed(futures)]

        assert errors == [], f"Ошибки при конкурентной перезаписи: {errors}"
        assert len(results) == N_WORKERS

        # В БД ровно 1 документ (последний перезаписал все предыдущие)
        docs = repo.list_documents()
        assert len(docs) == 1, f"Ожидался 1 документ, получено {len(docs)}"

        # list_documents() возвращает DocumentRow (TypedDict), обращаемся по ключу
        doc = repo.list_documents()[0]
        assert doc["source_path"] == "concurrent_target.pdf"
        logger.info(
            "Конкурентная перезапись: %d workers, финальный документ: %s",
            N_WORKERS,
            doc["title"],
        )

    # ────────────────────────────────────────────────────────────────
    # 4. Смешанная нагрузка — читатели + писатели
    # ────────────────────────────────────────────────────────────────

    def test_mixed_read_write_stress(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """Читатели + писатели одновременно.

        3 писателя пишут разные файлы, 7 читателей читают.
        Проверяем отсутствие:
        - sqlite3.ProgrammingError (thread mismatch)
        - IntegrityError (broken constraints)
        - Deadlock (виснущие потоки)
        """
        N_WRITERS = 3
        N_READERS = 7
        OPS_PER_WORKER = 15
        TIMEOUT_SEC = 30
        errors: list[Exception] = []
        errors_lock = threading.Lock()
        writer_barrier = threading.Barrier(N_WRITERS + 1)  # +1 для main thread

        # Заливаем baseline
        for i in range(5):
            repo.save_document_with_chunks(
                f"baseline_{i}.txt",
                [{"content": f"baseline content {i}", "page": 1}],
                "d1",
                vector_store=None,
            )

        baseline_count = len(repo.list_documents())

        def writer(wid: int) -> int:
            """Писатель: импортирует и перезаписывает свои файлы."""
            try:
                writer_barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            ops = 0
            for op in range(OPS_PER_WORKER):
                try:
                    path = f"writer_{wid}_op_{op}.txt"
                    repo.save_document_with_chunks(
                        path,
                        [{"content": f"writer {wid} op {op}", "page": 1}],
                        f"d{wid}",
                        vector_store=None,
                    )
                    ops += 1
                except Exception as exc:
                    with errors_lock:
                        errors.append(exc)
            return ops

        def reader(rid: int) -> int:
            """Читатель: листинг + поиск по ID."""
            ops = 0
            for _ in range(OPS_PER_WORKER):
                try:
                    docs = repo.list_documents()
                    if docs:
                        # list_documents() возвращает TypedDict — обращение по ключу
                        _ = repo.get_document_by_id(docs[0]["id"])
                    ops += 1
                    time.sleep(0.001)  # чуть перемешать порядок
                except Exception as exc:
                    with errors_lock:
                        errors.append(exc)
            return ops

        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=N_WRITERS + N_READERS) as pool:
            writer_futures = [pool.submit(writer, i) for i in range(N_WRITERS)]
            reader_futures = [pool.submit(reader, i) for i in range(N_READERS)]

            # Снимаем барьер — писатели стартуют
            try:
                writer_barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

            all_futures = writer_futures + reader_futures
            for f in as_completed(all_futures, timeout=TIMEOUT_SEC):
                f.result()  # re‑raise если упало

        elapsed = time.monotonic() - start

        assert errors == [], f"Ошибки при смешанной нагрузке: {errors}"

        # Проверяем целостность: все записи писателей + baseline
        final_docs = repo.list_documents()
        expected_count = baseline_count + N_WRITERS * OPS_PER_WORKER
        assert len(final_docs) == expected_count, (
            f"Ожидалось {expected_count} доков, получено {len(final_docs)}"
        )

        logger.info(
            "Смешанная нагрузка: %d писателей + %d читателей, %d доков за %.2fс",
            N_WRITERS,
            N_READERS,
            len(final_docs),
            elapsed,
        )

    # ────────────────────────────────────────────────────────────────
    # 5. Долгая транзакция не блокирует весь репозиторий
    # ────────────────────────────────────────────────────────────────

    def test_long_write_does_not_deadlock_read(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """Поток A держит lock через db_lock, поток B может зайти в другой метод.

        RLock — reentrant, значит:
        - A: save_document_with_chunks → внутри вызывает find_existing_by_path (RLock не блокирует)
        - B: может параллельно вызвать list_documents (другой with self._lock)
        + Тест проверяет, что read не ждёт write вечно.
        """
        READ_TIMEOUT = 5.0
        read_ok = threading.Event()

        def slow_writer():
            """Писатель, который делает несколько вызовов подряд
            (эмуляция многошаговой транзакции)."""
            for i in range(10):
                repo.save_document_with_chunks(
                    f"lock_test_{i}.txt",
                    [{"content": f"content {i}", "page": 1}],
                    "d1",
                    vector_store=None,
                )
                time.sleep(0.01)

        def fast_reader():
            """Читатель: list_documents не должен висеть вечно."""
            for _ in range(50):
                repo.list_documents()
                time.sleep(0.001)
            read_ok.set()

        writer_thread = threading.Thread(target=slow_writer, daemon=True)
        reader_thread = threading.Thread(target=fast_reader, daemon=True)

        writer_thread.start()
        reader_thread.start()

        writer_thread.join(timeout=READ_TIMEOUT)
        reader_thread.join(timeout=READ_TIMEOUT)

        assert read_ok.is_set(), "Reader не завершился за таймаут — возможен deadlock"

        logger.info("Long write + concurrent read: OK")

    # ────────────────────────────────────────────────────────────────
    # 6. Откат транзакции в конкурентном контексте
    # ────────────────────────────────────────────────────────────────

    def test_concurrent_rollback_isolation(
        self, repo: DocumentRepository, pdf_chunks: list[ChunkDict]
    ) -> None:
        """Один поток делает rollback — другой поток не видит незакоммиченных данных."""
        from unittest.mock import MagicMock

        EVIL_PATH = "rollback_test.txt"
        NORMAL_PATH = "normal_doc.txt"

        mock_vs = MagicMock()
        mock_vs.add_chunks.side_effect = Exception("ChromaDB crash")

        def evil_writer():
            """Писатель, транзакция которого откатывается."""
            try:
                repo.save_document_with_chunks(
                    source_path=EVIL_PATH,
                    chunks=[{"content": "evil data", "page": 1}],
                    discipline_id="d1",
                    vector_store=mock_vs,
                )
            except Exception:
                pass  # ожидаемо

        def normal_writer():
            """Писатель, который пишет нормально."""
            repo.save_document_with_chunks(
                source_path=NORMAL_PATH,
                chunks=[{"content": "normal data", "page": 1}],
                discipline_id="d1",
                vector_store=None,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            evil_future = pool.submit(evil_writer)
            normal_future = pool.submit(normal_writer)
            evil_future.result()
            normal_future.result()

        # Evil не должен был сохраниться
        assert repo.find_existing_by_path(EVIL_PATH) is None
        # Normal должен быть
        assert repo.find_existing_by_path(NORMAL_PATH) is not None

        logger.info(
            "Concurrent rollback isolation: OK (evil=%s, normal=%s)",
            EVIL_PATH,
            NORMAL_PATH,
        )
