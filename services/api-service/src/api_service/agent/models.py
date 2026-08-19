"""Typed provider contract for the minimal append-only agent loop."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class UsageInfo(BaseModel):
    """Usage reported by one provider completion."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ToolCall(BaseModel):
    """One native function call emitted by the provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CompletionRequest(BaseModel):
    """Everything a provider needs for one deterministic model step."""

    model_config = ConfigDict(frozen=True)

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    tenant_ids: list[str] = Field(default_factory=list)


class CompletionResponse(BaseModel):
    """Normalized native provider response; text is never parsed as a tool call."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: UsageInfo | None = None
    cost: float = 0.0


class ProviderConfig(BaseModel):
    """Stored configuration used to create a provider transport."""

    name: str
    model: str
    api_key: SecretStr = SecretStr("")
    api_base: str = ""
    enabled: bool = True
    priority: int = 0
    source: Literal["env", "store"] = "store"
    provider: str = ""
    label: str = ""
