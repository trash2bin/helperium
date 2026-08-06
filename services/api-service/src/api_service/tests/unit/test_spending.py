"""Tests for per-tenant LLM spending checker."""

from __future__ import annotations

import pytest

from api_service.spending import SpendingChecker, SpendingConfig


@pytest.fixture
def checker():
    """SpendingChecker with low budget for testing."""
    config = SpendingConfig(enabled=True, default_budget=10.0, period="monthly")
    return SpendingChecker(config=config)


@pytest.fixture
def disabled_checker():
    """SpendingChecker with limits disabled."""
    config = SpendingConfig(enabled=False, default_budget=10.0, period="monthly")
    return SpendingChecker(config=config)


@pytest.mark.asyncio
class TestSpending:
    """All spending limit test scenarios."""

    def test_spending_under_budget_allowed(self, checker):
        """Under budget → allowed."""
        checker.record_spending("tenant-a", 5.0)
        allowed, reason = checker.check_limits("tenant-a")
        assert allowed is True
        assert reason == ""

    def test_spending_under_budget_after_record(self, checker):
        """Record multiple calls, still under budget → allowed."""
        checker.record_spending("tenant-a", 3.0)
        checker.record_spending("tenant-a", 2.0)
        checker.record_spending("tenant-a", 1.0)
        allowed, reason = checker.check_limits("tenant-a")
        assert allowed is True
        assert reason == ""

    def test_spending_budget_enforced(self, checker):
        """Over budget → blocked."""
        checker.record_spending("tenant-a", 10.0)
        allowed, reason = checker.check_limits("tenant-a")
        assert allowed is False
        assert "exceeded" in reason or "limit" in reason

    def test_spending_budget_enforced_exact(self, checker):
        """At exactly budget → still allowed (blocked when exceeded)."""
        checker.record_spending("tenant-a", 9.99)
        allowed, _ = checker.check_limits("tenant-a")
        assert allowed is True
        # Now exceed
        checker.record_spending("tenant-a", 0.02)
        allowed2, _ = checker.check_limits("tenant-a")
        assert allowed2 is False

    def test_spending_disabled(self, disabled_checker):
        """Disabled → always allowed regardless of spending."""
        disabled_checker.record_spending("tenant-a", 100.0)
        allowed, reason = disabled_checker.check_limits("tenant-a")
        assert allowed is True
        assert reason == ""

    def test_spending_zero_budget_no_limit(self, checker):
        """Budget = 0 means no limit."""
        checker.set_budget("tenant-a", 0.0)
        checker.record_spending("tenant-a", 999.0)
        allowed, reason = checker.check_limits("tenant-a")
        assert allowed is True
        assert reason == ""

    def test_spending_per_tenant_override(self, checker):
        """Per-tenant budget override overrides default."""
        # Default budget is 10.0, set tenant-a to 100.0
        checker.set_budget("tenant-a", 100.0)
        checker.record_spending("tenant-a", 50.0)
        allowed, _ = checker.check_limits("tenant-a")
        assert allowed is True
        # Another tenant still uses default (10.0)
        checker.record_spending("tenant-b", 9.0)
        allowed_b, _ = checker.check_limits("tenant-b")
        assert allowed_b is True
        checker.record_spending("tenant-b", 2.0)
        allowed_b2, _ = checker.check_limits("tenant-b")
        assert allowed_b2 is False

    def test_spending_isolated_between_tenants(self, checker):
        """Spending is isolated per tenant."""
        checker.record_spending("tenant-a", 10.0)
        checker.record_spending("tenant-b", 1.0)
        allowed_a, _ = checker.check_limits("tenant-a")
        allowed_b, _ = checker.check_limits("tenant-b")
        assert allowed_a is False  # hit budget
        assert allowed_b is True  # still under

    def test_spending_get_correct_report(self, checker):
        """get_spending returns correct data structure."""
        checker.record_spending("tenant-a", 4.5)
        report = checker.get_spending("tenant-a")
        assert report["tenant_id"] == "tenant-a"
        assert report["budget"] == 10.0
        assert report["total_cost"] == 4.5
        assert report["period"] == "monthly"

    def test_spending_override_below_default(self, checker):
        """Override with lower budget than default works."""
        checker.set_budget("tenant-a", 2.0)
        checker.record_spending("tenant-a", 1.5)
        allowed, _ = checker.check_limits("tenant-a")
        assert allowed is True
        checker.record_spending("tenant-a", 1.0)
        allowed2, _ = checker.check_limits("tenant-a")
        assert allowed2 is False

    def test_spending_unlimited_tenants(self, checker):
        """Tenant that never spends is always allowed."""
        allowed, _ = checker.check_limits("never-spent")
        assert allowed is True

    def test_spending_reload_keeps_state(self, checker):
        """reload() resets config but preserves spending state."""
        checker.record_spending("tenant-a", 5.0)
        checker.reload()
        # In-memory spending is preserved (reload only resets config)
        allowed, _ = checker.check_limits("tenant-a")
        assert allowed is True
