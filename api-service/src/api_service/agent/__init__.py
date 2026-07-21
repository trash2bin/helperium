"""Agent package for the LLM-based assistant.

Modules
-------
orchestrator       — Thin coordinator (LLMAgent, agent singleton)
pipeline            — Pipeline orchestration (Pipeline, PipelineContext, Stage, Middleware)
stages              — Pipeline stages (LLMStage, ToolExecutionStage, GuardInputStage, …)
middlewares          — Pipeline middleware (SpendingMiddleware, BacklogMiddleware, …)
turn_context        — Turn-level state container
prompts             — System prompt constants
token_estimator     — Token estimation & fallback trimming
event_stream        — SSE formatting utilities
llm_client          — LiteLLM wrapper (+ LLMClientProtocol) [DEPRECATED]
litellm_provider    — Clean LiteLLM adapter under LLMProvider protocol [NEW]
provider_pool       — Health-checked LLM provider pool with failover [NEW]
mcp_client          — MCP SSE client
tool_parser         — Tool call extraction from LLM output
conversation        — Conversation history manager
types               — Shared type definitions
models              — Pydantic domain models [NEW]
protocols           — Structural subtyping contracts [NEW]
"""

from __future__ import annotations

from .conversation import ConversationManager
from .litellm_provider import LiteLLMProvider
from .llm_client import LLMClient, LLMClientProtocol
from .mcp_client import MCPClient, ToolResult
from .models import (
    CompletionRequest,
    CompletionResponse,
    ProviderConfig,
    UsageInfo,
)
from .middlewares import BacklogMiddleware, SpendingMiddleware, TokenBudgetMiddleware
from .orchestrator import LLMAgent, agent
from .pipeline import Pipeline, PipelineContext, Stage, Middleware
from .provider_pool import ProviderPool, ProviderWorker
from .stages import (
    FallbackStage,
    GuardInputStage,
    GuardOutputStage,
    LLMStage,
    SaveHistoryStage,
    ToolDiscoveryStage,
    ToolExecutionStage,
)
from .protocols import (
    BacklogWriter,
    ConversationStore,
    LLMProvider,
    MCPToolProvider,
    SpendingTracker,
)
from .tool_parser import ToolCallParser
from .types import AgentEvent, EventType, Message, ParsedToolCall, SessionId, TurnId
from .turn_context import TurnContext

__all__ = [
    "AgentEvent",
    "BacklogMiddleware",
    "BacklogWriter",
    "CompletionRequest",
    "CompletionResponse",
    "ConversationManager",
    "ConversationStore",
    "EventType",
    "FallbackStage",
    "GuardInputStage",
    "GuardOutputStage",
    "LLMAgent",
    "LLMClient",
    "LLMClientProtocol",
    "LLMProvider",
    "LLMStage",
    "LiteLLMProvider",
    "MCPClient",
    "MCPToolProvider",
    "Middleware",
    "Pipeline",
    "PipelineContext",
    "ProviderConfig",
    "ProviderPool",
    "ProviderWorker",
    "SaveHistoryStage",
    "SpendingMiddleware",
    "SpendingTracker",
    "Stage",
    "TokenBudgetMiddleware",
    "ToolCallParser",
    "ToolDiscoveryStage",
    "ToolExecutionStage",
    "ToolResult",
    "TurnContext",
    "UsageInfo",
    "agent",
    # Legacy types
    "Message",
    "ParsedToolCall",
    "SessionId",
    "TurnId",
]
