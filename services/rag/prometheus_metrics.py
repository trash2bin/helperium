"""Prometheus-метрики для RAG-сервиса.

Все метрики регистрируются при импорте этого модуля.
Экспортируются на /metrics через ASGI app (см. rag/service.py).
"""

from prometheus_client import Counter, Histogram, Gauge

# ── Gauges (point-in-time values) ──

rag_documents = Gauge(
    "rag_documents_total",
    "Total number of documents indexed in SQLite",
)

rag_chunks = Gauge(
    "rag_chunks_total",
    "Total number of chunks in SQLite",
)

rag_chroma_size = Gauge(
    "rag_chroma_size_bytes",
    "ChromaDB persistent storage size in bytes",
)

rag_cache_entries = Gauge(
    "rag_cache_entries",
    "Number of cached search result entries",
)

# ── Counters ──

rag_searches = Counter(
    "rag_searches_total",
    "Total number of search requests",
    ["status"],
)

rag_cache_hits = Counter(
    "rag_cache_hits_total",
    "Total number of cache hits during search",
)

rag_cache_misses = Counter(
    "rag_cache_misses_total",
    "Total number of cache misses during search",
)

# ── Histograms ──

rag_search_duration = Histogram(
    "rag_search_duration_ms",
    "Search duration in milliseconds",
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
)

rag_import_duration = Histogram(
    "rag_import_duration_ms",
    "Document import duration in milliseconds",
    buckets=[100, 500, 1000, 2000, 5000, 10000, 30000, 60000],
)
