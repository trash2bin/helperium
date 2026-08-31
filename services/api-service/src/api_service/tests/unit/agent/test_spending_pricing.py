"""Pre-call cost estimation for reservation admission."""

from __future__ import annotations

import pytest

from api_service.agent.pricing import (
    PricingConfigurationError,
    estimate_reservation_cost,
    model_cost_for,
)

PRICES = {
    "input_cost_per_token": 0.000002,
    "output_cost_per_token": 0.000004,
}


class TestEstimate:
    def test_estimate_uses_model_prices_and_output_cap(self) -> None:
        estimate = estimate_reservation_cost(
            model="test/model",
            messages=[{"role": "user", "content": "x" * 300}],
            max_output_tokens=1000,
            model_cost=PRICES,
        )

        assert estimate > 0
        assert estimate >= (100 * 0.000002) + (1000 * 0.000004)

    def test_longer_prompt_costs_more(self) -> None:
        short = estimate_reservation_cost(
            model="test/model",
            messages=[{"role": "user", "content": "x" * 100}],
            max_output_tokens=10,
            model_cost=PRICES,
        )
        long = estimate_reservation_cost(
            model="test/model",
            messages=[{"role": "user", "content": "x" * 10_000}],
            max_output_tokens=10,
            model_cost=PRICES,
        )

        assert long > short


class TestFailClosed:
    def test_unknown_model_pricing_fails_closed(self) -> None:
        with pytest.raises(PricingConfigurationError):
            estimate_reservation_cost(
                model="definitely-not-a-real-model/x",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=100,
            )

    def test_explicit_empty_pricing_fails_closed(self) -> None:
        with pytest.raises(PricingConfigurationError):
            estimate_reservation_cost(
                model="test/model",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=100,
                model_cost={},
            )

    def test_invalid_output_cap_fails_closed(self) -> None:
        with pytest.raises(PricingConfigurationError):
            estimate_reservation_cost(
                model="test/model",
                messages=[],
                max_output_tokens=0,
                model_cost=PRICES,
            )

    def test_free_pricing_fails_closed(self) -> None:
        """All-zero prices would reserve nothing and defeat admission."""
        with pytest.raises(PricingConfigurationError):
            estimate_reservation_cost(
                model="test/model",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=100,
                model_cost={
                    "input_cost_per_token": 0.0,
                    "output_cost_per_token": 0.0,
                },
            )

    def test_malformed_pricing_fails_closed(self) -> None:
        with pytest.raises(PricingConfigurationError):
            estimate_reservation_cost(
                model="test/model",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=100,
                model_cost={
                    "input_cost_per_token": "free",
                    "output_cost_per_token": 0.000004,
                },
            )


class TestCatalogLookup:
    def test_price_is_resolved_at_call_time(self) -> None:
        """Pricing must follow the model actually being called.

        FallbackProvider can switch upstream models mid-turn, so a per-turn
        frozen price would bill the reservation against a stale model.
        """
        import litellm

        known = next(
            name
            for name, entry in litellm.model_cost.items()
            if isinstance(entry, dict) and entry.get("input_cost_per_token")
        )

        assert model_cost_for(known) is not None
        assert model_cost_for("definitely-not-a-real-model/x") is None
        assert model_cost_for("") is None

    def test_provider_prefixed_model_falls_back_to_bare_name(self) -> None:
        import litellm

        known = next(
            name
            for name, entry in litellm.model_cost.items()
            if isinstance(entry, dict)
            and entry.get("input_cost_per_token")
            and "/" not in name
        )

        assert model_cost_for(f"someproxy/{known}") is not None
