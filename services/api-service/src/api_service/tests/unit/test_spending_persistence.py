"""TDD tests: SpendingChecker теряет spending records при перезапуске.

Проблема: SpendingChecker хранит _spending и _overrides только в памяти.
При создании нового экземпляра (симуляция рестарта api-service) все данные
теряются. Клиент может сжечь лимит до восстановления бюджета.

Текущее поведение (баг): новый экземпляр не видит spending records старого.
Ожидаемое поведение (фикс): spending records и budget overrides должны
переживать перезапуск (персистентность).

Тесты ПАДАЮТ пока фикс не внедрён — это TDD-контракт.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


from api_service.spending import SpendingChecker, SpendingConfig


class TestSpendingPersistenceOnRestart:
    """Spending records должны переживать перезапуск сервиса."""

    def test_spending_survives_restart_with_persistence(self):
        """Spending records ВЫЖИВАЮТ перезапуск при использовании persistence_path."""

        tenant = "tenant-spending-restart"
        budget = 10.0

        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "spending.json"

            # ── Instance A (original) ──────────────────────────────────
            checker_a = SpendingChecker(
                SpendingConfig(enabled=True, default_budget=budget),
                persistence_path=persist_path,
            )
            checker_a.record_spending(tenant, 5.0)
            spending_a = checker_a.get_spending(tenant)

            assert spending_a["total_cost"] == 5.0, (
                f"Instance A должен показать $5.00, показал ${spending_a['total_cost']}"
            )
            assert spending_a["budget"] == budget, (
                f"Instance A budget должен быть {budget}, показал {spending_a['budget']}"
            )
            assert persist_path.exists(), (
                "persistence_path должен создавать файл после record_spending"
            )

            # ── Instance B (simulates restart) ─────────────────────────
            checker_b = SpendingChecker(
                SpendingConfig(enabled=True, default_budget=budget),
                persistence_path=persist_path,
            )
            spending_b = checker_b.get_spending(tenant)

            # ⚡ TDD-контракт: при persistence_path spending выживает
            assert spending_b["total_cost"] == 5.0, (
                f"\n\n❌ TDD FAIL: Spending records ПОТЕРЯНЫ при рестарте.\n"
                f"Instance A: tenant={tenant}, total_cost={spending_a['total_cost']}\n"
                f"Instance B (restart with same persistence_path): "
                f"tenant={tenant}, total_cost={spending_b['total_cost']}\n"
                f"Spending должен загружаться из persistence_path при создании."
            )


class TestBudgetOverridesPersistence:
    """Per-tenant budget overrides должны переживать перезапуск."""

    def test_budget_overrides_survive_restart_with_persistence(self):
        """Budget overrides ВЫЖИВАЮТ перезапуск при использовании persistence_path."""

        tenant = "tenant-budget-restart"
        custom_budget = 100.0
        default_budget = 50.0

        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "spending.json"

            # ── Instance A (original) ──────────────────────────────────
            checker_a = SpendingChecker(
                SpendingConfig(enabled=True, default_budget=default_budget),
                persistence_path=persist_path,
            )
            checker_a.set_budget(tenant, custom_budget)
            budget_a = checker_a.get_budget(tenant)

            assert budget_a == custom_budget, (
                f"Instance A: get_budget({tenant}) = {budget_a}, expected {custom_budget}"
            )
            assert persist_path.exists(), (
                "persistence_path должен создавать файл после set_budget"
            )

            # ── Instance B (simulates restart) ─────────────────────────
            checker_b = SpendingChecker(
                SpendingConfig(enabled=True, default_budget=default_budget),
                persistence_path=persist_path,
            )
            budget_b = checker_b.get_budget(tenant)

            # ⚡ TDD-контракт: при persistence_path overrides выживают
            assert budget_b == custom_budget, (
                f"\n\n❌ TDD FAIL: Budget overrides ПОТЕРЯНЫ при рестарте.\n"
                f"Instance A set_budget({tenant}, {custom_budget})\n"
                f"Instance B (restart with same persistence_path): "
                f"get_budget({tenant}) = {budget_b} (default: {default_budget})\n"
                f"Budget overrides должны загружаться из persistence_path."
            )


class TestSpendingPersistenceFile:
    """Проверка что фикс может использовать файл для персистентности.

    Этот тест НЕ падает — он демонстрирует КАК должен работать
    персистентный SpendingChecker (target design).
    """

    def test_persistence_file_format(self):
        """SpendingChecker.save() должен писать корректный JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "spending.json"

            checker = SpendingChecker(
                SpendingConfig(enabled=True),
                persistence_path=persist_path,
            )
            checker.record_spending("tenant-1", 3.5)
            checker.set_budget("tenant-2", 100.0)

            # Read the file directly
            raw = json.loads(persist_path.read_text(encoding="utf-8"))

            assert "spending" in raw, "JSON должен содержать 'spending'"
            assert "overrides" in raw, "JSON должен содержать 'overrides'"
            assert raw["spending"].get("tenant-1") == 3.5, (
                f"spending.tenant-1 должно быть 3.5, получено {raw['spending'].get('tenant-1')}"
            )
            assert raw["overrides"].get("tenant-2") == 100.0, (
                f"overrides.tenant-2 должно быть 100.0, получено {raw['overrides'].get('tenant-2')}"
            )

    def test_spending_middleware_still_works_demo(self):
        """Демо: SpendingMiddleware должен корректно работать с персистентностью.

        Проверка что интерфейс record/check_limits не меняется.
        """
        checker = SpendingChecker(SpendingConfig(enabled=True, default_budget=10.0))

        # Симулируем normal flow
        checker.record_spending("tenant-demo", 2.0)
        allowed, reason = checker.check_limits("tenant-demo")
        assert allowed, f"After $2.0 with $10 budget: should be allowed, got {reason}"

        checker.record_spending("tenant-demo", 8.0)
        allowed, reason = checker.check_limits("tenant-demo")
        assert not allowed, (
            f"After $10 total with $10 budget: should be blocked, got '{reason}'"
        )
