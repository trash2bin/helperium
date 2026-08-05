"""Main agent orchestrator — coordinator that wires Pipeline + adapters.

Responsibility
--------------
Drive a single conversation turn: build context, run the pipeline,
yield events.  Pipeline orchestration goes to ``pipeline.py``,
stage logic to ``stages.py``, middleware to ``middlewares.py``,
async adapters for sync singletons (SpendingChecker, ModelBacklog).

Main entry point: ``stream_events()``.  ``health()`` stays.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from helperium_sdk.settings import settings

from .conversation import ConversationManager
from .factory import resolve_llm, _pool, _create_env_provider
from .mcp_client import MCPClient
from .middlewares import (
    SpendingMiddleware,
    TokenBudgetMiddleware,
)
from .pipeline import Pipeline, PipelineContext
from .prompts import SYSTEM_PROMPT
from .types import TurnMessages
from .stages import (
    FallbackStage,
    GuardInputStage,
    GuardOutputStage,
    LLMStage,
    SaveHistoryStage,
    ToolDiscoveryStage,
    ToolExecutionStage,
)
from .turn_context import TurnContext
from .types import (
    AgentEvent,
    SessionId,
)
from api_service.backlog import backlog

from .adapters import _AsyncBacklogWriter, _AsyncSpendingTracker

from api_service.error_messages import classify_error
from api_service.guardrails import get_guard_checker

logger = logging.getLogger("api_service.agent.orchestrator")


# ── Module-level constants ──────────────────────────────────────────────
# All provider resolution functions are imported from factory.py.
# _pool, _create_env_provider, _resolve_pool_or_env, _prefix_model
# are used by health() method and re-exported for deps.py.


class LLMAgent:
    """Thin orchestrator — creates Pipeline and runs it for each request.

    Components are injected in ``__init__`` (with defaults for production)
    so tests can substitute mocks freely.

    The Pipeline is assembled once in ``__init__`` and reused across
    requests.  Per-request state lives in ``PipelineContext``.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        mcp_client: MCPClient | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        # ── Persistent core (no LLM — created fresh per request) ────────
        # llm_client is for tests only — overrides all other resolution.
        self._test_llm_client: Any | None = llm_client
        self.mcp_client = mcp_client or MCPClient()
        self.conversation_manager = conversation_manager or ConversationManager()

        # ── Pipeline — assembled once ───────────────────────────────────
        self._pipeline = Pipeline(
            stages=[
                GuardInputStage(),
                ToolDiscoveryStage(),
                LLMStage(),
                ToolExecutionStage(),
            ],
            finalizer_stages=[
                FallbackStage(),
                GuardOutputStage(),
                SaveHistoryStage(),
            ],
            middlewares=[
                SpendingMiddleware(),
                TokenBudgetMiddleware(),
            ],
        )

        # ── Settings (live, not cached) ──────────────────────────────────
        # Settings are mutated at runtime by LiveAbuseProvider.apply_runtime_settings().
        # We keep a reference to the settings module and read values fresh on each
        # request instead of caching them in __init__.
        self._settings = settings

    # ── Public entry points ──────────────────────────────────────────────

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
        """Stream agent events: tokens, tool calls, tool results, final.

        This is the main entry point for new code.

        **Real-time provider update** — when none of ``llm_client``,
        ``provider_priority`` nor ``llm_config`` is provided, the
        ``ProviderPool`` is used.  If the pool is empty an env-based
        ``LiteLLMProvider`` is created instead.

        Args:
            user_message:   Raw text from the user.
            session_id:     Conversation session identifier.
            tenant_ids:     Scopes the MCP session to one or more tenants.
            llm_config:     Overrides the global LLM config for this request.
            llm_client:     Overrides the LLM provider for this request (e.g. prioritized).
            provider_priority:  Ordered list of provider names to try (first valid wins).
            system_prompt:  Overrides the global system prompt.
        """
        session_id = self.conversation_manager.normalize_session_id(session_id)
        logger.info(
            "[AGENT] User message for session %s (tenants: %s): %s",
            session_id,
            tenant_ids or ["(default)"],
            user_message[:100],
        )

        # Resolve the LLM provider for this request.
        request_llm = await resolve_llm(
            llm_client=llm_client,
            llm_config=llm_config,
            provider_priority=provider_priority,
            _test_llm_client=self._test_llm_client,
        )

        # ── Async adapters ──────────────────────────────────────────────
        async_spending = _AsyncSpendingTracker()
        async_backlog = _AsyncBacklogWriter()

        lock = await self.conversation_manager.get_session_lock(session_id)
        async with lock:
            # Build turn context
            effective_prompt = system_prompt or SYSTEM_PROMPT

            ctx = await TurnContext.build(
                user_message=user_message,
                session_id=session_id,
                system_prompt=effective_prompt,
                conversation_manager=self.conversation_manager,
                tenant_ids=tenant_ids,
            )
            ctx.turn_id = backlog.turn_start(session_id, user_message)

            # Build PipelineContext and run
            _turn_start = time.monotonic()
            _outcome = "final"
            _error_msg = ""
            pipeline_ctx = None
            try:
                async with self.mcp_client.get_session(
                    tenant_ids=tenant_ids
                ) as mcp_session:
                    pipeline_ctx = PipelineContext(
                        turn=ctx,
                        llm_provider=request_llm,
                        mcp_session=mcp_session,
                        store=self.conversation_manager,
                        spending=async_spending,
                        backlog=async_backlog,
                        guard_checker=get_guard_checker(),
                        max_iterations=self._settings.agent_max_iterations,
                        max_empty_rounds=self._settings.agent_max_empty_rounds,
                        max_turn_tokens=self._settings.agent_max_turn_tokens,
                        max_tool_calls_per_turn=self._settings.agent_max_tool_calls,
                    )

                    pipeline_ctx.set_error_context(session_id, correlation_id)

                    async for event in self._pipeline.run(pipeline_ctx):
                        yield event

            except Exception as exc:
                _outcome = "error"
                _error_msg = str(exc)
                logger.exception("[AGENT] Turn failed: %s", exc)
                backlog.error(session_id, ctx.turn_id, ctx.iteration, str(exc))
                yield AgentEvent(
                    "error",
                    {"message": classify_error(exc, lang)},
                )
            finally:
                # ⚡ Save history even if pipeline failed (HIGH-1 fix)
                if pipeline_ctx is not None:
                    try:
                        await SaveHistoryStage().force_save(pipeline_ctx)
                    except Exception:
                        logger.exception(
                            "[AGENT] force_save(pipeline_ctx) failed for session %s",
                            session_id,
                        )
                elif ctx.turn_messages:
                    # MCP connection failed before pipeline_ctx was created;
                    # save turn_messages directly via conversation_manager.
                    try:
                        await self.conversation_manager.aremember_turn(
                            ctx.session_id,
                            cast("TurnMessages", ctx.turn_messages),
                        )
                    except Exception:
                        logger.exception(
                            "[AGENT] force_save(ctx) failed for session %s",
                            session_id,
                        )

                _duration = (time.monotonic() - _turn_start) * 1000
                if not ctx.final_content and _outcome != "error":
                    _outcome = "limit"
                _b = pipeline_ctx.bench if pipeline_ctx else {}
                _tc = pipeline_ctx.tool_call_count if pipeline_ctx else 0
                backlog.turn_end(
                    session_id=session_id,
                    turn_id=ctx.turn_id,
                    duration_ms=_duration,
                    outcome=_outcome,
                    total_prompt_tokens=_b.get("total_prompt_tokens", 0),
                    total_completion_tokens=_b.get("total_completion_tokens", 0),
                    total_cost=_b.get("total_cost", 0.0),
                    llm_calls=_b.get("llm_calls", 0),
                    tool_calls=_tc,
                    tool_errors=_b.get("tool_errors", 0),
                    empty_results=_b.get("empty_results", 0),
                    empty_rounds=ctx.empty_rounds,
                    iterations=ctx.iteration,
                    final_length_chars=len(ctx.final_content)
                    if ctx.final_content
                    else 0,
                    final_text=ctx.final_content or "",
                    error_message=_error_msg[:500] if _error_msg else "",
                )

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Get agent health status."""
        import warnings

        try:
            # Check ProviderPool first
            worker = await _pool.get_any_worker()
            if worker is not None:
                return {
                    "status": "ok",
                    "model": worker.model,
                    "api_base": worker.api_base,
                    "thinking_enabled": worker.enable_thinking,
                }
            # Fallback to env-based provider
            live = _create_env_provider()
            return {
                "status": "ok",
                "model": live.model,
                "api_base": live.api_base,
                "thinking_enabled": live.enable_thinking,
            }
        except Exception as exc:
            warnings.warn(f"Health check failed: {exc}")
            return {
                "status": "degraded",
                "model": "unknown",
                "api_base": None,
                "thinking_enabled": False,
            }


# ── Default singleton ─────────────────────────────────────────────────────
agent = LLMAgent()
