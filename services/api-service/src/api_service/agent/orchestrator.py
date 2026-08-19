"""Public lifecycle boundary for the minimal append-only agent loop."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from helperium_sdk.settings import settings

from api_service.backlog import backlog
from api_service.error_messages import classify_error
from api_service.guardrails import get_guard_checker

from .adapters import _AsyncBacklogWriter, _AsyncSpendingTracker
from .conversation import ConversationManager
from .factory import _create_env_provider, _pool, resolve_llm
from .loop import AppendOnlyLoop, LoopLimits, LoopRun, Transcript
from .mcp_client import MCPClient
from .prompts import SYSTEM_PROMPT
from .types import AgentEvent, SessionId, TurnMessages

logger = logging.getLogger("api_service.agent.orchestrator")


class LLMAgent:
    """Run one typed, scoped, append-only agent turn.

    This class owns request lifecycle only. Tool dispatch, transcript append and
    stop conditions live in ``AppendOnlyLoop``; no pipeline or stage objects exist.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        mcp_client: MCPClient | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        self._test_llm_client = llm_client
        self.mcp_client = mcp_client or MCPClient()
        self.conversation_manager = conversation_manager or ConversationManager()
        self._settings = settings

    async def stream_events(
        self,
        user_message: str,
        session_id: SessionId = "default",
        tenant_ids: list[str] | None = None,
        llm_config: dict | None = None,
        llm_client: Any | None = None,
        provider_priority: list[str] | None = None,
        system_prompt: str | None = None,
        lang: str = "ru",
        correlation_id: str = "",
    ) -> AsyncIterator[AgentEvent]:
        del correlation_id  # kept only for the stable public route signature
        session_id = self.conversation_manager.normalize_session_id(session_id)
        resolved_tenants = tuple(tenant_ids or ())
        turn_id = backlog.turn_start(session_id, user_message)
        started = time.monotonic()
        run: LoopRun | None = None
        error_message = ""

        lock = await self.conversation_manager.get_session_lock(session_id)
        async with lock:
            try:
                provider = await resolve_llm(
                    llm_client=llm_client,
                    llm_config=llm_config,
                    provider_priority=provider_priority,
                    _test_llm_client=self._test_llm_client,
                )
                history = await self.conversation_manager.aget_history_messages(
                    session_id
                )
                messages = [
                    {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                    *history,
                    {"role": "user", "content": user_message},
                ]
                run = LoopRun(
                    Transcript(messages=messages, current_turn_start=len(messages) - 1)
                )
                async with self.mcp_client.get_session(
                    tenant_ids=list(resolved_tenants)
                ) as mcp:
                    loop = AppendOnlyLoop(
                        provider=provider,
                        mcp=mcp,
                        limits=LoopLimits(
                            max_model_calls=self._settings.agent_max_iterations,
                            max_tool_calls=self._settings.agent_max_tool_calls,
                            max_context_tokens=self._settings.agent_max_turn_tokens,
                            max_empty_responses=self._settings.agent_max_empty_rounds,
                        ),
                        guard_checker=get_guard_checker(),
                        spending=_AsyncSpendingTracker(),
                        backlog=_AsyncBacklogWriter(),
                        session_id=session_id,
                        turn_id=turn_id,
                        tenant_ids=resolved_tenants,
                    )
                    async for event in loop.run(run):
                        yield event
            except Exception as exc:
                error_message = str(exc)
                logger.exception("[AGENT] turn failed for session %s", session_id)
                backlog.error(
                    session_id, turn_id, run.metrics.model_calls if run else 0, str(exc)
                )
                yield AgentEvent("error", {"message": classify_error(exc, lang)})
            finally:
                if run is not None and run.transcript.current_turn:
                    try:
                        await self.conversation_manager.aremember_turn(
                            session_id,
                            cast("TurnMessages", run.transcript.current_turn),
                        )
                    except Exception:
                        logger.exception(
                            "[AGENT] failed to persist turn for %s", session_id
                        )
                self._close_backlog(session_id, turn_id, started, run, error_message)

    def _close_backlog(
        self,
        session_id: str,
        turn_id: str,
        started: float,
        run: LoopRun | None,
        error_message: str,
    ) -> None:
        metrics = run.metrics if run else None
        outcome = run.outcome.kind if run and run.outcome else "provider_error"
        final_text = run.outcome.final_text if run and run.outcome else ""
        backlog.turn_end(
            session_id=session_id,
            turn_id=turn_id,
            duration_ms=(time.monotonic() - started) * 1000,
            outcome=outcome,
            total_prompt_tokens=metrics.prompt_tokens if metrics else 0,
            total_completion_tokens=metrics.completion_tokens if metrics else 0,
            total_cost=metrics.total_cost if metrics else 0.0,
            llm_calls=metrics.model_calls if metrics else 0,
            tool_calls=metrics.tool_calls if metrics else 0,
            tool_errors=metrics.tool_errors if metrics else 0,
            empty_results=0,
            empty_rounds=metrics.empty_responses if metrics else 0,
            iterations=metrics.model_calls if metrics else 0,
            final_length_chars=len(final_text),
            final_text=final_text,
            error_message=error_message[:500],
        )

    async def health(self) -> dict[str, Any]:
        """Return provider resolution health without executing an agent turn."""
        try:
            worker = await _pool.get_any_worker()
            provider = worker or _create_env_provider()
            return {
                "status": "ok",
                "model": provider.model,
                "api_base": provider.api_base,
                "thinking_enabled": provider.enable_thinking,
            }
        except Exception:
            logger.exception("[AGENT] provider health check failed")
            return {
                "status": "degraded",
                "model": "unknown",
                "api_base": None,
                "thinking_enabled": False,
            }


agent = LLMAgent()
