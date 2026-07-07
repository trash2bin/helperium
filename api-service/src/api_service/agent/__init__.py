"""Agent package for the LLM-based assistant.

Modules
-------
orchestrator      — Thin coordinator (LLMAgent, agent singleton)
turn_context       — Turn-level state container
prompts            — System prompt constants
token_estimator    — Token estimation & fallback trimming
event_stream       — SSE formatting utilities
llm_handler        — LLM call → outcome (tool_calls / final / empty)
tool_handler       — MCP tool execution
fallback_handler   — Graceful degradation when no answer is produced
llm_client         — LiteLLM wrapper (+ LLMClientProtocol)
mcp_client         — MCP SSE client
tool_parser        — Tool call extraction from LLM output
conversation       — Conversation history manager
types              — Shared type definitions
"""

from __future__ import annotations

from .conversation import ConversationManager
from .llm_client import LLMClient, LLMClientProtocol
from .mcp_client import MCPClient
from .orchestrator import LLMAgent, agent
from .tool_parser import ToolCallParser
from .types import AgentEvent, EventType, Message, ParsedToolCall, SessionId, TurnId

__all__ = [
    "AgentEvent",
    "ConversationManager",
    "EventType",
    "LLMAgent",
    "LLMClient",
    "LLMClientProtocol",
    "MCPClient",
    "agent",
    "ToolCallParser",
    # Types
    "Message",
    "ParsedToolCall",
    "SessionId",
    "TurnId",
]
