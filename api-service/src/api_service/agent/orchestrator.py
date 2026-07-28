"""Main agent orchestrator — coordinator that wires Pipeline + adapters.

Responsibility
--------------
Drive a single conversation turn: build context, run the pipeline,
yield events.  Pipeline orchestration goes to ``pipeline.py``,
stage logic to ``stages.py``, middleware to ``middlewares.py``,
legacy adapters to ``legacy_adapters.py``.

Backward-compatible: ``stream_answer()``, ``stream_sse()``, ``stream_events()``
keep the same signatures.  ``health()`` stays.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from helperium_sdk.settings import settings

from .conversation import ConversationManager
from .event_stream import format_sse_event, unstreamed_suffix
from .llm_client import (
    LLMClient,
    create_client,
    create_fallback_client,
)
from .mcp_client import MCPClient
from .middlewares import (
    SpendingMiddleware,
    TokenBudgetMiddleware,
)
from .pipeline import Pipeline, PipelineContext
from .prompts import SYSTEM_PROMPT
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
from .legacy_adapters import (
    _AsyncSpendingTracker,
    _OldStyleLLMAdapter,
    _AsyncBacklogWriter,
)

from api_service.backlog import backlog
from api_service.error_messages import classify_error
from api_service.guardrails import get_guard_checker

logger = logging.getLogger("api_service.agent.orchestrator")


# ── Legacy adapters ──────────────────────────────────────
# Defined in legacy_adapters.py — imported above.


class LLMAgent:
    """Thin orchestrator — creates Pipeline and runs it for each request.

    Components are injected in ``__init__`` (with defaults for production)
    so tests can substitute mocks freely.

    The Pipeline is assembled once in ``__init__`` and reused across
    requests.  Per-request state lives in ``PipelineContext``.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        mcp_client: MCPClient | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        # ── Persistent core (no LLM — created fresh per request) ────────
        # llm_client is DEPRECATED for production.  When passed (tests only)
        # it becomes the fallback for stream_events() when neither
        # llm_client nor llm_config is provided explicitly.
        self._test_llm_client: LLMClient | None = llm_client
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
                GuardOutputStage(),
                FallbackStage(),
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

    async def stream_answer(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Backward-compatible token stream (plain strings, no SSE)."""
        streamed_text = ""
        async for event in self.stream_events(user_message, session_id=session_id):
            if event.type == "token":
                token = str(event.data)
                streamed_text += token
                yield token
            elif event.type == "final":
                content = (
                    event.data.get("content") if isinstance(event.data, dict) else None
                )
                if content:
                    suffix = unstreamed_suffix(streamed_text, str(content))
                    if suffix:
                        yield suffix

    async def stream_sse(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Stream Server-Sent Events (legacy compatibility)."""
        async for event in self.stream_events(user_message, session_id=session_id):
            yield format_sse_event(event)

    async def stream_events(
        self,
        user_message: str,
        session_id: SessionId = "default",
        tenant_ids: list[str] | None = None,
        llm_config: dict | None = None,
        llm_client: LLMClient | None = None,
        provider_priority: list[str] | None = None,
        system_prompt: str | None = None,
        lang: str = "ru",
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent events: tokens, tool calls, tool results, final.

        This is the main entry point for new code.

        **Real-time provider update** — when neither ``llm_client``,
        ``provider_priority`` nor ``llm_config`` is provided, a fresh
        ``create_fallback_client()`` is built from the current ProviderStore
        on every call. This means adding/changing/deleting providers via
        the admin dashboard takes effect immediately without restarting
        the service.

        Args:
            user_message:   Raw text from the user.
            session_id:     Conversation session identifier.
            tenant_ids:     Scopes the MCP session to one or more tenants.
            llm_config:     Overrides the global LLM config for this request.
            llm_client:     Overrides the LLM client for this request (e.g. prioritized).
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

        # Use scripted LLM in dev mode (USE_SCRIPTED_LLM=1).
        # Overrides ALL other providers — deterministic responses.
        from .scripted_provider import create_scripted_provider as _create_scripted

        scripted = _create_scripted()
        if scripted:
            request_llm: Any = scripted
        elif llm_client:
            request_llm: Any = llm_client
        elif llm_config:
            # If llm_config is a single provider config
            request_llm = create_client(llm_config)
        elif provider_priority:
            # Resolve first valid provider from priority list (sync operation)
            from api_service.provider_store import (
                KNOWN_PROVIDERS as _KNOWN,
                get_provider_store,
            )

            store = get_provider_store()
            raw_providers = store.all_providers_raw
            found = None
            for name in provider_priority:
                provider_data = raw_providers.get(name)
                if not provider_data:
                    continue
                if not provider_data.get("enabled", True):
                    continue
                model = provider_data.get("model", "")
                if not model:
                    continue
                # Allow empty api_key — local providers (Ollama, local) don't need one.
                # LiteLLM handles auth-less calls gracefully when api_key is empty.
                # The key requirement was blocking local Ollama providers from working.
                found = (name, provider_data)
                break

            if found:
                name, data = found
                model = data["model"]
                provider = data.get("provider", "")
                if not model.startswith(tuple(p + "/" for p in _KNOWN)) and provider:
                    model = f"{provider}/{model}"
                api_base = data.get("api_base", "") or ""
                from .litellm_provider import LiteLLMProvider

                request_llm = LiteLLMProvider(
                    model=model,
                    api_base=api_base or None,
                    timeout=120.0,
                )
            else:
                request_llm = self._test_llm_client or create_fallback_client()
        else:
            # FRESH client every request — reflects current ProviderStore state.
            # Falls back to self._test_llm_client when set (tests only).
            request_llm = self._test_llm_client or create_fallback_client()

        # ── Wrap old-style client if needed ────────────────────────────────
        # New pipeline expects the ``complete() → CompletionResponse`` contract.
        # Old clients expose ``stream_completion()`` — wrap with adapter.
        if not hasattr(request_llm, "complete"):
            request_llm = _OldStyleLLMAdapter(request_llm)

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
                    )

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
                    error_message=_error_msg[:500] if _error_msg else "",
                )

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Get agent health status."""
        import warnings

        try:
            live = create_fallback_client()
            return {
                "status": "ok",
                "model": live.model,
                "api_base": live.api_base,
                "thinking_enabled": live.enable_thinking,
            }
        except Exception as exc:
            warnings.warn(f"Health check failed to create client: {exc}")
            return {
                "status": "degraded",
                "model": "unknown",
                "api_base": None,
                "thinking_enabled": False,
            }


# ── Default singleton ─────────────────────────────────────────────────────
agent = LLMAgent()
