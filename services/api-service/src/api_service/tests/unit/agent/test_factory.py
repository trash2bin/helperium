"""Tests for LLM provider factory (resolve_llm)."""

from api_service.agent.factory import (
    _prefix_model,
    _KNOWN_PREFIXES,
)


class TestPrefixModel:
    """Tests for _prefix_model helper."""

    def test_ollama_with_api_base(self):
        result = _prefix_model("ollama", "llama3", "http://localhost:11434")
        assert result == "ollama_chat/llama3"

    def test_ollama_without_api_base(self):
        result = _prefix_model("ollama", "llama3", None)
        assert result == "llama3"

    def test_ollama_canonical_prefix_is_not_doubled(self):
        result = _prefix_model(
            "ollama", "ollama/minimax-m3:cloud", "http://127.0.0.1:11434"
        )
        assert result == "ollama/minimax-m3:cloud"

    def test_mistral(self):
        result = _prefix_model("mistral", "mistral-large", None)
        assert result == "mistral/mistral-large"

    def test_mistral_already_prefixed(self):
        result = _prefix_model("mistral", "mistral/mistral-large", None)
        assert result == "mistral/mistral-large"

    def test_openai(self):
        result = _prefix_model("openai", "gpt-4o", None)
        assert result == "openai/gpt-4o"

    def test_deepseek_gets_prefix(self):
        result = _prefix_model("deepseek", "deepseek-chat", None)
        assert result == "deepseek/deepseek-chat"

    def test_deepseek_already_prefixed(self):
        result = _prefix_model("deepseek", "deepseek/deepseek-chat", None)
        assert result == "deepseek/deepseek-chat"

    def test_none_provider(self):
        result = _prefix_model(None, "llama3", None)
        assert result == "llama3"


class TestOllamaPrefixes:
    """Tests for _KNOWN_PREFIXES constant."""

    def test_ollama_prefixes_contain_common(self):
        assert "ollama_chat/" in _KNOWN_PREFIXES
        assert "openai/" in _KNOWN_PREFIXES
        assert "anthropic/" in _KNOWN_PREFIXES
