"""TDD tests: classify_error() substring matching misclassifies errors.

Проблема: `services/api-service/src/api_service/error_messages.py`
использует `if 'auth' in exc_str` — это ловит 'database_auth_error' как
AUTH, хотя это CONNECTION/MCP ошибка. Аналогично с другими ключевыми
словами.

Тесты ДОКАЗЫВАЮТ misclassification и ПАДАЮТ пока баг не исправлен.
"""

from __future__ import annotations


from api_service.error_messages import classify_error


# ── Кастомные исключения для тестов ────────────────────────────────────


class DatabaseAuthError(ConnectionError):
    """Ошибка аутентификации БД — ConnectionError, не AuthError."""

    def __init__(self):
        super().__init__("database authentication failed: invalid credentials for host")


class TokenizerLimitError(RuntimeError):
    """Токенизатор превысил лимит — внутренняя ошибка, не context_length."""

    def __init__(self):
        super().__init__("tokenizer exceeded maximum sequence length")


class MistralMCPError(Exception):
    """Ошибка Mistral API с упоминанием MCP в описании — PROVDER, не MCP."""

    def __init__(self):
        super().__init__("litellm.MistralError: model_context_protocol not supported")


class FileUploadAuthError(PermissionError):
    """Ошибка прав доступа к файлу — PermissionError, не Auth."""

    def __init__(self):
        super().__init__("file authorization denied for upload path")


class PipelineTokenMismatchError(ValueError):
    """Ошибка несовпадения токенов в pipeline — internal, не context_length."""

    def __init__(self):
        super().__init__("token bucket: request token mismatched session token")


class ConnectionRefusedMCPError(ConnectionError):
    """Connection refused K mcp-gateway — CONNECTION, не MCP."""

    def __init__(self):
        super().__init__("Connection refused: mcp-gateway:8083")


class TestClassifyErrorAuthMisclassification:
    """classify_error НЕПРАВИЛЬНО классифицирует ошибки с 'auth' в тексте."""

    def test_database_auth_misclassified_as_auth(self):
        """'database authentication failed' классифицируется как AUTH (неверно).

        Правильная категория: CONNECTION (БД не смогла подключиться).
        Substring 'auth' в 'authentication' — ложное срабатывание.
        """
        exc = DatabaseAuthError()
        result = classify_error(exc, "ru")

        # Доказываем: сейчас она классифицируется как auth
        # А должна быть connection или mcp
        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        # ⚡ TDD: тест падает потому что classify_error возвращает
        # сообщение из категории AUTH, а должно возвращать CONNECTION
        auth_msg = (  # noqa: F841
            "Ошибка доступа к модели. Попробуйте позже или обратитесь к администратору."
        )
        connection_msg = "Не удалось подключиться к серверу данных. Попробуйте позже."
        mcp_msg = "Не удалось выполнить запрос к базе данных. Попробуйте позже."

        assert result in (connection_msg, mcp_msg), (
            f"\n\n❌ TDD FAIL: DatabaseAuthError классифицировано как AUTH, "
            f"хотя это CONNECTION/MCP ошибка.\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{connection_msg}' или '{mcp_msg}'\n"
            f"Причина: 'auth' in '{str(exc).lower()}' = True"
        )

    def test_file_auth_misclassified_as_auth(self):
        """'file authorization denied' классифицируется как AUTH (неверно).

        Правильная категория: INTERNAL (внутренняя ошибка прав доступа).
        """
        exc = FileUploadAuthError()
        result = classify_error(exc, "en")

        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        auth_msg = (  # noqa: F841
            "Model access error. Please try again later or contact the administrator."
        )
        internal_msg = "Sorry, an internal error occurred. Please try again."

        assert result == internal_msg, (
            f"\n\n❌ TDD FAIL: FileUploadAuthError классифицировано как AUTH, "
            f"хотя это INTERNAL.\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{internal_msg}'"
        )


class TestClassifyErrorTokenMisclassification:
    """classify_error НЕПРАВИЛЬНО классифицирует ошибки с 'token' в тексте."""

    def test_tokenizer_limit_misclassified_as_context_length(self):
        """'tokenizer exceeded maximum length' → CONTEXT_LENGTH (неверно).

        Правильная категория: INTERNAL (упал токенизатор, а не контекст).
        """
        exc = TokenizerLimitError()
        result = classify_error(exc, "en")

        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        context_msg = "The conversation is too long. Please start a new chat."  # noqa: F841
        internal_msg = "Sorry, an internal error occurred. Please try again."

        assert result == internal_msg, (
            f"\n\n❌ TDD FAIL: TokenizerLimitError классифицировано как CONTEXT_LENGTH, "
            f"хотя это INTERNAL (токенизатор, не контекст).\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{internal_msg}'\n"
            f"Причина: 'token'/'exceed'/'limit'/'length' в тексте = ложное срабатывание"
        )

    def test_token_mismatch_misclassified_as_context_length(self):
        """'token bucket: request token mismatched' → CONTEXT_LENGTH (неверно).

        Правильная категория: INTERNAL (pipeline, не контекст).
        """
        exc = PipelineTokenMismatchError()
        result = classify_error(exc, "en")

        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        context_msg = "The conversation is too long. Please start a new chat."  # noqa: F841
        internal_msg = "Sorry, an internal error occurred. Please try again."

        assert result == internal_msg, (
            f"\n\n❌ TDD FAIL: PipelineTokenMismatchError классифицировано как "
            f"CONTEXT_LENGTH, хотя это INTERNAL.\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{internal_msg}'"
        )


class TestClassifyErrorProviderPriority:
    """classify_error должен проверять provider ДО substring matching."""

    def test_litellm_error_with_mcp_in_message(self):
        """Ошибка LiteLLM c 'mcp' в сообщении → PROVIDER, не MCP.

        litellm провайдер должен проверяться ДО substring 'mcp'.
        """
        exc = MistralMCPError()
        result = classify_error(exc, "ru")

        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        mcp_msg = "Не удалось выполнить запрос к базе данных. Попробуйте позже."  # noqa: F841
        provider_msg = "Ошибка при обработке запроса моделью. Попробуйте позже."

        assert result == provider_msg, (
            f"\n\n❌ TDD FAIL: MistralMCPError классифицировано как MCP, "
            f"хотя это PROVDER (LiteLLM+Mistral).\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{provider_msg}'\n"
            f"Причина: 'mcp' в тексте совпало раньше проверки провайдера"
        )

    def test_connection_refused_mcp_not_misclassified(self):
        """Connection refused к mcp-gateway → CONNECTION, не MCP (правильно).

        Этот тест проверяет что CONNECTION проверяется раньше MCP
        в цепочке — 'connection refused' должен быть пойман как
        CONNECTION, а 'mcp' во вторую очередь.
        """
        exc = ConnectionRefusedMCPError()
        result = classify_error(exc, "en")

        print(f"\nИсключение: {exc}")
        print(f"classify_error вернула: {result}")

        connection_msg = "Failed to connect to the data server. Please try again later."
        mcp_msg = "Failed to query the database. Please try again later."  # noqa: F841

        assert result == connection_msg, (
            f"\n\n❌ TDD FAIL: ConnectionRefused к mcp-gateway классифицировано "
            f"как MCP, хотя это CONNECTION.\n"
            f"Получено: '{result}'\n"
            f"Ожидалось: '{connection_msg}'"
        )
