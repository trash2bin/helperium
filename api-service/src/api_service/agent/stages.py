"""Stage'ы для Pipeline — каждый этап обработки запроса.

Каждый Stage — async generator, реализующий Stage протокол из pipeline.py.

Порядок stage'ов в pipeline:
1. GuardInputStage        — одноразово: проверка prompt injection на входе
2. ToolDiscoveryStage     — одноразово: открыть MCP session, list_tools, schema
3. LLMStage               — вызвать LLM, стримить токены, определить outcome
4. ToolExecutionStage     — выполнить tool calls
5. GuardOutputStage       — одноразово: проверить финальный ответ на утечки
6. FallbackStage          — одноразово: если финала нет — fallback
7. SaveHistoryStage       — одноразово: сохранить turn в conversation store
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .models import CompletionRequest
from .pipeline import PipelineContext
from .prompts import FALLBACK_GENERIC
from .token_estimator import trim_for_fallback
from .types import (
    AgentEvent,
    ErrorEventData,
    FinalEventData,
    StatusEventData,
    ToolCallEventData,
    ToolResultEventData,
)

# Singleton-free: stages access backlog and guard checker through PipelineContext

logger = logging.getLogger("api_service.agent.stages")


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Guard Input
# ═══════════════════════════════════════════════════════════════════════════════


class GuardInputStage:
    """Проверка входящего сообщения на prompt injection.

    Выполняется один раз в начале pipeline.
    При блокировке выставляет ``ctx.should_stop = True``.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("guard_input"):
            return
        ctx._mark_done("guard_input")

        user_message = (
            ctx.turn.turn_messages[0].get("content", "")
            if ctx.turn.turn_messages
            else ""
        )

        guard_reason = ""
        if ctx.guard_checker is not None:
            guard_result = ctx.guard_checker.check_input(user_message)
            if guard_result.blocked:
                guard_reason = guard_result.reason
        if guard_reason:
            logger.warning("[GUARD] Blocked message: %s", guard_reason)
            ctx.backlog.error(ctx.turn.session_id, ctx.turn.turn_id, 0, guard_reason)
            ctx.should_stop = True
            yield AgentEvent(
                "error",
                ErrorEventData(
                    message="Ваше сообщение заблокировано системой безопасности."
                ),
            )
            return

        logger.debug("[GUARD] Input passed: clean")
        return


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Tool Discovery
# ═══════════════════════════════════════════════════════════════════════════════


class ToolDiscoveryStage:
    """Открыть MCP session, получить список инструментов и схему БД.

    Выполняется один раз (gate через _done_flags).
    Результат сохраняется в ctx.turn.tools.
    """

    def __init__(self) -> None:
        self._schema_cache: dict[str, str] = {}

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("tool_discovery"):
            return
        ctx._mark_done("tool_discovery")

        # 1. List tools
        tools = await ctx.mcp_session.list_tools()
        ctx.turn.tools = tools
        logger.info(
            "[TOOL_DISCOVERY] Available tools: %s",
            [t.get("function", {}).get("name") for t in tools],
        )

        # 2. Get schema (LLM-friendly)
        try:
            schema = await ctx.mcp_session.get_schema()
            if schema and schema.get("entities"):
                cache_key = "-".join(ctx.turn.tenant_ids or ["default"])
                if cache_key not in self._schema_cache:
                    self._schema_cache[cache_key] = _build_schema_message(schema)
                schema_note = self._schema_cache[cache_key]
                ctx.turn.messages.append({"role": "system", "content": schema_note})
                logger.info(
                    "[TOOL_DISCOVERY] Injected schema with %d entities and %d hints",
                    len(schema["entities"]),
                    len(schema.get("workflow_hints", [])),
                )
        except Exception:
            logger.warning("[TOOL_DISCOVERY] Failed to get schema", exc_info=True)

        return


