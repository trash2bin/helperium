"""Admission-ledger contract: money units, unlimited budgets, expiry, isolation."""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from api_service.spending import (
    MICROS_PER_USD,
    BudgetExceeded,
    ReservationConflict,
    SQLiteSpendingLedger,
    budget_usd_to_micros,
    usd_to_micros,
)


def make_ledger(
    tmp_path: Path,
    budget_micros: int | None = 1_000,
    ttl_seconds: float = 1800.0,
) -> SQLiteSpendingLedger:
    return SQLiteSpendingLedger(
        tmp_path / "spending-ledger.sqlite3",
        default_budget_micros=budget_micros,
        reservation_ttl_seconds=ttl_seconds,
    )


# ── Money units ──────────────────────────────────────────────────────────────


class TestMoneyUnits:
    """Sub-cent turn costs must survive conversion, or admission is inert."""

    def test_realistic_subcent_turn_is_not_rounded_away(self) -> None:
        # ~$0.0019 is a normal small turn; in cents this rounded to 0.
        assert usd_to_micros(0.0019) == 1900
        assert usd_to_micros(0.0000004) == 1

    def test_conversion_rounds_up_so_cost_is_never_understated(self) -> None:
        assert usd_to_micros(1.0000001) == MICROS_PER_USD + 1

    def test_non_positive_cost_is_zero(self) -> None:
        assert usd_to_micros(0.0) == 0
        assert usd_to_micros(-1.0) == 0

    def test_unlimited_budget_stays_unlimited(self) -> None:
        """SpendingChecker treats budget<=0 as no limit; the ledger must agree."""
        assert budget_usd_to_micros(0.0) is None
        assert budget_usd_to_micros(-5.0) is None
        assert budget_usd_to_micros(50.0) == 50 * MICROS_PER_USD

    def test_subcent_turns_accumulate_towards_the_budget(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=usd_to_micros(0.01))
        for index in range(5):
            request_id = f"turn-{index}"
            ledger.reserve("agent-a", request_id, usd_to_micros(0.0019), ["tenant-a"])
            ledger.commit(request_id, usd_to_micros(0.0019))
        assert ledger.balance("agent-a").committed_micros == 9500
        with pytest.raises(BudgetExceeded):
            ledger.reserve("agent-a", "turn-final", usd_to_micros(0.0019), ["tenant-a"])


# ── Admission ────────────────────────────────────────────────────────────────


