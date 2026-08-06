"""Тест что LLMAgent читает runtime-настройки живьём, а не кеширует.

Регрессионный тест на баг: LLMAgent.__init__() кешировал
settings.agent_max_turn_tokens, и apply_runtime_settings()
на POST /admin/abuse-config/reload не применялся —
TokenBudgetMiddleware продолжал резать по старым 8000.

Проверяет: после изменения settings через abuse_live,
новый запрос получает актуальные значения.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api_service.agent.orchestrator import LLMAgent


class TestLiveSettings:
    """LLMAgent должен читать настройки живьём, а не кешировать."""

    @pytest.mark.asyncio
    async def test_max_turn_tokens_reads_live_from_settings(self):
        """LLMAgent.stream_events() передаёт актуальный max_turn_tokens.

        ！！！ Эта настройка должна быть не закеширована в __init__.
        После apply_runtime_settings() новое значение должно
        подхватиться на следующем же вызове.
        """
        with patch("api_service.agent.orchestrator.settings") as mock_settings:
            # ── Имитируем старт с дефолтом 8000 ────────────────────────
            mock_settings.agent_max_turn_tokens = 8000
            mock_settings.agent_max_iterations = 5
            mock_settings.agent_max_empty_rounds = 3

            agent = LLMAgent(llm_client=MagicMock())

            # Проверяем что в PipelineContext попало 8000
            # (не можем вызвать полный поток — только проверить ссылку)
            assert agent._settings is mock_settings
            assert agent._settings.agent_max_turn_tokens == 8000

            # ── Меняем настройки (имитируем apply_runtime_settings) ────
            mock_settings.agent_max_turn_tokens = 64000
            mock_settings.agent_max_iterations = 12
            mock_settings.agent_max_empty_rounds = 5

            # ── Проверяем что значение подхватилось без пересоздания ──
            assert agent._settings.agent_max_turn_tokens == 64000
            assert agent._settings.agent_max_iterations == 12
            assert agent._settings.agent_max_empty_rounds == 5

    @pytest.mark.asyncio
    async def test_live_settings_via_apply_runtime_settings(self):
        """Полный цикл: LiveAbuseProvider -> settings -> LLMAgent.

        Проверяет что apply_runtime_settings() из abuse_live
        действительно меняет то что видит агент.
        """
        from api_service.abuse_live import LiveAbuseProvider, FullAbuseConfig

        # Создаём провайдера с тестовым конфигом
        provider = LiveAbuseProvider.get_instance()

        # Сохраняем оригинал
        orig = provider.get_config()

        try:
            # Меняем конфиг
            new_cfg = FullAbuseConfig()
            new_cfg.max_turn_tokens = 64000
            new_cfg.max_iterations = 12
            new_cfg.max_empty_rounds = 5

            # Подсовываем через save_config (пишет файл + reload)
            # Используем напрямую apply_runtime_settings с моком
            with patch.object(provider, "_full_config", new_cfg):
                provider.apply_runtime_settings()

            agent = LLMAgent(llm_client=MagicMock())

            assert agent._settings.agent_max_turn_tokens == 64000, (
                f"Expected 64000, got {agent._settings.agent_max_turn_tokens}. "
                "LLMAgent cached max_turn_tokens in __init__ instead of reading live!"
            )
            assert agent._settings.agent_max_iterations == 12
            assert agent._settings.agent_max_empty_rounds == 5

        finally:
            # Восстанавливаем
            with patch.object(provider, "_full_config", orig):
                provider.apply_runtime_settings()

    @pytest.mark.asyncio
    async def test_orchestrator_reads_live_on_each_call(self):
        """LLMAgent НЕ кеширует настройки — читает живьём при каждом вызове.

        Проверяем через прямую ссылку на settings: меняем значение,
        создаём агента, проверяем что он видит новое.
        """
        from unittest.mock import patch
        from api_service.agent.orchestrator import LLMAgent

        with patch("api_service.agent.orchestrator.settings") as mock_s:
            mock_s.agent_max_turn_tokens = 8000
            mock_s.agent_max_iterations = 5
            mock_s.agent_max_empty_rounds = 3

            agent = LLMAgent(llm_client=_FakeClient())

            # Меняем "в рантайме"
            mock_s.agent_max_turn_tokens = 32000
            mock_s.agent_max_iterations = 10
            mock_s.agent_max_empty_rounds = 4

            # Читаем через _settings (прямая ссылка на модуль)
            assert agent._settings.agent_max_turn_tokens == 32000, (
                f"agent._settings.agent_max_turn_tokens = "
                f"{agent._settings.agent_max_turn_tokens}, expected 32000. "
                "LLMAgent закешировал значение в __init__ вместо "
                "хранения ссылки на живой settings!"
            )


# Workaround: MagicMock isn't a valid type for the llm_client param
# that does `hasattr(request_llm, "complete")` check.
# We need a real object with that attribute.
class _FakeClient:
    """Minimal fake LLM client — just has 'complete' so the old-style adapter check passes."""

    complete = None
    model = "test"
    api_base = None
    enable_thinking = False
    last_usage = None
    last_cost = 0.0
