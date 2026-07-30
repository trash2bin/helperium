"""User-friendly error messages for LLM agent errors.

Maps internal exceptions to human-readable messages in multiple languages.
Language is detected from the Accept-Language header at the API service level.

Uses a two-phase strategy:
  1. Type-based checks (``isinstance``) for known exception hierarchies
     (litellm.exceptions, httpx, builtins).
  2. Substring fallback for plain ``Exception`` wrappers.

See also:
    - ``server.py:_get_lang()`` — Accept-Language parsing
    - ``embed/README.md`` — widget data-lang attribute
    - ``api-service/README.md`` — error message table
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Error categories ────────────────────────────────────────────────────

ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "rate_limit": {
        "ru": "Сервер временно перегружен. Пожалуйста, повторите ваш вопрос через несколько секунд.",
        "en": "Server is temporarily overloaded. Please retry your question in a few seconds.",
    },
    "auth": {
        "ru": "Ошибка доступа к модели. Попробуйте позже или обратитесь к администратору.",
        "en": "Model access error. Please try again later or contact the administrator.",
    },
    "context_length": {
        "ru": "Диалог слишком длинный. Пожалуйста, начните новый разговор.",
        "en": "The conversation is too long. Please start a new chat.",
    },
    "connection": {
        "ru": "Не удалось подключиться к серверу данных. Попробуйте позже.",
        "en": "Failed to connect to the data server. Please try again later.",
    },
    "timeout": {
        "ru": "Модель не отвечает. Пожалуйста, попробуйте снова или задайте более короткий вопрос.",
        "en": "The model is not responding. Please try again or ask a shorter question.",
    },
    "provider": {
        "ru": "Ошибка при обработке запроса моделью. Попробуйте позже.",
        "en": "An error occurred while processing your request. Please try again later.",
    },
    "mcp": {
        "ru": "Не удалось выполнить запрос к базе данных. Попробуйте позже.",
        "en": "Failed to query the database. Please try again later.",
    },
    "internal": {
        "ru": "Извините, произошла внутренняя ошибка. Попробуйте ещё раз.",
        "en": "Sorry, an internal error occurred. Please try again.",
    },
}

# Lazy import cache for performance
_litellm_exceptions: object = None  # module or None
_httpx_module: object = None  # module or None


def _import_litellm_exceptions() -> object:
    """Lazy import litellm.exceptions, cached after first call."""
    global _litellm_exceptions
    if _litellm_exceptions is None:
        try:
            import litellm.exceptions as m  # type: ignore[import-untyped]

            _litellm_exceptions = m
        except ImportError:
            _litellm_exceptions = False  # type: ignore[assignment]
    return _litellm_exceptions if _litellm_exceptions is not False else None


def _import_httpx() -> object:
    """Lazy import httpx, cached after first call."""
    global _httpx_module
    if _httpx_module is None:
        try:
            import httpx as m

            _httpx_module = m
        except ImportError:
            _httpx_module = False  # type: ignore[assignment]
    return _httpx_module if _httpx_module is not False else None


def classify_error(exc: Exception, lang: str = "ru") -> str:
    """Map an exception to a user-friendly message.

    Two-phase strategy:
      1. **Type-based** (``isinstance``): exact exception hierarchy matching
         for litellm.exceptions, httpx, and Python builtins.
      2. **Substring fallback**: for plain ``Exception`` wrappers and
         third-party libraries not covered by phase 1.

    Unwraps ``ExceptionGroup`` automatically — classifies the first inner
    exception for accurate error grouping.

    Args:
        exc: The exception to classify.
        lang: Language code (``"ru"`` or ``"en"``).

    Returns:
        A human-readable message in the requested language.
    """
    # ── Phase 0: ExceptionGroup unwrap ────────────────────────────────
    if isinstance(exc, ExceptionGroup) and exc.exceptions:
        return classify_error(exc.exceptions[0], lang)

    exc_str = str(exc).lower()
    exc_type_name = type(exc).__name__.lower()

    # ── Phase 1: Type-based checks (most reliable) ────────────────────
    litellm_exc = _import_litellm_exceptions()
    httpx_mod = _import_httpx()

    # 1a. litellm RateLimitError → rate_limit
    if litellm_exc is not None and isinstance(exc, litellm_exc.RateLimitError):  # type: ignore[union-attr]
        return _msg("rate_limit", lang)

    # 1b. litellm AuthenticationError → auth
    if litellm_exc is not None and isinstance(exc, litellm_exc.AuthenticationError):  # type: ignore[union-attr]
        return _msg("auth", lang)

    # 1c. litellm ContextWindowExceededError → context_length
    if litellm_exc is not None and isinstance(
        exc, litellm_exc.ContextWindowExceededError
    ):  # type: ignore[union-attr]
        return _msg("context_length", lang)

    # 1d. HTTPX connection errors → connection
    if httpx_mod is not None and isinstance(
        exc,
        (
            httpx_mod.ConnectError,  # type: ignore[union-attr]
            httpx_mod.NetworkError,  # type: ignore[union-attr]
            httpx_mod.RemoteProtocolError,  # type: ignore[union-attr]
        ),
    ):
        return _msg("connection", lang)

    # 1e. litellm APIConnectionError → connection
    if litellm_exc is not None and isinstance(exc, litellm_exc.APIConnectionError):  # type: ignore[union-attr]
        return _msg("connection", lang)

    # 1f. Python builtin ConnectionError → connection
    # Covers: database auth failures, connection refused, socket errors
    if isinstance(exc, ConnectionError):
        return _msg("connection", lang)

    # 1g. litellm BadRequestError (non-context) → provider
    # ContextWindowExceededError inherits from BadRequestError but is
    # caught above as context_length.  Remaining BadRequestError types
    # (invalid params, bad json, etc.) → provider.
    if litellm_exc is not None and isinstance(exc, litellm_exc.BadRequestError):  # type: ignore[union-attr]
        return _msg("provider", lang)

    # 1h. litellm server-side errors → provider
    if litellm_exc is not None and isinstance(
        exc,
        (
            litellm_exc.ServiceUnavailableError,  # type: ignore[union-attr]
            litellm_exc.InternalServerError,  # type: ignore[union-attr]
        ),
    ):
        return _msg("provider", lang)

    # 1i. TimeoutError (builtin / asyncio) → timeout
    if isinstance(exc, TimeoutError):
        return _msg("timeout", lang)

    # 1j. HTTPX TimeoutException → timeout
    if httpx_mod is not None and isinstance(exc, httpx_mod.TimeoutException):  # type: ignore[union-attr]
        return _msg("timeout", lang)

    # ── Phase 2: Substring fallback (plain Exception wrappers) ────────

    # 2a. Rate limiting
    if (
        "rate" in exc_str
        or "ratelimit" in exc_type_name
        or "429" in exc_str
        or "too many requests" in exc_str
        or "retry after" in exc_str
    ):
        return _msg("rate_limit", lang)

    # 2b. Context length — checked BEFORE provider substring because
    #     litellm exceptions can carry both "litellm" and "context" in
    #     their string representation.
    if ("context" in exc_str or "token" in exc_str) and (
        "length" in exc_str
        or "limit" in exc_str
        or "exceed" in exc_str
        or "too large" in exc_str
    ):
        # Protection against false positives from non-context errors
        # e.g. "tokenizer exceeded maximum length" or "token bucket mismatch"
        _false_positive = ("tokenizer", "token bucket", "token mismat")
        if not any(pat in exc_str for pat in _false_positive):
            return _msg("context_length", lang)

    # 2c. LLM provider errors (litellm wrapping OpenAI/Anthropic/Ollama)
    if any(
        prov in exc_str or prov in exc_type_name
        for prov in ("litellm", "openai", "anthropic", "ollama", "groq", "mistral")
    ):
        return _msg("provider", lang)

    # 2d. Authentication / authorization — more restrictive than the
    #     old single "auth" substring to avoid false positives like
    #     "database authentication failed".
    if (
        "401" in exc_str
        or "403" in exc_str
        or "unauthorized" in exc_str
        or "invalid api key" in exc_str
        or "authentication" in exc_str
        or "api_key" in exc_str
    ):
        return _msg("auth", lang)

    # 2e. Timeout
    if "timeout" in exc_str or "timed out" in exc_str:
        return _msg("timeout", lang)

    # 2f. Connection errors
    if any(
        kw in exc_str
        for kw in (
            "connection refused",
            "connection reset",
            "connection attempts",
            "connection failed",
        )
    ):
        return _msg("connection", lang)

    # 2g. MCP / gateway (last resort — checked after all other known
    #     error types to avoid misclassifying provider errors as MCP)
    if "mcp" in exc_str or "gateway" in exc_str:
        return _msg("mcp", lang)

    # 2h. Fallback → internal
    logger.debug(
        "[ERROR_MSG] Unclassified exception type=%s: %s", exc_type_name, exc_str[:120]
    )
    return _msg("internal", lang)


def _msg(key: str, lang: str) -> str:
    """Get a message for the given key and language, falling back to the other."""
    langs = ERROR_MESSAGES.get(key, ERROR_MESSAGES["internal"])
    if lang.startswith("ru") and "ru" in langs:
        return langs["ru"]
    return langs.get("en", ERROR_MESSAGES["internal"]["en"])
