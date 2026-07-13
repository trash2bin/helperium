"""User-friendly error messages for LLM agent errors.

Maps internal exceptions to human-readable messages in multiple languages.
Language is detected from the Accept-Language header at the API service level.

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
    "no_response": {
        "ru": "Не удалось получить ответ от модели. Пожалуйста, переформулируйте вопрос.",
        "en": "No response from the model. Please rephrase your question.",
    },
}


def classify_error(exc: Exception, lang: str = "ru") -> str:
    """Map an exception to a user-friendly message.

    Unwraps ``ExceptionGroup`` automatically — classifies the first inner
    exception for accurate error grouping.

    Args:
        exc: The exception to classify.
        lang: Language code (``"ru"`` or ``"en"``).

    Returns:
        A human-readable message in the requested language.
    """
    # Unwrap ExceptionGroup — the outer message is useless for classification
    if isinstance(exc, ExceptionGroup) and exc.exceptions:
        return classify_error(exc.exceptions[0], lang)

    exc_str = str(exc).lower()
    exc_type_name = type(exc).__name__.lower()

    # Rate limiting (literal "rate" / "429" / "too many")
    if (
        "rate" in exc_str
        or "ratelimit" in exc_type_name
        or "429" in exc_str
        or "too many requests" in exc_str
        or "retry after" in exc_str
    ):
        return _msg("rate_limit", lang)

    # Authentication / authorization
    if (
        "auth" in exc_str
        or "api_key" in exc_str
        or "unauthorized" in exc_str
        or "401" in exc_str
        or "403" in exc_str
        or "authentication" in exc_str
        or "invalid api key" in exc_str
    ):
        return _msg("auth", lang)

    # Token / context length exceeded
    if ("context" in exc_str or "token" in exc_str) and (
        "length" in exc_str
        or "limit" in exc_str
        or "exceed" in exc_str
        or "too large" in exc_str
    ):
        return _msg("context_length", lang)

    # Timeout
    if "timeout" in exc_str or "timed out" in exc_str:
        return _msg("timeout", lang)

    # MCP / gateway / connection
    if any(
        kw in exc_str
        for kw in (
            "mcp",
            "gateway",
            "connection refused",
            "connection reset",
            "connection attempts",
            "connection failed",
        )
    ):
        if "mcp" in exc_str or "gateway" in exc_str:
            return _msg("mcp", lang)
        return _msg("connection", lang)

    # LLM provider errors (litellm wrapping OpenAI/Anthropic/Ollama)
    if any(
        prov in exc_str or prov in exc_type_name
        for prov in ("litellm", "openai", "anthropic", "ollama", "groq", "mistral")
    ):
        return _msg("provider", lang)

    # Fallback → internal
    logger.debug(
        "[ERROR_MSG] Unclassified exception type=%s: %s", exc_type_name, exc_str[:120]
    )
    return _msg("internal", lang)


def format_no_response(lang: str = "ru") -> str:
    """Message for when LLM produced zero output."""
    return _msg("no_response", lang)


def _msg(key: str, lang: str) -> str:
    """Get a message for the given key and language, falling back to the other."""
    langs = ERROR_MESSAGES.get(key, ERROR_MESSAGES["internal"])
    if lang.startswith("ru") and "ru" in langs:
        return langs["ru"]
    return langs.get("en", ERROR_MESSAGES["internal"]["en"])
