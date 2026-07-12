import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api_service.agent.llm_client import LLMClient, create_client


# ── Fixtures ──


@pytest.fixture
def mock_settings():
    """Mock global settings + clean os.environ to avoid real .env interference."""
    with (
        patch("api_service.agent.llm_client.settings") as ms,
        patch.dict(os.environ, {}, clear=True),
    ):
        ms.mistral_api_key = None
        ms.mistral_model = "mistral/mistral-small"
        ms.ollama_model = "qwen2.5:0.5b"
        ms.ollama_url = "http://127.0.0.1:11434"
        ms.agent_temperature = 0.5
        ms.agent_max_tokens_thinking = 4096
        ms.request_timeout = 600.0
        ms.think_mode = True
        yield ms


# ── Tests for create_client ──


def test_create_client_with_ollama_config(mock_settings):
    """create_client with ollama llm_config should produce correct LLMClient."""
    llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:0.5b",
        "api_base": "http://127.0.0.1:11434",
        "temperature": 0.3,
    }
    client = create_client(llm_config)
    assert isinstance(client, LLMClient)
    assert client.model == "ollama_chat/qwen2.5:0.5b"
    assert client.api_base == "http://127.0.0.1:11434"
    assert client.temperature == 0.3


def test_create_client_with_ollama_custom_base(mock_settings):
    """create_client with custom api_base should use it."""
    llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:0.5b",
        "api_base": "http://ollama.internal:11434",
    }
    client = create_client(llm_config)
    assert client.api_base == "http://ollama.internal:11434"


def test_create_client_with_mistral_config(mock_settings):
    """create_client with mistral llm_config should produce correct LLMClient."""
    llm_config = {
        "provider": "mistral",
        "model": "mistral-small",
        "api_key": "sk-test-123",
    }
    client = create_client(llm_config)
    assert isinstance(client, LLMClient)
    assert client.model == "mistral/mistral-small"
    assert client.api_base is None  # LiteLLM handles Mistral's default


def test_create_client_with_openai_config(mock_settings):
    """create_client with openai llm_config should set env var and prefix model."""
    llm_config = {
        "provider": "openai",
        "model": "gpt-4",
        "api_key": "sk-test-openai-456",
    }
    # Clean env before test
    old_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        client = create_client(llm_config)
        assert isinstance(client, LLMClient)
        assert client.model == "openai/gpt-4"
        assert os.environ.get("OPENAI_API_KEY") == "sk-test-openai-456"
    finally:
        # Restore
        if old_key:
            os.environ["OPENAI_API_KEY"] = old_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)


def test_create_client_no_config(mock_settings):
    """create_client without llm_config should fall back to global settings."""
    client = create_client()
    assert isinstance(client, LLMClient)
    assert client.model == "ollama_chat/qwen2.5:0.5b"
    assert client.api_base == "http://127.0.0.1:11434"
    assert client.temperature == 0.5  # from mock_settings defaults


def test_create_client_no_config_fallback_mistral(mock_settings):
    """create_client without llm_config should use Mistral when MISTRAL_API_KEY is set."""
    with patch.dict(
        os.environ,
        {
            "MISTRAL_API_KEY": "sk-mistral-global",
            "MISTRAL_MODEL": "mistral/mistral-small",
        },
    ):
        client = create_client()
        assert client.model == "mistral/mistral-small"
        assert client.api_base is None


def test_create_client_with_anthropic_config(mock_settings):
    """create_client with anthropic llm_config should set env var."""
    llm_config = {
        "provider": "anthropic",
        "model": "claude-3-haiku-20240307",
        "api_key": "sk-ant-test",
    }
    old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        client = create_client(llm_config)
        assert isinstance(client, LLMClient)
        assert client.model == "claude-3-haiku-20240307"
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test"
    finally:
        if old_key:
            os.environ["ANTHROPIC_API_KEY"] = old_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)


def test_create_client_config_model_fallback(mock_settings):
    """create_client should fall back to settings.ollama_model when model is not in config."""
    llm_config = {
        "provider": "ollama",
        # no model key — should fallback to mock_settings.ollama_model
    }
    client = create_client(llm_config)
    assert client.model == "ollama_chat/qwen2.5:0.5b"


def test_create_client_config_max_tokens(mock_settings):
    """create_client should use max_tokens from config."""
    llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:0.5b",
        "max_tokens": 2048,
    }
    client = create_client(llm_config)
    assert client.max_tokens_thinking == 2048
    assert client.model == "ollama_chat/qwen2.5:0.5b"


# ── Existing stream completion test ──


@pytest.mark.asyncio
async def test_llm_client_stream_completion():
    # Mocking LiteLLM
    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion,
        patch("litellm.stream_chunk_builder") as mock_chunk_builder,
    ):
        # Setup mock
        from litellm import CustomStreamWrapper

        mock_response = MagicMock(spec=CustomStreamWrapper)

        # Mocking async iterator
        async def async_iter(*args, **kwargs):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock(content="Hello")
            yield chunk

        mock_response.__aiter__ = async_iter
        mock_acompletion.return_value = mock_response

        # Setup final mock
        from litellm.types.utils import ModelResponse

        final_msg = MagicMock(spec=ModelResponse)
        final_msg.choices = [MagicMock()]
        final_msg.choices[0].message = MagicMock(role="assistant", content="Hello")
        final_msg.choices[0].message.tool_calls = None
        final_msg.choices[0].message.reasoning_content = None
        mock_chunk_builder.return_value = final_msg

        # Execute
        client = LLMClient(model="test-model")
        messages = [{"role": "user", "content": "Hi"}]

        results = []
        async for token, final in client.stream_completion(messages):
            results.append((token, final))

        assert len(results) == 2
        assert results[0] == ("Hello", None)
        assert results[1][0] is None
        assert results[1][1]["content"] == "Hello"
