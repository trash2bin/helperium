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
    """Request to start or continue a chat.

    ``session_id`` is required and must be unique per visitor conversation:
    the server binds every session to a one-time capability token
    (``X-Session-Token``), so reusing a predictable or shared id — the old
    ``"default"`` fallback merged all session-less clients into one
    transcript — is rejected once that session exists.
    """

    message: str = Field(..., min_length=1, description="Text message from the user")
    session_id: str = Field(
        ...,
        min_length=1,
        description="Session ID for history persistence; issued a capability token on first use",
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


# === Widget Problem Reports ===


class ReportMessageContext(BaseModel):
    """The assistant message a visitor is complaining about."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        default="assistant",
        pattern=r"^(assistant|error)$",
        description="assistant answer or an error bubble",
    )
    text: str = Field(default="", max_length=4000, description="Message text")
    tools: list[str] = Field(
        default_factory=list, max_length=20, description="Tool names used for the answer"
    )
    display_names: list[str] = Field(
        default_factory=list, max_length=20, description="Human-readable tool labels"
    )


class ReportTranscriptMessage(BaseModel):
    """One transcript entry attached to a widget problem report."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., pattern=r"^(user|assistant)$", description="Message kind")
    text: str = Field(default="", max_length=2000, description="Message text")
    tools: list[str] = Field(default_factory=list, max_length=20)
    ts: str | None = Field(default=None, max_length=64, description="Client timestamp")


class ReportLastError(BaseModel):
    """Last SSE error observed by the widget, with its correlation id."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=500, description="Error bubble text")
    correlation_id: str | None = Field(
        default=None, max_length=64, description="Correlation id from the SSE error event"
    )


class ReportCreateRequest(BaseModel):
    """Widget problem report payload (public ``POST /api/reports``).

    Stored verbatim for operator review; the content is never fed to the LLM,
    so report text cannot steer model behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(..., min_length=1, max_length=128, description="Agent name")
    session_id: str = Field(..., min_length=1, max_length=128, description="Widget session id")
    lang: str | None = Field(default=None, pattern=r"^(ru|en)$", description="Widget language")
    message: ReportMessageContext = Field(
        ..., description="The message being reported"
    )
    transcript: list[ReportTranscriptMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Recent session transcript (client-capped)",
    )
    comment: str | None = Field(
        default=None, max_length=1000, description="Optional visitor comment"
    )
    last_error: ReportLastError | None = Field(
        default=None, description="Last error seen in this session"
    )
    page_url: str | None = Field(
        default=None, max_length=2048, description="Host page URL the widget runs on"
    )


class ReportStatusUpdateRequest(BaseModel):
    """Operator update of a report's review status."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^(new|reviewed)$", description="New status")


# === Widget & LLM Config Models ===


class WidgetConfig(BaseModel):
    """Embed widget display configuration for an agent."""

    title: str = Field(default="Ассистент", description="Widget header title")
    greeting: str = Field(
        default="Чем могу помочь?", description="Greeting message in chat"
    )
    accent_color: str = Field(default="#0f766e", description="Accent color (hex)")
    position: str = Field(default="right", description="Widget position: right | left")


# === Voice Config Models (STT) ===


class STTProviderConfig(BaseModel):
    """STT provider configuration — one entry in the priority list."""

    name: str = Field(..., description="Display name for this provider")
    provider: str = Field(
        ...,
        pattern=r"^(litellm|local)$",
        description="Engine type: litellm (OpenAI/Azure/Nvidia Riva API) or local (faster-whisper/whisper.cpp)",
    )
    model: str = Field(
        default="whisper-1",
        description="Model name: whisper-1, base, small, medium, large-v3, nvidia_riva/stt_en_streaming",
    )
    api_key: str | None = Field(
        default=None, description="API key for cloud STT (OpenAI, Azure, etc.)"
    )
    api_base: str | None = Field(default=None, description="Custom API base URL")
    enabled: bool = Field(default=True, description="Whether this provider is active")


class VoiceConfig(BaseModel):
    """Global STT (Speech-to-Text) configuration.

    A priority list of STT providers with fallback support.
    """

    enabled: bool = Field(default=True, description="Master switch for voice features")
    stt_providers: list[STTProviderConfig] = Field(
        default_factory=list, description="STT providers in priority order"
    )
    stt_fallback_enabled: bool = Field(
        default=True, description="If first STT provider fails, try next in list"
    )
    max_voice_message_size: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        description="Maximum voice message size in bytes (default 10MB)",
    )
    min_voice_interval_seconds: int = Field(
        default=10,
        ge=1,
        description="Minimum seconds between voice messages from the same session",
    )
    max_voice_duration_seconds: int = Field(
        default=120,
        ge=1,
        description="Maximum recording duration in seconds",
    )


class VoiceAgentConfig(BaseModel):
    """Per-agent STT voice configuration overrides.

    All fields are optional (None = fall back to global VoiceConfig).
    """

    enabled: bool | None = Field(
        default=None, description="Override master switch for this agent"
    )
    stt_provider: str | None = Field(
        default=None,
        description="STT provider name (must match a name in VoiceConfig.stt_providers)",
    )
    stt_fallback: bool | None = Field(
        default=None, description="Override STT fallback setting for this agent"
    )
    voice_input_disabled: bool | None = Field(
        default=None,
        description="Explicitly disable voice input for this agent (hide mic in widget)",
    )


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


class AbuseConfigOverride(BaseModel):
    """Restrictive per-agent anti-abuse overrides with an explicit public contract."""

    model_config = ConfigDict(extra="forbid")

    rps: float | None = Field(default=None, gt=0)
    burst: int | None = Field(default=None, ge=1)
    max_message_length: int | None = Field(default=None, ge=1)
    min_interval_ms: int | None = Field(default=None, ge=0)
    max_user_turns_per_session: int | None = Field(default=None, ge=1)
    block_empty_user_agent: bool | None = None
    blocked_user_agents: list[str] | None = None


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
    abuse_config: AbuseConfigOverride | None = Field(
        default=None,
        description="Per-agent anti-abuse overrides; only explicit AbuseConfigOverride fields are accepted",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Per-agent system prompt override",
    )
    voice_config: VoiceAgentConfig | None = Field(
        default=None,
        description="Per-agent voice configuration overrides",
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
    abuse_config: AbuseConfigOverride | None = Field(
        default=None,
        description="Per-agent anti-abuse overrides; legacy message-based quota fields are rejected",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Per-agent system prompt override",
    )
    voice_config: VoiceAgentConfig | None = Field(
        default=None,
        description="Per-agent voice configuration overrides",
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
    abuse_config: AbuseConfigOverride | None = Field(
        default=None,
        description="Per-agent anti-abuse overrides; legacy message-based quota fields are rejected",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Per-agent system prompt override",
    )
    voice_config: VoiceAgentConfig | None = Field(
        default=None,
        description="Per-agent voice configuration overrides",
    )
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")


class AgentListResponse(BaseModel):
    """List of agents."""

    agents: list[AgentResponse] = Field(..., description="List of agents")
