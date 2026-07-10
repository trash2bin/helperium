"""Prometheus metric definitions for api-service.

Usage in server.py lifespan::

    from api_service.prometheus_metrics import init_metrics
    await init_metrics(app)

Metrics are exposed at /metrics automatically.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── Chat / Agent Metrics ─────────────────────────────────────────────────────

chat_sessions_total = Counter(
    "chat_sessions_total",
    "Total chat sessions created",
)

chat_messages_total = Counter(
    "chat_messages_total",
    "Total chat messages sent, by status",
    ["status"],  # sent, error, filtered
)

llm_calls_total = Counter(
    "llm_calls_total",
    "Total LLM calls by model and provider",
    ["model", "provider"],
)

llm_duration_ms = Histogram(
    "llm_duration_ms",
    "LLM call duration in milliseconds",
    ["model"],
    buckets=(500, 1000, 2000, 5000, 10000, 30000, 60000, 120000),
)

llm_token_usage = Counter(
    "llm_token_usage",
    "Token usage by type (prompt, completion, total)",
    ["type"],
)

llm_cost_total = Counter(
    "llm_cost_total",
    "Total LLM cost in USD",
)

abuse_blocked_total = Counter(
    "abuse_blocked_total",
    "Requests blocked by anti-abuse engine, by reason",
    [
        "reason"
    ],  # rate_limit, user_agent, message_length, interval, session_budget, repeated_text
)

embed_widget_requests = Counter(
    "embed_widget_requests_total",
    "Total embed widget requests by endpoint",
    ["endpoint"],  # chat, config
)

# ── Backlog Metrics ──────────────────────────────────────────────────────────

backlog_records_total = Counter(
    "backlog_records_total",
    "Total backlog records written",
    ["type"],  # llm_call, error, tool_call
)

backlog_errors_total = Counter(
    "backlog_errors_total",
    "Total errors recorded in backlog",
    ["error_type"],  # llm_timeout, llm_429, llm_error, internal
)


_instrumented: bool = False


def init_metrics(app) -> None:
    """Register custom Prometheus metrics.

    Idempotent — safe to call multiple times.
    Does NOT expose /metrics — that's done via an explicit @app.get handler.
    """
    global _instrumented
    if _instrumented:
        return
    _instrumented = True
