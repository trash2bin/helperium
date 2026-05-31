from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DemoSettings:
    api_host: str = os.environ.get("DEMO_API_HOST", "127.0.0.1")
    api_port: int = int(os.environ.get("DEMO_API_PORT", "8081"))
    web_host: str = os.environ.get("DEMO_WEB_HOST", "127.0.0.1")
    web_port: int = int(os.environ.get("DEMO_WEB_PORT", "8080"))
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
    request_timeout: float = float(os.environ.get("DEMO_REQUEST_TIMEOUT", "120"))


settings = DemoSettings()
