from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DemoSettings:
    api_host: str = os.environ.get("DEMO_API_HOST", "127.0.0.1")
    api_port: int = int(os.environ.get("DEMO_API_PORT", "8081"))
    web_host: str = os.environ.get("DEMO_WEB_HOST", "127.0.0.1")
    web_port: int = int(os.environ.get("DEMO_WEB_PORT", "8080"))
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
    request_timeout: float = float(os.environ.get("DEMO_REQUEST_TIMEOUT", 120*5))
    python_executable: str = os.environ.get("PYTHON_EXECUTABLE", sys.executable or "python3")
    think_mode: bool = os.environ.get("ENABLE_THINK", "true").lower() in ("1", "true", "yes")
    history_turns: int = int(os.environ.get("DEMO_HISTORY_TURNS", "8"))
    history_content_chars: int = int(os.environ.get("DEMO_HISTORY_CONTENT_CHARS", "6000"))
    session_db_path: str = os.environ.get("DEMO_SESSION_DB_PATH", str(PROJECT_ROOT / "demo_sessions.sqlite"))

    # Model backlog (full interaction tracing)
    backlog_dir: str = os.environ.get("BACKLOG_DIR", str(PROJECT_ROOT / "backlog"))
    backlog_retention_days: int = int(os.environ.get("BACKLOG_RETENTION_DAYS", "30"))


settings = DemoSettings()
