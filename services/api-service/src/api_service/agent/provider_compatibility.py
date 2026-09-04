"""Declarative, verified provider/model wire compatibility policies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ReasoningBodyFactory = Callable[[bool], dict[str, Any]]


@dataclass(frozen=True)
class ProviderModelPolicy:
    """Wire behavior verified for one provider/model prefix.

    The policy is intentionally internal. It is not an agent configuration field:
    changing it means validating a provider/model integration and adding a
    regression, rather than allowing a persisted record to guess wire behavior.
    """

    provider: str
    model_prefix: str
    reasoning_body: ReasoningBodyFactory | None = None
    keep_tool_schemas_on_continuation: bool = False
    parse_text_tool_calls: bool = False


def _step37_reasoning_body(enabled: bool) -> dict[str, Any]:
    return {"chat_template_kwargs": {"thinking": enabled}}


_POLICIES: tuple[ProviderModelPolicy, ...] = (
    ProviderModelPolicy(
        provider="nvidia_nim",
        model_prefix="nvidia_nim/stepfun-ai/step-3.7-flash",
        reasoning_body=_step37_reasoning_body,
        keep_tool_schemas_on_continuation=True,
    ),
    # OpenAI-compatible passthrough relays (api_base overrides) serve models
    # litellm cannot resolve in its OpenAI registry, so
    # litellm.supports_function_calling() false-negatives and the completion
    # policy strips tool schemas on unresolved tool continuations. The wire
    # itself verifies native function calling (finish_reason=tool_calls), so
    # keep the schemas and let the model finish the tool cycle.
    ProviderModelPolicy(
        provider="openai",
        model_prefix="openai/deepseek",
        keep_tool_schemas_on_continuation=True,
    ),
    # Ollama's registry currently reports false for this cloud model even
    # though its live wire response includes native tool_calls.  Stripping the
    # schemas after the first call makes the next model step fall back to
    # visible JSON/text instead of continuing the MCP cycle.
    ProviderModelPolicy(
        provider="ollama",
        model_prefix="gemma4:31b-cloud",
        keep_tool_schemas_on_continuation=True,
        parse_text_tool_calls=True,
    ),
)


def find_provider_model_policy(
    provider: str | None, model: str
) -> ProviderModelPolicy | None:
    """Return the verified policy for the provider/model pair, if one exists."""
    if provider is None:
        return None
    for policy in _POLICIES:
        if provider == policy.provider and model.startswith(policy.model_prefix):
            return policy
    return None
