from __future__ import annotations

import os
import sys
from pathlib import Path

# demo-сервисы запускаются из корня проекта (scripts/dev.sh стартует из корня),
# поэтому пути относительно cwd. Для другого cwd — переопредели env vars ниже.
PROJECT_ROOT = Path.cwd()


class DemoSettings:
    """Конфигурация demo-сервисов.

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
            "DEMO_SESSION_DB_PATH", str(PROJECT_ROOT / "demo_sessions.sqlite")
        )

        # Tenant configuration for web UI
        self.default_tenant_id: str = os.environ.get("DEFAULT_TENANT_ID", "default")
        self.demo_tenants: str = os.environ.get("DEMO_TENANTS", "")

        # Model backlog (full interaction tracing)
        self.backlog_dir: str = os.environ.get(
            "BACKLOG_DIR", str(PROJECT_ROOT / "backlog")
        )
        self.backlog_retention_days: int = int(
            os.environ.get("BACKLOG_RETENTION_DAYS", "30")
        )

        # MCP service URL for HTTP transport
        self.mcp_service_url: str = os.environ.get(
            "MCP_SERVICE_URL", "http://127.0.0.1:8083/mcp"
        )

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
