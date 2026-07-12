"""HTTP DTO for the Agent API service.

These models define the HTTP contract between the Web frontend and the
Agent API service (``api-service/src/api_service/server.py``). They are used both by FastAPI
(for automatic OpenAPI generation) and by external consumers.

Internal agent types (TypedDicts) live in ``demo.api.agent.types`` —
they are not part of the HTTP contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# === Requests ===


class ChatRequest(BaseModel):
    """Request to start or continue a chat."""

    message: str = Field(..., min_length=1, description="Text message from the user")
    session_id: str | None = Field(
        default="default", description="Session ID for history persistence"
    )


# === Responses ===


class HealthResponse(BaseModel):
    """API service health status."""

    api: str = "ok"
    ollama: dict = Field(..., description="LLM provider connection status")


class BacklogSessionMetadata(BaseModel):
    """Metadata for one backlog session entry."""

    session_id: str = Field(..., description="Session ID")
    size_bytes: int = Field(..., description="File size in bytes")
    num_events: int = Field(..., description="Number of events")
    first_event: dict | None = Field(default=None, description="First event")
    last_event: dict | None = Field(default=None, description="Last event")


class BacklogListResponse(BaseModel):
    """List of backlog sessions."""

    sessions: list[BacklogSessionMetadata] = Field(
        ..., description="List of session metadata entries"
    )


class BacklogEvent(BaseModel):
    """A single event/record in a backlog session.

    Fields vary by ``event`` type — ``model_response`` events contain
    ``duration_ms`` and ``tokens``. Extra fields are preserved via
    ``extra='allow'``.
    """

    model_config = ConfigDict(extra="allow")

    session_id: str = Field(..., description="Session ID")
    turn_id: str = Field(..., description="Turn ID")
    iteration: int = Field(..., description="Iteration number")
    event: str = Field(..., description="Event type")
    ts: str = Field(..., description="Timestamp")
    data: dict = Field(..., description="Event data")


class BacklogDetailResponse(BaseModel):
    """Events of a specific backlog session."""

    records: list[BacklogEvent] = Field(..., description="List of session events")
    session_id: str = Field(..., description="Requested session ID")
    count: int = Field(..., description="Number of records")


class ChatMessage(BaseModel):
    """A single message in chat history."""

    role: str = Field(..., description="Role: user / assistant / system")
    content: str = Field(default="", description="Message text")
    tool_calls: list[dict] | None = Field(default=None, description="Tool calls")


class SessionHistoryResponse(BaseModel):
    """Chat message history for a session."""

    messages: list[ChatMessage] = Field(
        ..., description="Messages (role, content, tool_calls)"
    )


# === Agent Management ===


# === Widget & LLM Config Models ===


class WidgetConfig(BaseModel):
    """Embed widget display configuration for an agent."""

    title: str = Field(default="Ассистент", description="Widget header title")
    greeting: str = Field(
        default="Чем могу помочь?", description="Greeting message in chat"
    )
    accent_color: str = Field(default="#0f766e", description="Accent color (hex)")
    position: str = Field(default="right", description="Widget position: right | left")


class LLMConfig(BaseModel):
    """Per-agent LLM configuration. All fields optional — falls back to global defaults when null."""

    provider: str | None = Field(
        default=None, description="LLM provider: ollama, mistral, openai, anthropic"
    )
    api_key: str | None = Field(default=None, description="API key for the provider")
    model: str | None = Field(
        default=None,
        description="Model name (e.g. qwen2.5:0.5b or mistral/mistral-small)",
    )
    api_base: str | None = Field(default=None, description="Base URL for the API")
    system_prompt: str | None = Field(
        default=None, description="System prompt (overrides global)"
    )
    temperature: float | None = Field(
        default=None, ge=0, le=2, description="Model temperature"
    )
    max_tokens: int | None = Field(
        default=None, ge=1, description="Maximum tokens in response"
    )


# === Agent Management ===


class AgentCreateRequest(BaseModel):
    """Request to create a new agent."""

    name: str = Field(
        ...,
        min_length=1,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Unique agent name (lowercase, no spaces)",
    )
    description: str = Field(default="", description="Human-readable description")
    tenant_ids: list[str] = Field(
        default_factory=list, description="Tenant IDs for this agent"
    )
    widget_config: WidgetConfig | None = Field(
        default=None, description="Embed widget display configuration"
    )
    llm_config: LLMConfig | None = Field(
        default=None, description="Per-agent LLM overrides"
    )
    provider_priority: list[str] = Field(
        default_factory=list,
        description="Provider names in priority order (from ProviderStore)",
    )


class AgentUpdateRequest(BaseModel):
    """Request to update an existing agent."""

    description: str | None = Field(
        default=None, description="Human-readable description"
    )
    tenant_ids: list[str] | None = Field(
        default=None, description="Tenant IDs for this agent"
    )
    widget_config: WidgetConfig | None = Field(
        default=None, description="Embed widget display configuration"
    )
    llm_config: LLMConfig | None = Field(
        default=None, description="Per-agent LLM overrides"
    )
    provider_priority: list[str] | None = Field(
        default=None,
        description="Provider names in priority order (from ProviderStore)",
    )


class AgentResponse(BaseModel):
    """Agent metadata."""

    name: str = Field(..., description="Unique agent name")
    description: str = Field(default="", description="Human-readable description")
    tenant_ids: list[str] = Field(default_factory=list, description="Tenant IDs")
    widget_config: WidgetConfig | None = Field(
        default=None, description="Embed widget display configuration"
    )
    llm_config: LLMConfig | None = Field(
        default=None, description="Per-agent LLM overrides"
    )
    provider_priority: list[str] = Field(
        default_factory=list,
        description="Provider names in priority order (from ProviderStore)",
    )
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")


class AgentListResponse(BaseModel):
    """List of agents."""

    agents: list[AgentResponse] = Field(..., description="List of agents")
