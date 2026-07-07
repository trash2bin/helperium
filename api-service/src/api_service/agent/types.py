"""Type definitions for the agent module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict, NotRequired

# Event types for AgentEvent
EventType = Literal[
    "status",
    "token",
    "tool_call",
    "tool_result",
    "final",
    "error",
]


# Message types for LLM conversation
class MessageRole(str):
    """Valid message roles in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# Type for message content
MessageContent = str | list[dict[str, Any]]


class BaseMessage(TypedDict):
    """Base message structure."""

    role: str
    content: MessageContent


class SystemMessage(BaseMessage):
    """System message."""

    role: Literal["system"]  # type: ignore[reportIncompatibleVariableOverride]


class UserMessage(BaseMessage):
    """User message."""

    role: Literal["user"]  # type: ignore[reportIncompatibleVariableOverride]


class AssistantMessage(BaseMessage):
    """Assistant message with optional tool calls."""

    role: Literal["assistant"]  # type: ignore[reportIncompatibleVariableOverride]
    tool_calls: NotRequired[list[ToolCall]]


class ToolMessage(BaseMessage):
    """Tool response message."""

    role: Literal["tool"]  # type: ignore[reportIncompatibleVariableOverride]
    tool_call_id: str
    name: str


# Union type for all message types
Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage


# Tool call types
class FunctionCall(TypedDict):
    """Function call structure."""

    name: str
    arguments: str  # JSON string


class ToolCall(TypedDict):
    """Tool call structure."""

    id: str | None
    type: Literal["function"]
    function: FunctionCall


# Parsed tool call (internal representation)
class ParsedToolCall(TypedDict):
    """Internal representation of a parsed tool call."""

    id: str
    name: str
    arguments: dict[str, Any]


# MCP tool definition (flexible to match MCP library output)
class MCPToolParameter(TypedDict, total=False):
    """MCP tool parameter."""

    type: str
    description: str | None
    enum: NotRequired[list[str]]
    items: NotRequired[dict[str, Any]]
    properties: NotRequired[dict[str, Any]]


class MCPToolSchema(TypedDict, total=False):
    """MCP tool schema."""

    type: str
    properties: NotRequired[dict[str, MCPToolParameter]]
    required: NotRequired[list[str]]


class MCPToolFunction(TypedDict, total=False):
    """MCP tool function definition."""

    name: str
    description: NotRequired[str]
    parameters: NotRequired[MCPToolSchema | dict[str, Any]]


class MCPTool(TypedDict, total=False):
    """MCP tool definition."""

    type: Literal["function"]
    function: MCPToolFunction | dict[str, Any]


# LLM response types
class UsageInfo(TypedDict):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMResponse(TypedDict):
    """Complete LLM response."""

    role: str
    content: str
    tool_calls: NotRequired[list[ToolCall]]
    reasoning_content: NotRequired[str]
    _usage: NotRequired[UsageInfo]


# Agent event data types
class StatusEventData(TypedDict):
    """Data for status events."""

    phase: Literal["empty_round", "tool_calls"]
    iteration: int
    empty_rounds: NotRequired[int]
    count: NotRequired[int]


class TokenEventData(TypedDict):
    """Data for token events."""

    data: str


class ToolCallEventData(TypedDict):
    """Data for tool_call events."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultEventData(TypedDict):
    """Data for tool_result events."""

    id: str
    name: str
    result: str


class FinalEventData(TypedDict):
    """Data for final events."""

    content: str


class ErrorEventData(TypedDict):
    """Data for error events."""

    message: str


# Union type for all event data
AgentEventData = (
    StatusEventData
    | TokenEventData
    | ToolCallEventData
    | ToolResultEventData
    | FinalEventData
    | ErrorEventData
    | dict[str, Any]  # Fallback for any other data
)


# AgentEvent — the unit of output from the orchestrator


@dataclass(slots=True)
class AgentEvent:
    """Event emitted by the agent during processing."""

    type: EventType
    data: AgentEventData


# Session and turn types
SessionId = str
TurnId = str
TurnMessages = list[Message]
MessageHistory = list[Message]
