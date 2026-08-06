"""Бенчмарк качества семантического поиска RAG.

Используется для регрессионного анализа: лучше или хуже стала выдача
после изменений в чанкере, эмбеддингах, конфиге или вектором хранилище.

Золотой датасет: 12 синтетических документов × 27 запросов с известной
релевантностью. Метрики: Recall@k, MRR.

Запуск:
    # CI (mock эмбеддинги — только регрессия crash/no-crash):
    uv run pytest rag/tests/benchmark/test_search_quality.py -v --tb=short

    # Полный замер качества (нужна embedding-модель):
    RAG_BENCHMARK_REAL=1 uv run pytest rag/tests/benchmark/test_search_quality.py -v --tb=short

    # Обновление baseline:
    RAG_BENCHMARK_REAL=1 uv run python -m rag.tests.benchmark.golden_qa --update-baseline
"""
