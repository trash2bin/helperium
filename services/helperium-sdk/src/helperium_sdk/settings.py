"""Shared settings for helperium services.

Moved here from demo/settings.py to break the reverse dependency
api-service → demo. Both api-service and demo import from here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    """Return the project root directory.

    Uses PROJECT_ROOT env var if set (for tests/CI), otherwise falls back
    to cwd. Called at runtime (not import time) so tests can override.
    """
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env)
    return Path.cwd()


class DemoSettings:
    """Конфигурация helperium-сервисов.

    Все поля читаются из env при создании экземпляра (не на уровне класса),
    чтобы тесты могли переопределять env vars через patch.dict(os.environ, ...).
    """

    def __init__(self) -> None:
        self.api_host: str = os.environ.get("DEMO_API_HOST", "127.0.0.1")
        self.api_port: int = int(os.environ.get("DEMO_API_PORT", "8081"))
        self.web_host: str = os.environ.get("DEMO_WEB_HOST", "127.0.0.1")
        self.web_port: int = int(os.environ.get("DEMO_WEB_PORT", "8080"))
        self.web_origin: str = os.environ.get("WEB_ORIGIN", "http://localhost:8080")
        self.api_bearer_token: str | None = os.environ.get("API_BEARER_TOKEN")
        self.ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")

        self.request_timeout: float = float(
            os.environ.get("DEMO_REQUEST_TIMEOUT", 120 * 5)
        )
        self.python_executable: str = os.environ.get(
            "PYTHON_EXECUTABLE", sys.executable or "python3"
        )
        self.think_mode: bool = os.environ.get("ENABLE_THINK", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.history_turns: int = int(os.environ.get("DEMO_HISTORY_TURNS", "8"))
        self.history_content_chars: int = int(
            os.environ.get("DEMO_HISTORY_CONTENT_CHARS", "6000")
        )
        self.session_db_path: str = os.environ.get(
            "DEMO_SESSION_DB_PATH", str(project_root() / "demo_sessions.sqlite")
        )

        # Tenant configuration for web UI
        self.default_tenant_id: str = os.environ.get("DEFAULT_TENANT_ID", "default")
        self.demo_tenants: str = os.environ.get("DEMO_TENANTS", "")

        # Optional admin-managed agent record used as the direct-chat quality
        # profile (system prompt, llm_config, provider_priority). Tenant scope
        # is NEVER taken from this record: direct chat stays bound to
        # DEFAULT_TENANT_ID per the tenant authority contract. Empty value
        # keeps the legacy pool/env provider resolution.
        self.direct_chat_agent: str = os.environ.get("DIRECT_CHAT_AGENT", "").strip()

        # Model backlog (full interaction tracing)
        self.backlog_dir: str = os.environ.get(
            "BACKLOG_DIR", str(project_root() / "backlog")
        )
        self.backlog_retention_days: int = int(
            os.environ.get("BACKLOG_RETENTION_DAYS", "30")
        )
        self.backlog_mode: str = os.environ.get("BACKLOG_MODE", "full")

        # MCP gateway uses one standard Streamable HTTP endpoint.
        self.mcp_gateway_url: str = os.environ.get(
            "MCP_GATEWAY_URL", "http://127.0.0.1:8083"
        ).rstrip("/")
        self.mcp_streamable_http_url: str = os.environ.get(
            "MCP_STREAMABLE_HTTP_URL",
            self.mcp_gateway_url + "/mcp",
        )
        # Optional service credential for api-service → mcp-gateway. Keep this
        # distinct from public/admin tokens and inject it only at deployment.
        self.mcp_client_api_key: str | None = os.environ.get("MCP_CLIENT_API_KEY")

        # Direct service URLs (for web proxy bypassing api-service)
        self.data_service_url: str = os.environ.get(
            "DATA_SERVICE_URL", "http://127.0.0.1:8084"
        )
        self.rag_service_url: str = os.environ.get(
            "RAG_SERVICE_URL", "http://127.0.0.1:8082"
        )

        # Web proxy HTTP client timeout (seconds)
        self.web_proxy_timeout: float = float(
            os.environ.get("WEB_PROXY_TIMEOUT", "30.0")
        )

        # Model parameters
        self.agent_temperature: float = float(
            os.environ.get("AGENT_TEMPERATURE", "0.5")
        )
        self.agent_max_iterations: int = int(
            os.environ.get("AGENT_MAX_ITERATIONS", "5")
        )
        self.agent_max_tokens_thinking: int = int(
            os.environ.get("AGENT_MAX_TOKENS_THINKING", "4096")
        )
        self.agent_max_empty_rounds: int = int(
            os.environ.get("AGENT_MAX_EMPTY_ROUNDS", "3")
        )
        self.agent_max_turn_tokens: int = int(
            os.environ.get("AGENT_MAX_TURN_TOKENS", "8000")
        )
        self.agent_max_tool_calls: int = int(
            os.environ.get("AGENT_MAX_TOOL_CALLS", "10")
        )
        self.agent_fallback_max_messages: int = int(
            os.environ.get("AGENT_FALLBACK_MAX_MESSAGES", "7")
        )

        # Internal LLM completion retry policy. This stays process-wide because
        # retry is a transport reliability policy, not public per-agent config.
        self.llm_max_attempts: int = int(os.environ.get("LLM_MAX_ATTEMPTS", "3"))
        self.llm_retry_max_elapsed_seconds: float = float(
            os.environ.get("LLM_RETRY_MAX_ELAPSED_SECONDS", "60.0")
        )
        self.llm_retry_transient_base_seconds: float = float(
            os.environ.get("LLM_RETRY_TRANSIENT_BASE_SECONDS", "0.25")
        )
        self.llm_retry_throttled_base_seconds: float = float(
            os.environ.get("LLM_RETRY_THROTTLED_BASE_SECONDS", "1.0")
        )
        self.llm_retry_max_backoff_seconds: float = float(
            os.environ.get("LLM_RETRY_MAX_BACKOFF_SECONDS", "4.0")
        )

        # ── Guardrails ──────────────────────────────────────────────────
        self.guardrail_enabled: bool = os.environ.get(
            "GUARDRAIL_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.guardrail_block_patterns: str = os.environ.get(
            "GUARDRAIL_BLOCK_PATTERNS", ""
        )
        self.guardrail_block_on_match: str = os.environ.get(
            "GUARDRAIL_BLOCK_ON_MATCH", "block"
        )

        # ── Spending Limits ─────────────────────────────────────────────
        # ── MCP Client ─────────────────────────────────────────────────
        self.mcp_max_consecutive_failures: int = int(
            os.environ.get("MCP_MAX_CONSECUTIVE_FAILURES", "3")
        )
        self.mcp_circuit_breaker_timeout: float = float(
            os.environ.get("MCP_CIRCUIT_BREAKER_TIMEOUT", "30.0")
        )
        self.mcp_gc_interval: float = float(os.environ.get("MCP_GC_INTERVAL", "60.0"))
        self.mcp_max_idle_seconds: float = float(
            os.environ.get("MCP_MAX_IDLE_SECONDS", "600.0")
        )
        self.mcp_lock_acquire_timeout: float = float(
            os.environ.get("MCP_LOCK_ACQUIRE_TIMEOUT", "10.0")
        )
        self.mcp_tool_execution_timeout: float = float(
            os.environ.get("MCP_TOOL_EXECUTION_TIMEOUT", "15.0")
        )
        self.mcp_close_escalation_timeout: float = float(
            os.environ.get("MCP_CLOSE_ESCALATION_TIMEOUT", "5.0")
        )
        self.mcp_zombie_tool_timeouts: int = int(
            os.environ.get("MCP_ZOMBIE_TOOL_TIMEOUTS", "2")
        )
        self.mcp_http_timeout: float = float(os.environ.get("MCP_HTTP_TIMEOUT", "10.0"))
        self.mcp_http_read_timeout: float = float(
            os.environ.get("MCP_HTTP_READ_TIMEOUT", "1800.0")
        )
        self.mcp_session_init_timeout: float = float(
            os.environ.get("MCP_SESSION_INIT_TIMEOUT", "15.0")
        )

        self.spending_limit_enabled: bool = os.environ.get(
            "SPENDING_LIMIT_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.spending_default_budget: float = float(
            os.environ.get("SPENDING_DEFAULT_BUDGET", "50.0")
        )
        self.spending_budget_period: str = os.environ.get(
            "SPENDING_BUDGET_PERIOD", "monthly"
        )


settings = DemoSettings()
