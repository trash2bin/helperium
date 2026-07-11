"""Model pricing configuration for LLM cost tracking.

Prices in USD per 1M tokens (input, output).
Override via LLM_PRICING_OVERRIDES env var (JSON dict of model -> [input_price, output_price]).
"""

from __future__ import annotations

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default prices (USD per 1M tokens)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ── OpenAI ──────────────────────────────────────────────────────
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4o-2024-08-06": (2.50, 10.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o-mini-2024-07-18": (0.15, 0.60),
    "openai/o1": (15.00, 60.00),
    "openai/o1-mini": (1.10, 4.40),
    "openai/o3-mini": (1.10, 4.40),
    "openai/gpt-4-turbo": (10.00, 30.00),
    "openai/gpt-3.5-turbo": (0.50, 1.50),
    # ── Anthropic ───────────────────────────────────────────────────
    "anthropic/claude-sonnet-4-20250514": (3.00, 15.00),
    "anthropic/claude-3-5-sonnet-20241022": (3.00, 15.00),
    "anthropic/claude-3-opus-20240229": (15.00, 75.00),
    "anthropic/claude-3-haiku-20240307": (0.25, 1.25),
    "anthropic/claude-instant-1.2": (0.80, 4.00),
    # ── Mistral ─────────────────────────────────────────────────────
    "mistral/mistral-large-latest": (2.00, 6.00),
    "mistral/mistral-small-latest": (1.00, 3.00),
    "mistral/mistral-7b-instruct": (0.25, 0.25),
    # ── Google Gemini ───────────────────────────────────────────────
    "gemini/gemini-2.5-pro-preview-05-06": (1.25, 10.00),
    "gemini/gemini-2.0-flash": (0.10, 0.40),
    "gemini/gemini-1.5-pro": (1.25, 5.00),
    "gemini/gemini-1.5-flash": (0.075, 0.30),
    # ── Perplexity ──────────────────────────────────────────────────
    "perplexity/sonar-pro": (3.00, 15.00),
    "perplexity/sonar": (1.00, 1.00),
    # ── DeepSeek ────────────────────────────────────────────────────
    "deepseek/deepseek-chat": (0.27, 1.10),
    "deepseek/deepseek-reasoner": (0.55, 2.19),
    # ── Ollama (local, free) ────────────────────────────────────────
    "ollama/llama3.2": (0.0, 0.0),
    "ollama/mistral": (0.0, 0.0),
    "ollama/llama3.1": (0.0, 0.0),
    "ollama/qwen2.5": (0.0, 0.0),
    # ── Generic fallback key ────────────────────────────────────────
    "__default__": (1.00, 3.00),
}


def load_model_pricing() -> dict[str, tuple[float, float]]:
    """Load pricing, applying env overrides if present."""
    pricing = dict(MODEL_PRICING)
    overrides = os.environ.get("LLM_PRICING_OVERRIDES")
    if overrides:
        try:
            parsed = json.loads(overrides)
            for model, prices in parsed.items():
                input_price, output_price = prices
                pricing[model] = (float(input_price), float(output_price))
            logger.info("Loaded LLM_PRICING_OVERRIDES for %d models", len(parsed))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Failed to parse LLM_PRICING_OVERRIDES: %s", e)
    return pricing


_cached_pricing: dict[str, tuple[float, float]] = load_model_pricing()


def reload_pricing() -> None:
    """Reload pricing from env. Call after changing LLM_PRICING_OVERRIDES at runtime."""
    global _cached_pricing
    _cached_pricing = load_model_pricing()


def get_model_cost(
    model: str, prompt_tokens: int, completion_tokens: int
) -> Optional[float]:
    """Calculate cost for a model call. Returns None if model not found.

    Tries exact match first, then falls back to prefix match (e.g. 'openai/gpt-4o'
    matches pricing for 'openai/gpt-4o-2024-08-06'), then falls back to
    '__default__'.
    """
    pricing = _cached_pricing

    input_price = 0.0
    output_price = 0.0

    # 1. Exact match
    if model in pricing:
        input_price, output_price = pricing[model]
    else:
        # 2. Prefix match: look for a shorter key that is a prefix of model
        matched = False
        for key, (ip, op) in pricing.items():
            if key != "__default__" and model.startswith(key):
                input_price, output_price = ip, op
                matched = True
                break
        if not matched:
            # 3. Fallback to default
            if "__default__" in pricing:
                logger.warning(
                    "No pricing found for model '%s', using default ($1.0/3.0 per 1M)",
                    model,
                )
                input_price, output_price = pricing["__default__"]
            else:
                return None

    cost = (prompt_tokens * input_price / 1_000_000) + (
        completion_tokens * output_price / 1_000_000
    )
    return cost if cost > 0 else 0.0