class TestAdmission:
    def test_reserve_is_atomic_under_concurrency(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=1_000)

        def reserve(index: int) -> str:
            reservation = ledger.reserve(
                principal_id="agent-a",
                request_id=f"turn-{index}",
                estimated_cost_micros=600,
                tenant_ids=["tenant-a", "tenant-b"],
            )
            return reservation.request_id

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(reserve, index) for index in range(20)]
        results = []
        rejected = 0
        for future in futures:
            try:
                results.append(future.result())
            except BudgetExceeded:
                rejected += 1

        assert len(results) == 1
        assert rejected == 19
        assert ledger.balance("agent-a").reserved_micros == 600

    def test_duplicate_reserve_does_not_double_count(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path)

        first = ledger.reserve("agent-a", "turn-1", 400, ["tenant-a"])
        second = ledger.reserve("agent-a", "turn-1", 400, ["tenant-a"])

        assert second == first
        assert ledger.balance("agent-a").reserved_micros == 400

    def test_duplicate_request_id_with_other_amount_conflicts(
        self, tmp_path: Path
    ) -> None:
        ledger = make_ledger(tmp_path)
        ledger.reserve("agent-a", "turn-1", 400, ["tenant-a"])

        with pytest.raises(ReservationConflict):
            ledger.reserve("agent-a", "turn-1", 401, ["tenant-a"])

    def test_budget_exceeded_is_fail_closed(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=100)
        ledger.reserve("agent-a", "turn-1", 100, ["tenant-a"])

        with pytest.raises(BudgetExceeded):
            ledger.reserve("agent-a", "turn-2", 1, ["tenant-a"])

    def test_unlimited_principal_is_never_refused(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=None)

        ledger.reserve("agent-a", "turn-1", 10**12, ["tenant-a"])

        balance = ledger.balance("agent-a")
        assert balance.unlimited is True
        assert balance.available_micros is None

    def test_explicit_budget_overrides_the_default(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=100)
        ledger.set_budget("agent-a", 10_000)

        ledger.reserve("agent-a", "turn-1", 9_000, ["tenant-a"])

        assert ledger.balance("agent-a").budget_micros == 10_000

    def test_budget_can_be_set_to_unlimited(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=100)
        ledger.set_budget("agent-a", None)

        ledger.reserve("agent-a", "turn-1", 10**9, ["tenant-a"])

        assert ledger.balance("agent-a").unlimited is True

    def test_default_budget_change_applies_to_existing_principals(
        self, tmp_path: Path
    ) -> None:
        """A default-budget reload must reach principals created earlier.

        The reload only mutates the in-memory default; the balance row is
        created on first reserve. If the row never followed the new default,
        an existing principal would silently keep the old budget forever —
        e.g. tightening to a lower cap would not take effect.
        """
        ledger = make_ledger(tmp_path, budget_micros=1_000)
        ledger.reserve("agent-a", "turn-1", 10, ["tenant-a"])

        # Operator reload: default budget is now unlimited.
        ledger.default_budget_micros = None
        ledger.reserve("agent-a", "turn-2", 10_000, ["tenant-a"])
        assert ledger.balance("agent-a").unlimited is True

        # And tightening applies too, but never over an explicit override.
        ledger.default_budget_micros = 500
        ledger.set_budget("agent-b", 9_000)
        ledger.reserve("agent-b", "turn-3", 10, ["tenant-a"])
        ledger.reserve("agent-c", "turn-4", 10, ["tenant-a"])
        ledger.default_budget_micros = 100
        ledger.reserve("agent-b", "turn-5", 8_000, ["tenant-a"])
        ledger.reserve("agent-c", "turn-6", 90, ["tenant-a"])
        assert ledger.balance("agent-b").budget_micros == 9_000
        assert ledger.balance("agent-c").budget_micros == 100

    def test_principals_do_not_share_capacity(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=100)
        ledger.reserve("agent-a", "turn-a", 100, ["tenant-shared"])

        ledger.reserve("agent-b", "turn-b", 100, ["tenant-shared"])

        assert ledger.balance("agent-a").reserved_micros == 100
        assert ledger.balance("agent-b").reserved_micros == 100

    def test_composite_scope_is_charged_once(self, tmp_path: Path) -> None:
        """One composite turn = one charge, with all tenants kept as dimensions."""
        ledger = make_ledger(tmp_path, budget_micros=1_000)
        reservation = ledger.reserve(
            "agent-a", "turn-1", 300, ["tenant-a", "tenant-b", "tenant-c"]
        )
        ledger.commit("turn-1", 300)

        assert reservation.tenant_ids == ("tenant-a", "tenant-b", "tenant-c")
        assert ledger.balance("agent-a").committed_micros == 300


# ── Commit / release / expiry ────────────────────────────────────────────────


