"""Conservative pre-call LLM cost estimation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import litellm


class PricingConfigurationError(ValueError):
    """Raised when a safe pre-call estimate cannot be calculated."""


def model_cost_for(model: str) -> Mapping[str, Any] | None:
    """Look up per-token pricing for one model at call time.

    Resolved on every reservation rather than once per turn: ``FallbackProvider``
    can switch the upstream model mid-turn, and a frozen price would then bill
    the reservation against a model that is no longer being called.
    """
    if not model:
        return None
    catalog = getattr(litellm, "model_cost", None)
    if not isinstance(catalog, Mapping):
        return None
    entry = catalog.get(model)
    if entry is None and "/" in model:
        # LiteLLM catalogs some models without their provider prefix.
        entry = catalog.get(model.split("/", 1)[1])
    return entry if isinstance(entry, Mapping) else None


def _conservative_input_tokens(messages: list[dict[str, Any]]) -> int:
    """Approximate prompt tokens from serialized transcript length.

    KNOWN LIMITATION (tracked for the estimate-accuracy step): a fixed
    characters-per-token ratio is not a guaranteed upper bound. Cyrillic text
    mixed with part numbers and emoji tokenize denser than 3 chars/token, and
    the tool schemas sent with the request are not counted here at all. The
    reservation flag must stay off until this is replaced with a real tokenizer
    count over messages *and* tools.
    """
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(serialized) / 3))


def estimate_reservation_cost(
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int,
    model_cost: Mapping[str, Any] | None = None,
) -> float:
    """Return a conservative USD upper bound for one provider completion.

    ``model_cost`` overrides catalog lookup (used by tests and by stored model
    configuration). When omitted, pricing is resolved from LiteLLM for the model
    being called right now. Unknown or invalid pricing fails closed: reserving
    zero would defeat budget admission.
    """
    resolved = model_cost if model_cost is not None else model_cost_for(model)
    if not model or max_output_tokens <= 0 or not resolved:
        raise PricingConfigurationError(f"missing pricing configuration for {model!r}")
    try:
        input_price = float(resolved["input_cost_per_token"])
        output_price = float(resolved["output_cost_per_token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PricingConfigurationError(
            f"invalid pricing configuration for {model!r}"
        ) from exc
    if input_price < 0 or output_price < 0 or (input_price == 0 and output_price == 0):
        raise PricingConfigurationError(
            f"non-positive pricing configuration for {model!r}"
        )
    input_tokens = _conservative_input_tokens(messages)
    return (input_tokens * input_price) + (max_output_tokens * output_price)
