"""HTTP DTO for the Agent API service.

These models define the HTTP contract between the Web frontend and the
Agent API service (``demo/api/server.py``). They are used both by FastAPI
(for automatic OpenAPI generation) and by external consumers.

Internal agent types (TypedDicts) live in ``demo.api.agent.types`` —
they are not part of the HTTP contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# === Requests ===


class ChatRequest(BaseModel):
    """Request to start or continue a chat."""

    message: str = Field(
        ..., min_length=1, description="Text message from the user"
    )
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

    records: list[BacklogEvent] = Field(
        ..., description="List of session events"
    )
    session_id: str = Field(..., description="Requested session ID")
    count: int = Field(..., description="Number of records")


class ChatMessage(BaseModel):
    """A single message in chat history."""

    role: str = Field(..., description="Role: user / assistant / system")
    content: str = Field(default="", description="Message text")
    tool_calls: list[dict] | None = Field(
        default=None, description="Tool calls"
    )


class SessionHistoryResponse(BaseModel):
    """Chat message history for a session."""

    messages: list[ChatMessage] = Field(
        ..., description="Messages (role, content, tool_calls)"
    )
