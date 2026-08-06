"""Tests for Agent API models — WidgetConfig, LLMConfig, Agent CRUD.

Covers:
- WidgetConfig defaults and custom values
- LLMConfig defaults, custom values, and validation
- AgentCreateRequest with/without configs
- AgentResponse with/without configs
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from helperium_sdk.api.models import (
    WidgetConfig,
    LLMConfig,
    AgentCreateRequest,
    AgentUpdateRequest,
    AgentResponse,
    AgentListResponse,
)


# ── WidgetConfig ──


class TestWidgetConfig:
    """WidgetConfig — embed widget display settings."""

    def test_defaults(self):
        """Все поля имеют значения по умолчанию."""
        cfg = WidgetConfig()
        assert cfg.title == "Ассистент"
        assert cfg.greeting == "Чем могу помочь?"
        assert cfg.accent_color == "#0f766e"
        assert cfg.position == "right"

    def test_custom_values(self):
        """Все поля принимают кастомные значения."""
        cfg = WidgetConfig(
            title="Поддержка",
            greeting="Чем помочь?",
            accent_color="#2563eb",
            position="left",
        )
        assert cfg.title == "Поддержка"
        assert cfg.greeting == "Чем помочь?"
        assert cfg.accent_color == "#2563eb"
        assert cfg.position == "left"

        dumped = cfg.model_dump()
        assert dumped["title"] == "Поддержка"
        assert dumped["greeting"] == "Чем помочь?"
        assert dumped["accent_color"] == "#2563eb"
        assert dumped["position"] == "left"

    def test_accepts_right_and_left(self):
        """position принимает 'right' и 'left' (сейчас без enum-валидации)."""
        assert WidgetConfig(position="right").position == "right"
        assert WidgetConfig(position="left").position == "left"

    def test_model_dump_json(self):
        """model_dump(mode='json') сериализуется корректно."""
        cfg = WidgetConfig(title="Test", greeting="Hi")
        dumped = cfg.model_dump(mode="json")
        assert dumped["title"] == "Test"
        assert dumped["greeting"] == "Hi"
        assert dumped["accent_color"] == "#0f766e"
        assert dumped["position"] == "right"


# ── LLMConfig ──


class TestLLMConfig:
    """LLMConfig — per-agent LLM overrides."""

    def test_defaults_all_none(self):
        """Все поля по умолчанию None."""
        cfg = LLMConfig()
        assert cfg.provider is None
        assert cfg.api_key is None
        assert cfg.model is None
        assert cfg.api_base is None
        assert cfg.system_prompt is None
        assert cfg.temperature is None
        assert cfg.max_tokens is None

    def test_custom_values(self):
        """Все поля заполнены кастомными значениями."""
        cfg = LLMConfig(
            provider="mistral",
            api_key="sk-test123",
            model="mistral/mistral-small",
            api_base="https://api.mistral.ai",
            system_prompt="Ты помощник.",
            temperature=0.3,
            max_tokens=4096,
        )
        assert cfg.provider == "mistral"
        assert cfg.api_key == "sk-test123"
        assert cfg.model == "mistral/mistral-small"
        assert cfg.api_base == "https://api.mistral.ai"
        assert cfg.system_prompt == "Ты помощник."
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 4096

        dumped = cfg.model_dump()
        assert dumped["provider"] == "mistral"
        assert dumped["max_tokens"] == 4096

    def test_temperature_out_of_range_high(self):
        """temperature > 2 выбрасывает ValidationError."""
        with pytest.raises(ValidationError):
            LLMConfig(temperature=2.5)

    def test_temperature_out_of_range_low(self):
        """temperature < 0 выбрасывает ValidationError."""
        with pytest.raises(ValidationError):
            LLMConfig(temperature=-0.1)

    def test_temperature_boundary_values(self):
        """temperature на границах 0 и 2 должна проходить."""
        cfg_low = LLMConfig(temperature=0.0)
        assert cfg_low.temperature == 0.0
        cfg_high = LLMConfig(temperature=2.0)
        assert cfg_high.temperature == 2.0

    def test_max_tokens_out_of_range(self):
        """max_tokens < 1 выбра��ывает ValidationError."""
        with pytest.raises(ValidationError):
            LLMConfig(max_tokens=0)

    def test_max_tokens_boundary(self):
        """max_tokens == 1 должен проходить."""
        cfg = LLMConfig(max_tokens=1)
        assert cfg.max_tokens == 1

    def test_partial_config(self):
        """Только некоторые поля заполнены, остальные None."""
        cfg = LLMConfig(provider="ollama", model="qwen2.5:0.5b")
        assert cfg.provider == "ollama"
        assert cfg.model == "qwen2.5:0.5b"
        assert cfg.temperature is None
        assert cfg.api_key is None


# ── AgentCreateRequest ──


class TestAgentCreateRequest:
    """AgentCreateRequest — создание агента."""

    def test_with_all_configs(self):
        """Create request со всеми полями, включая widget_config и llm_config."""
        req = AgentCreateRequest(
            name="support-agent",
            description="Агент поддержки",
            tenant_ids=["tenant-a", "tenant-b"],
            widget_config=WidgetConfig(
                title="Поддержка",
                greeting="Чем помочь?",
                accent_color="#2563eb",
                position="left",
            ),
            llm_config=LLMConfig(
                provider="mistral",
                api_key="sk-test",
                model="mistral/mistral-small",
                temperature=0.3,
            ),
        )
        assert req.name == "support-agent"
        assert req.description == "Агент поддержки"
        assert req.tenant_ids == ["tenant-a", "tenant-b"]
        assert req.widget_config is not None
        assert req.widget_config.title == "Поддержка"
        assert req.llm_config is not None
        assert req.llm_config.provider == "mistral"

        dumped = req.model_dump()
        assert dumped["name"] == "support-agent"
        assert dumped["widget_config"]["accent_color"] == "#2563eb"
        assert dumped["llm_config"]["temperature"] == 0.3

    def test_without_configs(self):
        """Create request только с обязательными полями."""
        req = AgentCreateRequest(name="simple-agent")
        assert req.name == "simple-agent"
        assert req.description == ""
        assert req.tenant_ids == []
        assert req.widget_config is None
        assert req.llm_config is None

    def test_invalid_name_pattern(self):
        """name с пробелами или заглавными — ValidationError из-за pattern."""
        with pytest.raises(ValidationError):
            AgentCreateRequest(name="Bad Agent")
        with pytest.raises(ValidationError):
            AgentCreateRequest(name="CapitalName")
        with pytest.raises(ValidationError):
            AgentCreateRequest(name="")

    def test_valid_name_patterns(self):
        """name с дефисами и подчёрки��аниями."""
        req = AgentCreateRequest(name="my-agent")
        assert req.name == "my-agent"
        req2 = AgentCreateRequest(name="agent_42")
        assert req2.name == "agent_42"

    def test_widget_config_as_dict(self):
        """widget_config можно передать как dict (Pydantic coerce)."""
        req = AgentCreateRequest(
            name="test-agent",
            widget_config={"title": "Dict", "greeting": "Hi"},
        )
        assert req.widget_config is not None
        assert req.widget_config.title == "Dict"
        assert req.widget_config.greeting == "Hi"
        assert req.widget_config.accent_color == "#0f766e"  # default

    def test_llm_config_as_dict(self):
        """llm_config можно передать как dict."""
        req = AgentCreateRequest(
            name="test-agent",
            llm_config={"provider": "ollama", "temperature": 0.7},
        )
        assert req.llm_config is not None
        assert req.llm_config.provider == "ollama"
        assert req.llm_config.temperature == 0.7


# ── AgentUpdateRequest ──


class TestAgentUpdateRequest:
    """AgentUpdateRequest — обновление агента."""

    def test_all_fields_optional(self):
        """Все поля опциональны — можно передать пустой запрос."""
        req = AgentUpdateRequest()
        assert req.description is None
        assert req.tenant_ids is None
        assert req.widget_config is None
        assert req.llm_config is None

    def test_partial_update(self):
        """Можно обновить только description."""
        req = AgentUpdateRequest(description="new desc")
        assert req.description == "new desc"
        assert req.tenant_ids is None
        assert req.widget_config is None

    def test_update_with_configs(self):
        """Обновление с widget_config и llm_config."""
        req = AgentUpdateRequest(
            description="updated",
            widget_config=WidgetConfig(title="New Title"),
            llm_config=LLMConfig(model="new-model"),
        )
        assert req.description == "updated"
        assert req.widget_config.title == "New Title"
        assert req.llm_config.model == "new-model"


# ── AgentResponse ──


class TestAgentResponse:
    """AgentResponse — ответ с данными агента."""

    def test_with_configs(self):
        """Response со всеми полями, включая конфиги."""
        resp = AgentResponse(
            name="support-agent",
            description="Агент поддержки",
            tenant_ids=["tenant-a"],
            widget_config=WidgetConfig(title="Поддержка", accent_color="#2563eb"),
            llm_config=LLMConfig(provider="mistral", temperature=0.3),
            created_at="2026-07-07T12:00:00+00:00",
            updated_at="2026-07-07T12:00:00+00:00",
        )
        assert resp.name == "support-agent"
        assert resp.widget_config.accent_color == "#2563eb"
        assert resp.llm_config.provider == "mistral"
        assert resp.created_at == "2026-07-07T12:00:00+00:00"

        dumped = resp.model_dump()
        assert dumped["widget_config"]["title"] == "Поддержка"
        assert dumped["llm_config"]["temperature"] == 0.3

    def test_without_configs(self):
        """Response без конфигов — widget_config и llm_config Optional."""
        resp = AgentResponse(
            name="simple-agent",
            description="",
            tenant_ids=[],
            created_at="2026-07-07T12:00:00+00:00",
            updated_at="2026-07-07T12:00:00+00:00",
        )
        assert resp.widget_config is None
        assert resp.llm_config is None

    def test_missing_name_raises(self):
        """name обязателен — без него ValidationError."""
        with pytest.raises(ValidationError):
            AgentResponse(
                description="test",
                created_at="2026-07-07T12:00:00+00:00",
                updated_at="2026-07-07T12:00:00+00:00",
            )


# ── AgentListResponse ──


class TestAgentListResponse:
    """AgentListResponse — список агентов."""

    def test_empty_list(self):
        """Пустой список агентов."""
        resp = AgentListResponse(agents=[])
        assert resp.agents == []

    def test_with_agents(self):
        """Список из нескольких агентов."""
        resp = AgentListResponse(
            agents=[
                AgentResponse(
                    name="agent-a",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
                AgentResponse(
                    name="agent-b",
                    widget_config=WidgetConfig(title="B"),
                    created_at="2026-01-02T00:00:00Z",
                    updated_at="2026-01-02T00:00:00Z",
                ),
            ]
        )
        assert len(resp.agents) == 2
        assert resp.agents[0].name == "agent-a"
        assert resp.agents[0].widget_config is None
        assert resp.agents[1].widget_config.title == "B"
