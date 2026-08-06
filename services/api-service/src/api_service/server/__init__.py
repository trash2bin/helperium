"""Server package — FastAPI application.

Re-exports for backward compatibility with ``from api_service.server import app``.
"""

from .app import app, main
from .rate_limit import rate_limit
from .sse import _sse, _event_payload, _single_error, _get_lang
from .routes.chat import chat_voice_endpoint, chat_agent_handler, chat_endpoint

__all__ = [
    "app",
    "rate_limit",
    "main",
    "_sse",
    "_event_payload",
    "_single_error",
    "_get_lang",
    "chat_voice_endpoint",
    "chat_agent_handler",
    "chat_endpoint",
]
