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


# === Voice Config Models (STT/TTS) ===


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


class TTSProviderConfig(BaseModel):
    """TTS provider configuration — one entry in the priority list."""

    name: str = Field(..., description="Display name for this provider")
    provider: str = Field(
        ...,
        pattern=r"^(litellm|local)$",
        description="Engine type: litellm (OpenAI/Azure TTS API) or local (piper-tts, coqui, OOT)",
    )
    model: str = Field(
        default="tts-1", description="Model name: tts-1, tts-1-hd, piper, coqui-xtts"
    )
    voice: str = Field(
        default="alloy",
        description="Voice name: alloy, echo, fable, onyx, nova, shimmer (OpenAI); or local voice pack name",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for cloud TTS (OpenAI, Azure, ElevenLabs, etc.)",
    )
    api_base: str | None = Field(default=None, description="Custom API base URL")
    enabled: bool = Field(default=True, description="Whether this provider is active")


class VoiceConfig(BaseModel):
    """Global voice (STT/TTS) configuration.

    Mirrors the structure of LLM provider config — a priority list of providers
    with fallback support.
    """

    enabled: bool = Field(default=True, description="Master switch for voice features")
    stt_providers: list[STTProviderConfig] = Field(
        default_factory=list, description="STT providers in priority order"
    )
    tts_providers: list[TTSProviderConfig] = Field(
        default_factory=list, description="TTS providers in priority order"
    )
    stt_fallback_enabled: bool = Field(
        default=True, description="If first STT provider fails, try next in list"
    )
    tts_fallback_enabled: bool = Field(
        default=True, description="If first TTS provider fails, try next in list"
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
    """Per-agent voice configuration overrides.

    All fields are optional (None = fall back to global VoiceConfig).
    """

    enabled: bool | None = Field(
        default=None, description="Override master switch for this agent"
    )
    stt_provider: str | None = Field(
        default=None,
        description="STT provider name (must match a name in VoiceConfig.stt_providers)",
    )
    tts_provider: str | None = Field(
        default=None,
        description="TTS provider name (must match a name in VoiceConfig.tts_providers)",
    )
    stt_fallback: bool | None = Field(
        default=None, description="Override STT fallback setting for this agent"
    )
    tts_fallback: bool | None = Field(
        default=None, description="Override TTS fallback setting for this agent"
    )
    voice_input_disabled: bool | None = Field(
        default=None,
        description="Explicitly disable voice input for this agent (hide mic in widget)",
    )
    voice_output_disabled: bool | None = Field(
        default=None,
        description="Explicitly disable TTS for this agent",
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
    abuse_config: dict | None = Field(
        default=None,
        description="Per-agent abuse settings overrides (keys match AbuseConfig fields)",
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
    abuse_config: dict | None = Field(
        default=None,
        description="Per-agent abuse settings overrides",
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
    abuse_config: dict | None = Field(
        default=None,
        description="Per-agent abuse settings overrides",
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
