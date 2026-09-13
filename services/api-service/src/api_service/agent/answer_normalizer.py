"""Provider-agnostic response-shape helpers and middleware between any LLM
adapter and the agent loop.

Some models answer with structured envelopes instead of natural language:
a fabricated ``Tool Calls: [...]`` list (native wire format echoed as text)
or a JSON object wrapping the answer (``{"answer": "..."}). Both are shape
quirks of specific models, not conversation semantics, so they are normalised
at the provider boundary — one decorator, trivially removable — while the
loop keeps generic echo/empty-round handling.

Everything implemented here is pure content parsing over the internal request
contract and must keep working when the transport underneath is swapped out.
Whether an adapter *uses* these helpers (e.g. the opt-in JSON-fence tool-call
envelope for models served through LiteLLM) and how it turns a parsed envelope
into wire-level tool-call ids remains the adapter's decision
(``litellm_provider.py``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import CompletionRequest, CompletionResponse
from .protocols import LLMProvider
from .providers.base import BaseLLMProvider

logger = logging.getLogger("api_service.agent.answer_normalizer")

# Single-key answer envelopes the middleware is allowed to unwrap. Keys
# outside this set keep the content verbatim: data-shaped JSON (product
# lists, user-requested JSON) is a legitimate final answer.
ANSWER_ENVELOPE_KEYS = frozenset({"answer", "text", "response", "message"})

_MAX_UNWRAP_DEPTH = 3


def _strip_code_fence(candidate: str) -> str:
    if candidate.startswith("```json") and candidate.endswith("```"):
        return candidate[len("```json") : -len("```")].strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        return candidate[3:-3].strip()
    return candidate


def is_tool_call_markup(final_text: str) -> bool:
    """Whether the text is a fabricated tool-call envelope, not an answer.

    Structural and provider-agnostic: matches only content whose entire
    body parses as a tool-call envelope (a single call object or a list of
    them, optionally fenced or prefixed with a ``Tool Calls:`` label).
    """
    candidate = _strip_code_fence(final_text.strip())
    if candidate.lower().startswith("tool calls:"):
        candidate = candidate[len("tool calls:") :].strip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(parsed, list):
        return bool(parsed) and all(_tool_call_shaped(item) for item in parsed)
    return _tool_call_shaped(parsed)


def _tool_call_shaped(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    function = item.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return True
    return isinstance(item.get("name"), str) and isinstance(item.get("arguments"), dict)


def unwrap_answer_envelope(content: str) -> str | None:
    """Return the human-readable answer inside a single-key JSON envelope.

    ``json.loads`` also reverses unicode escaping the model may have baked
    into the string. Returns ``None`` when the content is not an envelope
    (including data-shaped JSON with several keys) — the caller must keep
    such content verbatim.
    """
    candidate = _strip_code_fence(content.strip())
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    result: Any = parsed
    for _ in range(_MAX_UNWRAP_DEPTH):
        if (
            isinstance(result, dict)
            and len(result) == 1
            and next(iter(result)) in ANSWER_ENVELOPE_KEYS
        ):
            result = next(iter(result.values()))
            continue
        break
    return result if isinstance(result, str) and result.strip() else None


class AnswerNormalizer(BaseLLMProvider):
    """Decorating middleware implementing the LLMProvider protocol.

    - A content body that is entirely a fabricated tool-call envelope is
      rewritten to an empty content: the agent loop already treats empty
      rounds as "did not answer" (regenerate, then the polite fallback), so
      the markup can never become the user-facing final answer.
    - A single-key answer envelope (``{"answer": "..."}``) is unwrapped to
      the inner string; the model did answer, it just wrapped the text.
    Everything else — native tool calls, data-shaped JSON, plain text —
    passes through untouched.
    """

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attribute reads (``model``, identity fields) to
        the wrapped provider, so the wrapper stays transparent over the
        whole provider identity surface."""
        return getattr(self.inner, name)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        response = await self.inner.complete(request)
        if response.tool_calls or not response.content.strip():
            return response

        text = response.content.strip()
        if is_tool_call_markup(text):
            logger.warning(
                "[LLM] completion content looks like a fabricated tool-call "
                "envelope; replacing with an empty round model=%s",
                self.inner.model,
            )
            return response.model_copy(update={"content": ""})

        unwrapped = unwrap_answer_envelope(text)
        if unwrapped is not None and unwrapped != text:
            logger.info(
                "[LLM] unwrapped single-key answer envelope model=%s",
                self.inner.model,
            )
            return response.model_copy(update={"content": unwrapped})
        return response
