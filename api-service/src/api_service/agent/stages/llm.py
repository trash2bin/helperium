"""Stage 3 — LLM Stage.

Вызов LLM: стриминг + определение outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..error_context import ErrorContext
from ..models import CompletionRequest, CompletionResponse
from ..pipeline import PipelineContext
from ..token_estimator import estimate_tokens
from ..tool_parser import ToolCallParser
from ..types import AgentEvent, FinalEventData, StatusEventData

logger = logging.getLogger("api_service.agent.stages")


class LLMStage:
    """Вызов LLM: стриминг + определение outcome.

    Исправляет проблемы текущей реализации:
    - НЕ засовывает reasoning_content в messages
    - Возвращает outcome через CompletionResponse, а не мутабельные поля

    Outcome определяется по CompletionResponse:
    - tool_calls → pending_calls заполняется, LLMStage заканчивается
    - content → final_content, yield AgentEvent(“final”, …), return
    - reasoning_content → empty_rounds += 1, continue
    - пусто → empty_rounds += 1, continue
    """

    def __init__(self, max_empty_retries: int = 2) -> None:
        """LLMStage with configurable empty-response retry.

        Args:
            max_empty_retries: Times to retry when LLM returns empty
                (e.g. API finish_reason='error'). -1 = unlimited.
        """
        self._max_empty_retries = max_empty_retries

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # LLMStage повторяется на каждой итерации — никакого gating

        if ctx.should_stop:
            return

        # ✋ Проверка token budget ДО вызова LLM (MEDIUM-5 fix)
        # Предотвращает бесполезные LLM-вызовы когда контекст уже превысил лимит.
        # Считаем только system + contributions ЭТОГО turn'а (turn_messages),
        # а не всю историю диалога — она уже в кеше провайдера и заплачена.
        if ctx.max_turn_tokens > 0:
            model = getattr(ctx.llm_provider, "model", "")
            # budget = system prompt + только этот turn (user, tool_calls, tool_results)
            budget_msgs = [ctx.turn.messages[0]] + list(ctx.turn.turn_messages)
            _token_count = estimate_tokens(budget_msgs, model=model)
            if _token_count >= ctx.max_turn_tokens:
                logger.warning(
                    "[LLM_STAGE] Token budget exceeded before LLM call "
                    "(%d >= %d), skipping",
                    _token_count,
                    ctx.max_turn_tokens,
                )
                ctx.should_stop = True
                return

        response = await self._call_llm(ctx)

        # 4-way dispatch by response shape
        if response.tool_calls:
            async for event in self._emit_tool_calls(
                ctx, response.tool_calls, response.content, ctx.turn.iteration
            ):
                yield event
            return

        elif response.content:
            async for event in self._handle_content_response(ctx, response):
                yield event
            return

        elif response.reasoning_content:
            async for event in self._handle_reasoning(ctx, response):
                yield event
            return

        else:
            async for event in self._handle_empty_response(ctx):
                yield event
            return

    async def _call_llm(self, ctx: PipelineContext) -> CompletionResponse:
        """Build request, call LLM, record backlog. Plain async, no yields."""
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

        # Accumulate bench metrics
        if response.usage:
            ctx.bench["total_prompt_tokens"] += response.usage.prompt_tokens or 0
            ctx.bench["total_completion_tokens"] += (
                response.usage.completion_tokens or 0
            )
            ctx.bench["total_cost"] += response.cost or 0.0
        ctx.bench["llm_calls"] += 1

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

        return response

    async def _emit_tool_calls(
        self,
        ctx: PipelineContext,
        pending_calls: list[dict[str, Any]],
        raw_content: str | None,
        iteration: int,
    ) -> AsyncIterator[AgentEvent]:
        """Shared helper for LAYER 1 and LAYER 2 tool call emission.

        Sets had_tool_calls_this_iteration, pending_calls, yields status event,
        formats and appends assistant message.
        """
        ctx.had_tool_calls_this_iteration = True
        ctx.turn.pending_calls = pending_calls
        yield AgentEvent(
            "status",
            StatusEventData(phase="tool_calls", iteration=iteration),
        )
        formatted_tc = _format_tool_calls_for_message(pending_calls)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": raw_content or "",
        }
        if formatted_tc:
            assistant_msg["tool_calls"] = formatted_tc
        ctx.turn.messages.append(assistant_msg)
        ctx.turn.turn_messages.append(assistant_msg)

    async def _handle_content_response(
        self, ctx: PipelineContext, response: CompletionResponse
    ) -> AsyncIterator[AgentEvent]:
        """LAYER 2 (ToolCallParser) → LAYER 3 (safety net) → TRUE FINAL."""
        # ════════════════════════════════════════════════════════════════════
        # LAYER 2 — Fallback: ToolCallParser extracts tools from text
        # ──────────────────────────────────────────────────────────────────
        # If LiteLLM didn't parse tool calls (model returned JSON as
        # plain text despite add_function_to_prompt), try to extract
        # them from content manually. Supports: NDJSON, JSON arrays,
        # OpenAI-style function wrappers, markdown code blocks, inline.
        # Still does NOT stream content_tokens — they contain raw JSON.
        # ════════════════════════════════════════════════════════════════════
        parser = ToolCallParser()
        parsed = parser.extract_tool_calls({"content": response.content})
        if parsed:
            logger.info(
                "[LLM_STAGE][TOOL_PARSER] Extracted %d tool calls from JSON text "
                "(LiteLLM didn't parse them, fallback parser caught them)",
                len(parsed),
            )
            pending = []
            for tc in parsed:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                call_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                pending.append({"name": name, "arguments": args, "id": call_id})
            async for event in self._emit_tool_calls(
                ctx, pending, response.content, ctx.turn.iteration
            ):
                yield event
            return

        # ════════════════════════════════════════════════════════════════════
        # LAYER 3 — Safety net: block raw JSON from reaching the user
        # ──────────────────────────────────────────────────────────────────
        # If both LAYER 1 and LAYER 2 failed (unrecognised format,
        # malformed JSON, mixed text+JSON that parser missed), check
        # if the content still looks like tool calls via heuristic
        # pattern matching. If yes, emit error instead of final.
        # content_tokens are NEVER emitted in this path.
        # ════════════════════════════════════════════════════════════════════
        if _looks_like_raw_json_tool_calls(response.content):
            logger.warning(
                "[LLM_STAGE][SAFETY_NET] BLOCKED final: content looks like raw "
                "JSON tool calls (LiteLLM+ToolParser both failed). "
                "Content[0:200]=%s",
                response.content[:200],
            )
            if ctx.error_context:
                ctx.error_context = ctx.error_context.with_stage("llm_stage")
            else:
                ctx.error_context = ErrorContext(stage="llm_stage")
            ctx.error_context.error_code = "raw_json_tool_calls"
            ctx.error_context.message = response.content[:200]
            logger.error(
                "[STAGE][LLM] Blocked raw JSON tool calls",
                extra=ctx.error_context.to_dict(),
            )
            err_msg = (
                "Ошибка: модель вернула необработанный JSON-запрос. "
                "Попробуйте переформулировать вопрос."
            )
            ctx.turn.final_content = err_msg
            ctx.should_stop = True
            yield AgentEvent("error", {"message": err_msg})
            return

        # ════════════════════════════════════════════════════════════════════
        # TRUE FINAL — stream tokens to the user
        # ──────────────────────────────────────────────────────────────────
        # We only reach here if ALL THREE LAYERS above concluded that
        # the response is NOT tool calls — it's a genuine final answer.
        # content_tokens are safe to stream. This is the ONLY path
        # that yields token events to the user.
        # ════════════════════════════════════════════════════════════════════
        for token in response.content_tokens:
            yield AgentEvent("token", {"data": token})

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

    async def _handle_reasoning(
        self, ctx: PipelineContext, response: CompletionResponse
    ) -> AsyncIterator[AgentEvent]:
        """Handle reasoning-only response (thinking, no tool/content)."""
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

    async def _handle_empty_response(
        self, ctx: PipelineContext
    ) -> AsyncIterator[AgentEvent]:
        """Handle empty response + retry logic."""
        # Empty response (e.g. finish_reason 'error' mapped to 'stop')
        logger.warning(
            "[LLM_STAGE] Empty response (iteration %d, tool_results=%d)",
            ctx.turn.iteration,
            len(ctx.turn.tool_results),
        )

        # Retry when LLM returns empty (DeepSeek overload, API error).
        # Use iteration as retry counter — each run() call increments it
        # in the pipeline loop. Retries are NOT counted as empty_rounds.
        if self._max_empty_retries != 0 and (
            self._max_empty_retries == -1
            or ctx.turn.iteration < self._max_empty_retries
        ):
            logger.warning(
                "[LLM_STAGE] Retrying empty response (attempt %d/%d)",
                ctx.turn.iteration + 1,
                self._max_empty_retries,
            )
            # Brief delay for API backpressure
            await asyncio.sleep(0.5)
            yield AgentEvent(
                "status",
                StatusEventData(
                    phase="re_prompt",
                    iteration=ctx.turn.iteration,
                ),
            )
            return

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


def _looks_like_raw_json_tool_calls(content: str) -> bool:
    """Safety net: проверка что ``final`` не уйдёт с голым JSON тулов.

    Использует STRUCTURAL проверку через json.loads:
      - NDJSON: ``{"name": "...", "arguments": {...}}`` — arguments ОБЯЗАТЕЛЬНО dict
      - OpenAI-style: ``{"function": {"name": "...", "arguments": "..."}}``
      - array: ``[{...}, {...}]``

    ЛЕГИТИМНЫЕ JSON-ответы с name + arguments как string НЕ блокируются.
    """
    stripped = content.strip()

    if "Tool Calls" in stripped or "tool_calls" in stripped:
        return True
    if "name" in stripped and "arguments" in stripped:
        # ── Multi-line NDJSON: split by lines, check each separately ──
        # json.loads() raises JSONDecodeError('Extra data') on NDJSON.
        if stripped.startswith("{") and "\n" in stripped:
            lines = [line.strip() for line in stripped.split("\n") if line.strip()]
            for line in lines:
                if (
                    not line.startswith("{")
                    or "name" not in line
                    or "arguments" not in line
                ):
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        # NDJSON: {"name": "...", "arguments": {...}} — dict required
                        if parsed.get("name") and isinstance(
                            parsed.get("arguments"), dict
                        ):
                            return True
                        # OpenAI-style: {"function": {"name": "...", "arguments": "..."}}
                        func = parsed.get("function")
                        if (
                            isinstance(func, dict)
                            and func.get("name")
                            and "arguments" in func
                        ):
                            return True
                except (json.JSONDecodeError, TypeError):
                    pass
            return False

        # ── Single JSON object or array ──
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        # OpenAI-style: {"function": {"name": "...", "arguments": "..."}}
                        func = item.get("function")
                        if (
                            isinstance(func, dict)
                            and func.get("name")
                            and "arguments" in func
                        ):
                            return True
                        # NDJSON: {"name": "...", "arguments": {...}} — arguments ДОЛЖЕН быть dict
                        if item.get("name") and isinstance(item.get("arguments"), dict):
                            return True
            elif isinstance(parsed, dict):
                # OpenAI-style wrapper
                func = parsed.get("function")
                if isinstance(func, dict) and func.get("name") and "arguments" in func:
                    return True
                # LAYER 1 LiteLLM: {"type": "function", "name": "...", "arguments": {...}}
                if parsed.get("type") == "function" and parsed.get("name"):
                    return True
                # NDJSON: {"name": "...", "arguments": {...}} — arguments ОБЯЗАТЕЛЬНО dict
                if parsed.get("name") and isinstance(parsed.get("arguments"), dict):
                    return True
        except (json.JSONDecodeError, TypeError):
            # ── Regex fallback for malformed JSON that looks like tool calls ──
            # When json.loads() fails (malformed JSON, unclosed strings, etc.),
            # use a heuristic regex check. Only triggers if content clearly
            # matches tool call shape: {"name": "...", "arguments": ...}
            MALFORMED_TOOL_RE = re.compile(
                r'\{\s*"name"\s*:\s*"[a-z_]+"\s*,'
                r'\s*"arguments"\s*:\s*"[a-z_]+',
                re.DOTALL,
            )
            if MALFORMED_TOOL_RE.search(stripped):
                return True

    return False


def _format_tool_calls_for_message(tool_calls: list[dict]) -> list[dict]:
    """Format tool_calls from LLM response into message-compatible format."""
    result = []
    for tc in tool_calls:
        raw_args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
        # Protect against double-encoding: if arguments is already a JSON string,
        # use as-is (LAYER 1 LiteLLM passes string). Only json.dumps if it's a dict.
        if isinstance(raw_args, dict):
            serialized = json.dumps(raw_args, ensure_ascii=False)
        else:
            serialized = raw_args
        result.append(
            {
                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": tc.get("name", tc.get("function", {}).get("name", "")),
                    "arguments": serialized,
                },
            }
        )
    return result
