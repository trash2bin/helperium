"""TDD tests: ErrorContext не используется всеми stage'ами.

Проблема: ErrorContext внедрён только в LLMStage._call_llm() и
ToolExecutionStage.run(). GuardInputStage, FallbackStage, SaveHistoryStage
НЕ вызывают ctx.error_context.with_stage() при логировании ошибок.

Дополнительно: ToolExecutionStage вызывает with_stage(), но НЕ
присваивает результат обратно в ctx.error_context — баг в существующем коде.

Тесты ПАДАЮТ пока все stage'и не начнут использовать ErrorContext.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api_service.agent.error_context import ErrorContext
from api_service.agent.pipeline import PipelineContext
from api_service.agent.stages import (
    FallbackStage,
    GuardInputStage,
    SaveHistoryStage,
    ToolExecutionStage,
)


def _make_minimal_ctx() -> PipelineContext:
    """Create a minimal PipelineContext for testing ErrorContext coverage."""
    turn = AsyncMock()
    turn.messages = [{"role": "system", "content": "test"}]
    turn.turn_messages = [{"role": "user", "content": "hello"}]
    turn.tools = []
    turn.pending_calls = []
    turn.tool_results = []
    turn.final_content = ""
    turn.session_id = "test-session"
    turn.turn_id = "test-turn"
    turn.tenant_ids = None
    turn.empty_rounds = 0
    turn.iteration = 0

    llm = AsyncMock()
    llm.model = "test-model"
    llm.api_base = "http://test"
    llm.enable_thinking = False
    llm.complete.return_value = AsyncMock()
    llm.complete.return_value.content = ""

    mcp = AsyncMock()
    mcp.list_tools.return_value = []
    mcp.call_tool.return_value = AsyncMock()
    mcp.call_tool.return_value.tool_content = "ok"
    mcp.call_tool.return_value.ok = True
    mcp.call_tool.return_value.reminder = ""

    store = AsyncMock()
    spending = AsyncMock()
    backlog = AsyncMock()

    ctx = PipelineContext(
        turn=turn,
        llm_provider=llm,
        mcp_session=mcp,
        store=store,
        spending=spending,
        backlog=backlog,
        error_context=ErrorContext(
            session_id="test-session",
            correlation_id="test-correlation",
        ),
    )
    return ctx


class TestErrorContextGuardInputStage:
    """GuardInputStage должен устанавливать error_context.stage."""

    @pytest.mark.asyncio
    async def test_guard_input_stage_sets_error_context(self):
        """После GuardInputStage.run() error_context.stage должен быть 'guard_input'.

        Сейчас (баг): GuardInputStage не вызывает ctx.error_context.with_stage()
        при логировании предупреждения о блокировке сообщения.
        """
        from unittest.mock import MagicMock

        ctx = _make_minimal_ctx()
        ctx.guard_checker = MagicMock()
        ctx.guard_checker.check_input.return_value.blocked = True
        ctx.guard_checker.check_input.return_value.reason = "inject_test"
        ctx.should_stop = False

        # Прогоняем GuardInputStage (one-shot — сработает один раз)
        stage = GuardInputStage()
        async for _ in stage.run(ctx):
            pass

        # ⚡ TDD: error_context.stage должен быть guard_input
        assert ctx.error_context.stage == "guard_input", (
            f"\n\n❌ TDD FAIL: GuardInputStage не установил error_context.stage.\n"
            f"Текущее значение: '{ctx.error_context.stage}'\n"
            f"Ожидаемое значение: 'guard_input'\n"
            f"Сейчас GuardInputStage логирует блокировку через logger.warning() "
            f"без ctx.error_context.with_stage() — structured logging теряет "
            f"информацию о stage."
        )


class TestErrorContextFallbackStage:
    """FallbackStage должен устанавливать error_context.stage."""

    @pytest.mark.asyncio
    async def test_fallback_stage_sets_error_context(self):
        """После FallbackStage.run() error_context.stage должен быть 'fallback'.

        Сейчас (баг): FallbackStage не вызывает ctx.error_context.with_stage()
        при логировании ошибки LLM вызова.
        """
        ctx = _make_minimal_ctx()
        ctx.should_stop = True
        ctx._mark_done("fallback")  # Пропускаем gating, симулируем что run уже был
        ctx._done_flags.clear()  # Сбрасываем для повторного прогона
        ctx.turn.final_content = ""  # Нет финала — fallback сработает

        # Мокаем LLM чтобы упал
        ctx.llm_provider.complete.side_effect = RuntimeError("Fallback LLM crashed")

        stage = FallbackStage()
        async for _ in stage.run(ctx):
            pass

        # ⚡ TDD: error_context.stage должен быть fallback
        assert ctx.error_context.stage == "fallback", (
            f"\n\n❌ TDD FAIL: FallbackStage не установил error_context.stage.\n"
            f"Текущее значение: '{ctx.error_context.stage}'\n"
            f"Ожидаемое значение: 'fallback'\n"
            f"FallbackStage логирует исключение 'LLM call failed' "
            f"без ctx.error_context.with_stage()."
        )


class TestErrorContextSaveHistoryStage:
    """SaveHistoryStage должен устанавливать error_context.stage."""

    @pytest.mark.asyncio
    async def test_save_history_stage_sets_error_context(self):
        """После SaveHistoryStage.run() error_context.stage должен быть 'save_history'.

        Сейчас (баг): SaveHistoryStage не вызывает ctx.error_context.with_stage()
        при логировании ошибки сохранения.
        """
        ctx = _make_minimal_ctx()
        ctx.should_stop = True
        ctx.turn.final_content = "some content"
        ctx.turn.turn_messages = [{"role": "user", "content": "hello"}]

        # Мокаем store чтобы упал
        ctx.store.aremember_turn.side_effect = RuntimeError("DB write failed")

        stage = SaveHistoryStage()
        async for _ in stage.run(ctx):
            pass

        # ⚡ TDD: error_context.stage должен быть save_history
        assert ctx.error_context.stage == "save_history", (
            f"\n\n❌ TDD FAIL: SaveHistoryStage не установил error_context.stage.\n"
            f"Текущее значение: '{ctx.error_context.stage}'\n"
            f"Ожидаемое значение: 'save_history'\n"
            f"SaveHistoryStage логирует исключение 'Failed to save' "
            f"без ctx.error_context.with_stage()."
        )


class TestErrorContextToolExecutionStage:
    """ToolExecutionStage должен устанавливать error_context.stage КОРРЕКТНО.

    Дополнительный баг: ToolExecutionStage вызывает with_stage(), но
    НЕ ПРИСВАИВАЕТ результат обратно в ctx.error_context.
    """

    @pytest.mark.asyncio
    async def test_tool_execution_stage_mutates_error_context(self):
        """ToolExecutionStage должен МУТИРОВАТЬ ctx.error_context, а не создавать копию.

        Сейчас (баг): код делает:
          err_ctx = ctx.error_context.with_stage("tool_execution")
          # err_ctx используется для логирования, но ctx.error_context НЕ МЕНЯЕТСЯ

        with_stage() возвращает НОВЫЙ объект ErrorContext (immutable-style builder).
        Результат не присваивается обратно.
        """
        ctx = _make_minimal_ctx()
        # Мокаем call_tool чтобы выбросил исключение — только так
        # ToolExecutionStage попадает в except блок с with_stage()
        ctx.mcp_session.call_tool.side_effect = RuntimeError("Tool execution failed")
        ctx.turn.pending_calls = [
            {
                "name": "test_tool",
                "id": "call_1",
                "arguments": {},
                "function": {"name": "test_tool", "arguments": "{}"},
            }
        ]

        stage = ToolExecutionStage()
        async for event in stage.run(ctx):
            pass

        # ⚡ TDD: ctx.error_context.stage должен стать 'tool_execution'
        assert ctx.error_context.stage == "tool_execution", (
            f"\n\n❌ TDD FAIL: ToolExecutionStage не мутировал ctx.error_context.\n"
            f"Текущее значение: '{ctx.error_context.stage}'\n"
            f"Ожидаемое значение: 'tool_execution'\n"
            f"with_stage() возвращает НОВЫЙ объект, но результат не присвоен "
            f"обратно в ctx.error_context."
        )


class TestAllMissingStages:
    """Агрегированный тест: все stage'и должны установить error_context."""

    @pytest.mark.asyncio
    async def test_all_missing_stages_identified(self):
        """Проверяем что GuardInput, Fallback, SaveHistory — все отсутствуют.

        Этот тест собирает все не-установленные stage для одного отчёта.
        """
        from unittest.mock import MagicMock

        missing = []

        # GuardInput
        ctx = _make_minimal_ctx()
        ctx.guard_checker = MagicMock()
        ctx.guard_checker.check_input.return_value.blocked = True
        ctx.guard_checker.check_input.return_value.reason = "inject_test"
        ctx.should_stop = False
        stage = GuardInputStage()
        async for _ in stage.run(ctx):
            pass
        if ctx.error_context.stage != "guard_input":
            missing.append("GuardInputStage")

        # Fallback
        ctx2 = _make_minimal_ctx()
        ctx2.should_stop = True
        ctx2.turn.final_content = ""
        ctx2.llm_provider.complete.side_effect = RuntimeError("fail")
        stage2 = FallbackStage()
        async for _ in stage2.run(ctx2):
            pass
        if ctx2.error_context.stage != "fallback":
            missing.append("FallbackStage")

        # SaveHistory
        ctx3 = _make_minimal_ctx()
        ctx3.should_stop = True
        ctx3.turn.final_content = "content"
        ctx3.turn.turn_messages = [{"role": "user", "content": "hi"}]
        ctx3.store.aremember_turn.side_effect = RuntimeError("fail")
        stage3 = SaveHistoryStage()
        async for _ in stage3.run(ctx3):
            pass
        if ctx3.error_context.stage != "save_history":
            missing.append("SaveHistoryStage")

        assert not missing, (
            f"\n\n❌ TDD FAIL: Следующие stage'и НЕ используют ErrorContext: {missing}\n"
            f"Каждый stage должен вызывать:\n"
            f"  ctx.error_context = ctx.error_context.with_stage('stage_name')\n"
            f"перед logger.warning/error, чтобы structured logging содержал stage."
        )