class TestSettlement:
    def test_commit_is_idempotent_and_releases_unused_estimate(
        self, tmp_path: Path
    ) -> None:
        ledger = make_ledger(tmp_path)
        ledger.reserve("agent-a", "turn-1", 400, ["tenant-a"])

        ledger.commit("turn-1", actual_cost_micros=250)
        ledger.commit("turn-1", actual_cost_micros=250)

        balance = ledger.balance("agent-a")
        assert balance.reserved_micros == 0
        assert balance.committed_micros == 250

    def test_commit_may_exceed_the_estimate(self, tmp_path: Path) -> None:
        """Realized cost is authoritative even when the estimate was too low."""
        ledger = make_ledger(tmp_path, budget_micros=1_000)
        ledger.reserve("agent-a", "turn-1", 100, ["tenant-a"])

        ledger.commit("turn-1", 900)

        assert ledger.balance("agent-a").committed_micros == 900

    def test_release_returns_capacity(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path)
        ledger.reserve("agent-a", "turn-1", 700, ["tenant-a"])

        ledger.release("turn-1")

        assert ledger.balance("agent-a").reserved_micros == 0
        assert (
            ledger.reserve("agent-a", "turn-2", 1_000, ["tenant-a"]).request_id
            == "turn-2"
        )

    def test_release_is_idempotent_and_ignores_unknown_ids(
        self, tmp_path: Path
    ) -> None:
        ledger = make_ledger(tmp_path)
        ledger.reserve("agent-a", "turn-1", 700, ["tenant-a"])

        ledger.release("turn-1")
        ledger.release("turn-1")
        ledger.release("never-existed")

        assert ledger.balance("agent-a").reserved_micros == 0

    def test_expired_reservation_frees_capacity(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path, budget_micros=1_000, ttl_seconds=0.01)
        ledger.reserve("agent-a", "turn-1", 900, ["tenant-a"])
        time.sleep(0.02)

        ledger.expire_stale()

        assert ledger.balance("agent-a").reserved_micros == 0

    def test_expired_reservation_is_still_committed(self, tmp_path: Path) -> None:
        """The provider call already happened, so the charge must not be lost."""
        ledger = make_ledger(tmp_path, budget_micros=1_000, ttl_seconds=0.01)
        ledger.reserve("agent-a", "turn-1", 900, ["tenant-a"])
        time.sleep(0.02)
        ledger.expire_stale()

        ledger.commit("turn-1", 850)

        balance = ledger.balance("agent-a")
        assert balance.committed_micros == 850
        assert balance.reserved_micros == 0

    def test_commit_after_release_conflicts(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path)
        ledger.reserve("agent-a", "turn-1", 400, ["tenant-a"])
        ledger.release("turn-1")

        with pytest.raises(ReservationConflict):
            ledger.commit("turn-1", 400)

    def test_commit_of_unknown_reservation_conflicts(self, tmp_path: Path) -> None:
        ledger = make_ledger(tmp_path)

        with pytest.raises(ReservationConflict):
            ledger.commit("never-reserved", 100)


# ── Storage hygiene ──────────────────────────────────────────────────────────


class TestStorage:
    def test_state_survives_a_new_ledger_instance(self, tmp_path: Path) -> None:
        make_ledger(tmp_path).reserve("agent-a", "turn-1", 400, ["tenant-a"])

        assert make_ledger(tmp_path).balance("agent-a").reserved_micros == 400

    def test_retired_cents_schema_is_rejected(self, tmp_path: Path) -> None:
        """A pre-micro-USD file must not be silently reinterpreted as micros."""
        path = tmp_path / "spending-ledger.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE spending_balances ("
                "principal_id TEXT PRIMARY KEY, budget_cents INTEGER NOT NULL, "
                "committed_cents INTEGER NOT NULL DEFAULT 0, "
                "reserved_cents INTEGER NOT NULL DEFAULT 0)"
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(RuntimeError, match="retired cents schema"):
            SQLiteSpendingLedger(path, default_budget_micros=1_000)

    def test_invalid_construction_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            SQLiteSpendingLedger(tmp_path / "a.sqlite3", default_budget_micros=-1)
        with pytest.raises(ValueError):
            SQLiteSpendingLedger(
                tmp_path / "b.sqlite3",
                default_budget_micros=1,
                reservation_ttl_seconds=0,
            )

    def test_ledger_does_not_leak_connections(self, tmp_path: Path) -> None:
        """Every ledger call must close its handle, not wait for GC."""
        import gc

        ledger = make_ledger(tmp_path, budget_micros=10**9)
        gc.collect()
        before = sum(
            1 for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)
        )

        for index in range(30):
            request_id = f"turn-{index}"
            ledger.reserve("agent-a", request_id, 10, ["tenant-a"])
            ledger.commit(request_id, 10)
            ledger.balance("agent-a")

        gc.collect()
        after = sum(
            1 for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)
        )
        assert after <= before