def _build_schema_message(schema: dict) -> str:
    """Build a system-prompt block from the LLM-friendly schema.

    Compact format — no raw SQL, only semantic information the LLM
    needs to make effective tool calls.
    """
    lines = ["=== СТРУКТУРА ДАННЫХ (автоматически загружена из БД) ==="]

    for ent in schema.get("entities", []):
        name = ent.get("name", "?")
        lines.append(f"\n📦 {name}")

        desc = ent.get("description", "")
        if desc:
            lines.append(f"   Описание: {desc}")

        sf = ent.get("search_fields", "")
        if sf:
            lines.append(f"   Поиск: по полю '{sf}' (ILIKE, нечёткий)")

        for fg in ent.get("filter_fields", []):
            label = fg.get("label", "")
            fields = fg.get("fields", [])
            if not fields:
                continue
            parts = []
            for f in fields:
                desc = f.get("description", "")
                col = f.get("column", f.get("name", "?"))
                if desc:
                    parts.append(f"{col} ({desc})")
                else:
                    parts.append(col)
            lines.append(f"   Фильтр ({label}): {', '.join(parts)}")

        for rel in ent.get("relations", []):
            field = rel.get("field", "?")
            ref = rel.get("referenced_entity", "?")
            lines.append(f"   Связь: {field} → {ref}")

    hints = schema.get("workflow_hints", [])
    if hints:
        lines.append("\n📌 Стратегические подсказки:")
        for h in hints:
            lines.append(f"   • {h}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3 — LLM Stage
# ═══════════════════════════════════════════════════════════════════════════════


class LLMStage:
    """Вызов LLM: стриминг + определение outcome.

    Исправляет проблемы текущей реализации:
    - НЕ засовывает reasoning_content в messages
    - НЕ вставляет PARTIAL_REMINDER
    - Возвращает outcome через CompletionResponse, а не мутабельные поля

    Outcome определяется по CompletionResponse:
    - tool_calls → pending_calls заполняется, LLMStage заканчивается
    - content → final_content, yield AgentEvent("final", …), return
    - reasoning_content → empty_rounds += 1, continue
    - пусто → empty_rounds += 1, continue
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # LLMStage повторяется на каждой итерации — никакого gating

        if ctx.should_stop:
            return

        req = CompletionRequest(
            messages=ctx.turn.messages,
            tools=ctx.turn.tools if ctx.turn.tools else None,
            stream=True,
            tenant_ids=ctx.turn.tenant_ids,
        )

        _start = time.monotonic()
        response = await ctx.llm_provider.complete(req)
        _duration_ms = (time.monotonic() - _start) * 1000

        ctx.last_response = response

        # 📊 Backlog: LLM call
        model = getattr(ctx.llm_provider, "model", "unknown")
        usage = response.usage
        ctx.backlog.record_llm_call(
            session_id=ctx.turn.session_id,
            model=model,
            provider=model.split("/")[0] if "/" in model else "unknown",
            duration_ms=_duration_ms,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            cost=response.cost,
            status="success",
            tenant_ids=ctx.turn.tenant_ids or [],
            turn_id=ctx.turn.turn_id,
            iteration=ctx.turn.iteration,
        )

        # ── Стримим токены ────────────────────────────────────────────
        for token in response.content_tokens:
            yield AgentEvent("token", {"data": token})

        # ── Определяем outcome ────────────────────────────────────────
        if response.tool_calls:
            ctx.turn.pending_calls = response.tool_calls
            yield AgentEvent(
                "status",
                StatusEventData(
                    phase="tool_calls",
                    iteration=ctx.turn.iteration,
                ),
            )
            # Append assistant message with tool_calls to history
            formatted_tc = _format_tool_calls_for_message(response.tool_calls)
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
            }
            if formatted_tc:
                assistant_msg["tool_calls"] = formatted_tc
            ctx.turn.messages.append(assistant_msg)
            ctx.turn.turn_messages.append(assistant_msg)
            return  # let ToolExecutionStage handle calls

        elif response.content:
            ctx.turn.final_content = response.content
            assistant_msg = {
                "role": "assistant",
                "content": response.content,
            }
            ctx.turn.messages.append(assistant_msg)
            ctx.turn.turn_messages.append(assistant_msg)
            yield AgentEvent("final", FinalEventData(content=response.content))
            ctx.should_stop = True
            return

        elif response.reasoning_content:
            # NOT adding to messages — фикс проблемы "модели пишут мысли"
            logger.debug(
                "[LLM_STAGE] Reasoning-only response (iteration %d)",
                ctx.turn.iteration,
            )
            ctx.turn.empty_rounds += 1
            yield AgentEvent(
                "status",
                StatusEventData(
                    phase="empty_round",
                    iteration=ctx.turn.iteration,
                    empty_rounds=ctx.turn.empty_rounds,
                ),
            )
            return

        else:
            # Empty response
            logger.warning(
                "[LLM_STAGE] Empty response (iteration %d)", ctx.turn.iteration
            )
            ctx.turn.empty_rounds += 1
            yield AgentEvent(
                "status",
                StatusEventData(
                    phase="empty_round",
                    iteration=ctx.turn.iteration,
                    empty_rounds=ctx.turn.empty_rounds,
                ),
            )
            return


def _format_tool_calls_for_message(tool_calls: list[dict]) -> list[dict]:
    """Format tool_calls from LLM response into message-compatible format."""
    result = []
    for tc in tool_calls:
        result.append(
            {
                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": tc.get("name", tc.get("function", {}).get("name", "")),
                    "arguments": json.dumps(
                        tc.get(
                            "arguments", tc.get("function", {}).get("arguments", {})
                        ),
                        ensure_ascii=False,
                    ),
                },
            }
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 4 — Tool Execution
# ═══════════════════════════════════════════════════════════════════════════════


class ToolExecutionStage:
    """Выполнить tool calls, вернуть результаты.

    Берёт pending_calls из ctx.turn.pending_calls, выполняет каждый
    через ctx.mcp_session.call_tool(), сохраняет результаты в
    ctx.turn.messages как role="tool" и в ctx.turn.tool_results.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # ToolExecutionStage срабатывает только когда есть pending_calls
        if not ctx.turn.pending_calls:
            return

        # Pre-resolve display names
        display_names: dict[str, str] = {}
        for tc in ctx.turn.pending_calls:
            n = tc.get("name", "")
            if n and n not in display_names:
                try:
                    mcp_client = getattr(ctx.mcp_session, "client", None)
                    if mcp_client is not None and hasattr(
                        mcp_client, "get_display_name"
                    ):
                        dn = await mcp_client.get_display_name(ctx.turn.tenant_ids, n)
                        display_names[n] = (
                            n if not isinstance(dn, str) or not dn else dn
                        )
                    else:
                        display_names[n] = n
                except Exception:
                    display_names[n] = n

        for tool_call in ctx.turn.pending_calls:
            name: str = tool_call.get("name", "")
            arguments: dict[str, Any] = tool_call.get("arguments", {})
            tool_call_id: str = (
                tool_call.get("id", "") or f"call_{name}_{uuid.uuid4().hex[:8]}"
            )
            display_name = display_names.get(name, name)

            # 📊 Backlog: tool call
            ctx.backlog.tool_call(
                ctx.turn.session_id,
                ctx.turn.turn_id,
                ctx.turn.iteration,
                name,
                arguments,
            )
            yield AgentEvent(
                "tool_call",
                ToolCallEventData(
                    id=tool_call_id,
                    name=name,
                    display_name=display_name,
                    arguments=arguments,
                ),
            )

            logger.info(
                "[TOOL_STAGE] Executing tool %s for iteration=%d with args=%s",
                name,
                ctx.turn.iteration,
                arguments,
            )

            # Execute
            try:
                tool_result = await ctx.mcp_session.call_tool(name, arguments)
                logger.info(
                    "[TOOL_STAGE] Tool %s OK=%s, ContentLength=%d, Iteration=%d, Args=%s",
                    name,
                    tool_result.ok,
                    len(tool_result.tool_content),
                    ctx.turn.iteration,
                    arguments,
                )
            except Exception as exc:
                logger.exception("[TOOL_STAGE] Tool call '%s' failed", name)
                logger.info(
                    "[TOOL_STAGE] Tool %s FAILED, Iteration=%d, Args=%s, Error=%s",
                    name,
                    ctx.turn.iteration,
                    arguments,
                    str(exc),
                )
                from .mcp_client import ToolResult

                tool_result = ToolResult(
                    tool_content=json.dumps(
                        {"error": True, "message": str(exc)},
                        ensure_ascii=False,
                    ),
                    reminder=(
                        f"Инструмент '{name}' завершился ошибкой: {exc}. "
                        "Попробуй другой инструмент или ответь пользователю."
                    ),
                    ok=False,
                    error=str(exc),
                )

            # Truncate for backlog
            tool_content = tool_result.tool_content
            if len(tool_content) > 10_000:
                tool_content_short = (
                    tool_content[:10_000]
                    + f"\n...(truncated, {len(tool_result.tool_content)} chars)"
                )
            else:
                tool_content_short = tool_content

            ctx.backlog.tool_result(
                ctx.turn.session_id,
                ctx.turn.turn_id,
                ctx.turn.iteration,
                name,
                tool_content_short,
                duration_ms=0,
            )

            result_payload: dict[str, Any] = {
                "id": tool_call_id,
                "name": name,
                "display_name": display_name,
                "result": tool_result.tool_content,
            }
            if not tool_result.ok:
                result_payload["isError"] = True
            yield AgentEvent("tool_result", ToolResultEventData(**result_payload))

            # Store result in tool_results
            ctx.turn.tool_results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "result": tool_result.tool_content,
                }
            )

            # Append role="tool" to messages
            # LLM-friendly content: for errors, use clear text with [TOOL_ERROR] prefix
            # so the model knows this is a failed invocation, not data
            llm_content = tool_result.tool_content
            if not tool_result.ok:
                # Extract error message from JSON if possible
                try:
                    err_parts = json.loads(tool_result.tool_content)
                    err_msg = err_parts.get("error", tool_result.tool_content)
                except (json.JSONDecodeError, TypeError):
                    err_msg = tool_result.tool_content
                llm_content = f"[TOOL_ERROR] Tool '{name}' returned an error: {err_msg}. Do NOT repeat the same call with the same arguments."

            ctx.turn.messages.append(
                {
                    "role": "tool",
                    "content": llm_content,
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )
            ctx.turn.turn_messages.append(
                {
                    "role": "tool",
                    "content": llm_content,
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )

        # Clear pending calls
        ctx.turn.pending_calls = []


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 5 — Guard Output
# ═══════════════════════════════════════════════════════════════════════════════


class GuardOutputStage:
    """Проверка финального ответа на утечку system prompt или credentials.

    Выполняется один раз — когда появляется final_content.
    Gate через _done_flags: не помечается done, пока не проверил реальный контент.
    Если ответ заблокирован — заменяет содержимое.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("guard_output"):
            return

        if not ctx.turn.final_content:
            return  # ждём пока появится контент

        # Теперь есть что проверять — маркируем done
        ctx._mark_done("guard_output")

        guard_reason = ""
        if ctx.guard_checker is not None:
            output_check = ctx.guard_checker.check_output(ctx.turn.final_content)
            if output_check.blocked:
                guard_reason = output_check.reason
        if guard_reason:
            logger.warning(
                "[GUARD] Blocked output: %s (session %s)",
                guard_reason,
                ctx.turn.session_id,
            )
            # Заменить последнее assistant сообщение
            blocked_text = "[Ответ заблокирован системой безопасности]"
            for msg in reversed(ctx.turn.messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = blocked_text
                    break
            ctx.turn.final_content = blocked_text
            # Тоже правим turn_messages
            for msg in reversed(ctx.turn.turn_messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = blocked_text
                    break

        return


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 6 — Fallback
# ═══════════════════════════════════════════════════════════════════════════════


class FallbackStage:
    """Fallback — если после всех итераций pipeline не дал финала.

    Выполняется один раз, только когда ``ctx.should_stop == True`` и
    ``ctx.turn.final_content`` ещё пуст. Триммит историю до
    system + последние 2 exchange, вызывает LLM, стримит ответ.

    Использует llm_provider.complete() и итерирует content_tokens как стрим.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # Fallback — только когда pipeline остановлен
        if not ctx.should_stop:
            return

        if ctx._stage_ran("fallback"):
            return
        ctx._mark_done("fallback")

        # Если финал уже есть — не нужен fallback
        if ctx.turn.final_content:
            return

        fallback_messages = trim_for_fallback(ctx.turn.messages)
        logger.info(
            "[FALLBACK] Trimming %d messages to %d for fallback",
            len(ctx.turn.messages),
            len(fallback_messages),
        )

        req = CompletionRequest(
            messages=fallback_messages,
            stream=True,
            tenant_ids=ctx.turn.tenant_ids,
        )

        try:
            response = await ctx.llm_provider.complete(req)
        except Exception:
            logger.exception("[FALLBACK] LLM call failed")
            # Генерический ответ
            yield AgentEvent("token", {"data": FALLBACK_GENERIC})
            ctx.turn.final_content = FALLBACK_GENERIC
            ctx.turn.turn_messages.append(
                {"role": "assistant", "content": FALLBACK_GENERIC}
            )
            yield AgentEvent("final", FinalEventData(content=FALLBACK_GENERIC))
            return

        fallback_parts: list[str] = []
        for token in response.content_tokens:
            fallback_parts.append(token)
            yield AgentEvent("token", {"data": token})

        full_answer = "".join(fallback_parts) if fallback_parts else FALLBACK_GENERIC

        if not fallback_parts:
            yield AgentEvent("token", {"data": FALLBACK_GENERIC})

        ctx.turn.final_content = full_answer
        ctx.turn.turn_messages.append({"role": "assistant", "content": full_answer})

        yield AgentEvent("final", FinalEventData(content=full_answer))
        ctx.should_stop = True


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 7 — Save History
# ═══════════════════════════════════════════════════════════════════════════════


class SaveHistoryStage:
    """Сохранить turn в conversation store.

    Выполняется один раз в конце pipeline.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        # Сохраняем только когда turn завершён
        if not ctx.should_stop and not ctx.turn.final_content:
            return

        if ctx._stage_ran("save_history"):
            return
        ctx._mark_done("save_history")

        if not ctx.turn.turn_messages:
            return

        await ctx.store.aremember_turn(
            ctx.turn.session_id,
            ctx.turn.turn_messages,
        )
        logger.debug(
            "[SAVE_HISTORY] Saved %d messages for session %s",
            len(ctx.turn.turn_messages),
            ctx.turn.session_id,
        )
        return

    async def force_save(self, ctx: PipelineContext) -> None:
        """Принудительно сохранить (для аварийных ситуаций)."""
        if not ctx.turn.turn_messages:
            return

        await ctx.store.aremember_turn(
            ctx.turn.session_id,
            ctx.turn.turn_messages,
        )
