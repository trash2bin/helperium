"""Per-tenant LLM spending tracking and limits.

Tracks spending in-memory and enforces hard limits.
Configurable via env vars and admin API.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SpendingConfig:
    """Spending limit configuration."""

    enabled: bool = True
    default_budget: float = 50.0  # USD per period
    period: str = "monthly"  # daily | weekly | monthly

    @classmethod
    def from_env(cls) -> SpendingConfig:
        import os

        return cls(
            enabled=os.environ.get("SPENDING_LIMIT_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            default_budget=float(os.environ.get("SPENDING_DEFAULT_BUDGET", "50.0")),
            period=os.environ.get("SPENDING_BUDGET_PERIOD", "monthly"),
        )


class SpendingChecker:
    """Check and enforce per-tenant LLM spending limits.

    Stores spending in memory AND persists to a JSON file when
    ``persistence_path`` is provided.  Each mutation (record_spending,
    set_budget) triggers an atomic write to that file, so spending
    records survive service restarts.
    """

    def __init__(
        self,
        config: SpendingConfig | None = None,
        persistence_path: str | Path | None = None,
    ):
        self.config = config or SpendingConfig.from_env()
        # Per-tenant budget overrides: tenant_id -> budget_usd
        self._overrides: dict[str, float] = {}
        # Per-tenant spending: tenant_id -> total_cost
        self._spending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._persistence_path: Path | None = (
            Path(persistence_path) if persistence_path else None
        )

        # Restore state from persistence file on construction (survives restart)
        if self._persistence_path and self._persistence_path.exists():
            self._load_from(self._persistence_path)

    def reload(self) -> None:
        """Reload config from env."""
        self.config = SpendingConfig.from_env()

    def get_budget(self, tenant_id: str) -> float:
        """Get budget for a tenant."""
        with self._lock:
            if tenant_id in self._overrides:
                return self._overrides[tenant_id]
        return self.config.default_budget

    def set_budget(self, tenant_id: str, budget: float) -> None:
        """Set per-tenant budget override."""
        with self._lock:
            self._overrides[tenant_id] = budget
        self._persist()

    def record_spending(self, tenant_id: str, cost: float) -> None:
        """Record an LLM call cost for a tenant."""
        with self._lock:
            current = self._spending.get(tenant_id, 0.0)
            self._spending[tenant_id] = current + cost
        self._persist()

    def get_spending(self, tenant_id: str) -> dict:
        """Get current spending for a tenant."""
        with self._lock:
            total = self._spending.get(tenant_id, 0.0)
        return {
            "tenant_id": tenant_id,
            "budget": self.get_budget(tenant_id),
            "total_cost": round(total, 4),
            "period": self.config.period,
        }

    def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        """Check if tenant has exceeded spending limit.

        Returns (allowed: bool, reason: str).
        If allowed=True, the LLM call can proceed.
        If allowed=False, the call should be blocked.
        """
        if not self.config.enabled:
            return True, ""

        budget = self.get_budget(tenant_id)
        if budget <= 0:
            # 0 means no limit
            return True, ""

        with self._lock:
            spent = self._spending.get(tenant_id, 0.0)

        if spent >= budget:
            logger.warning(
                "[SPENDING] Tenant %s exceeded budget: $%.2f >= $%.2f",
                tenant_id,
                spent,
                budget,
            )
            return False, f"Spending limit exceeded (${spent:.2f} >= ${budget:.2f})"

        return True, ""

    # ── Persistence ───────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Persist spending records and overrides to a JSON file.

        Format::

            {
              "spending": {"tenant-1": 5.0, ...},
              "overrides": {"tenant-2": 100.0, ...}
            }

        Safe to call concurrently: each writer uses a unique temp file and
        atomically replaces the target, so concurrent writers can never
        splice partial documents into the published file (a shared
        ``<file>.tmp`` name let a rename publish a torn half-written JSON
        document under concurrent record_spending calls).
        """
        path = Path(path)
        with self._lock:
            data = {
                "spending": dict(self._spending),
                "overrides": dict(self._overrides),
            }
        text = json.dumps(data, ensure_ascii=False, indent=2)

        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp per write + atomic replace: last completed writer wins
        # and the target always holds ONE complete document.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def load(self, path: str | Path) -> None:
        """Load spending records and overrides from a JSON file.

        Calling this method merges the persisted state on top of the
        current in-memory state.  This is rarely needed outside of
        ``__init__`` (which already calls ``_load_from``), but is
        exposed for manual admin recovery.
        """
        self._load_from(Path(path))

    def _load_from(self, path: Path) -> None:
        """Load persisted state from a JSON file and merge into memory."""
        if not path.exists():
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "[SPENDING] Failed to load persistence file %s: %s", path, exc
            )
            return

        with self._lock:
            persisted_spending = data.get("spending", {})
            for tenant_id, cost in persisted_spending.items():
                current = self._spending.get(tenant_id, 0.0)
                self._spending[tenant_id] = current + cost

            persisted_overrides = data.get("overrides", {})
            self._overrides.update(persisted_overrides)

        logger.info(
            "[SPENDING] Loaded %d tenant spending records and %d overrides from %s",
            len(persisted_spending),
            len(persisted_overrides),
            path,
        )

    def _persist(self) -> None:
        """Write current state to the persistence file (if configured).

        Called after every mutation (record_spending, set_budget).
        The write is synchronous and fast (small JSON dict).
        """
        if self._persistence_path is None:
            return
        try:
            self.save(self._persistence_path)
        except OSError as exc:
            logger.warning("[SPENDING] Failed to persist spending data: %s", exc)


class BudgetExceeded(Exception):
    """Raised when a reservation would exceed the principal budget."""


class ReservationConflict(Exception):
    """Raised when a request ID is reused with incompatible state or values."""


# ── Money unit ───────────────────────────────────────────────────────────────
#
# Admission arithmetic is integer micro-USD (1e-6 USD). A realistic turn costs
# a small fraction of a cent, so cents round every reservation and every commit
# to zero and silently disable admission entirely. Micro-USD keeps four decimal
# digits of headroom below one cent and still fits a monthly budget in int64.
MICROS_PER_USD = 1_000_000


def usd_to_micros(cost_usd: float) -> int:
    """Convert a provider-reported USD cost to integer micro-USD.

    Rounds up so a partially representable cost is never under-charged.
    """
    if cost_usd <= 0:
        return 0
    return math.ceil(cost_usd * MICROS_PER_USD)


def budget_usd_to_micros(budget_usd: float) -> int | None:
    """Convert a configured USD budget to micro-USD, preserving 'unlimited'.

    ``SpendingChecker`` treats a budget of zero or less as *no limit*. The
    ledger must not silently invert that into *nothing is allowed*, so an
    unlimited budget is represented as ``None`` (stored as SQL NULL) rather
    than as ``0``.
    """
    if budget_usd <= 0:
        return None
    return int(budget_usd * MICROS_PER_USD)


@dataclass(frozen=True)
class Reservation:
    request_id: str
    principal_id: str
    estimated_cost_micros: int
    expires_at: float
    tenant_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpendingBalance:
    """Principal-level admission state.

    ``budget_micros is None`` means unlimited, matching ``SpendingChecker``.
    """

    principal_id: str
    budget_micros: int | None
    committed_micros: int
    reserved_micros: int

    @property
    def unlimited(self) -> bool:
        return self.budget_micros is None

    @property
    def available_micros(self) -> int | None:
        if self.budget_micros is None:
            return None
        return self.budget_micros - self.committed_micros - self.reserved_micros


class SQLiteSpendingLedger:
    """Transactional single-instance ledger for spending admission.

    SQLite is intentionally used behind this small domain API. ``BEGIN IMMEDIATE``
    serializes reserve/commit/release mutations across threads and processes;
    the repository can later be replaced by PostgreSQL without changing the
    agent-loop contract.

    All amounts are integer micro-USD. A ``None`` budget means unlimited, which
    matches ``SpendingChecker``'s ``budget <= 0`` semantics.
    """

    def __init__(
        self,
        path: str | Path,
        default_budget_micros: int | None,
        reservation_ttl_seconds: float = 1800.0,
    ):
        if default_budget_micros is not None and default_budget_micros < 0:
            raise ValueError("default_budget_micros must be non-negative or None")
        if reservation_ttl_seconds <= 0:
            raise ValueError("reservation_ttl_seconds must be positive")
        self.path = Path(path)
        self.default_budget_micros = default_budget_micros
        self.reservation_ttl_seconds = reservation_ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one serialized write transaction and always close the handle.

        ``sqlite3.Connection`` as a context manager commits or rolls back but
        never closes, so every ledger call would leak a file handle until GC.
        """
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            self._reject_legacy_schema(connection)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS spending_balances (
                    principal_id TEXT PRIMARY KEY,
                    budget_micros INTEGER,
                    budget_override INTEGER NOT NULL DEFAULT 0,
                    committed_micros INTEGER NOT NULL DEFAULT 0,
                    reserved_micros INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS spending_reservations (
                    request_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    estimated_cost_micros INTEGER NOT NULL,
                    committed_cost_micros INTEGER,
                    status TEXT NOT NULL,
                    tenant_ids_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_spending_reservations_principal
                    ON spending_reservations(principal_id, status);
                """
            )
        finally:
            connection.close()

    @staticmethod
    def _reject_legacy_schema(connection: sqlite3.Connection) -> None:
        """Refuse to run against a pre-micro-USD ledger file.

        The cents schema could only have been written by a build where
        admission was effectively inert (every amount rounded to zero). Failing
        loudly is safer than reinterpreting those numbers as micro-USD or
        deleting a file that an operator may want to inspect.
        """
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='spending_balances'"
        ).fetchone()
        if row is None:
            return
        columns = {
            info["name"]
            for info in connection.execute("PRAGMA table_info(spending_balances)")
        }
        if "budget_cents" in columns:
            raise RuntimeError(
                "spending ledger uses the retired cents schema; remove the file "
                f"{connection.execute('PRAGMA database_list').fetchone()['file']} "
                "and let the micro-USD ledger be recreated"
            )

    @staticmethod
    def _reservation_from_row(row: sqlite3.Row) -> Reservation:
        tenant_ids = tuple(json.loads(row["tenant_ids_json"]))
        return Reservation(
            request_id=row["request_id"],
            principal_id=row["principal_id"],
            estimated_cost_micros=row["estimated_cost_micros"],
            expires_at=row["expires_at"],
            tenant_ids=tenant_ids,
        )

    def _expire_for_principal(
        self, connection: sqlite3.Connection, principal_id: str, now: float
    ) -> None:
        rows = connection.execute(
            """
            SELECT request_id, estimated_cost_micros
            FROM spending_reservations
            WHERE principal_id = ? AND status = 'ACTIVE' AND expires_at <= ?
            """,
            (principal_id, now),
        ).fetchall()
        if not rows:
            return
        released = sum(row["estimated_cost_micros"] for row in rows)
        connection.execute(
            """
            UPDATE spending_reservations
            SET status = 'EXPIRED'
            WHERE principal_id = ? AND status = 'ACTIVE' AND expires_at <= ?
            """,
            (principal_id, now),
        )
        connection.execute(
            """
            UPDATE spending_balances
            SET reserved_micros = reserved_micros - ?
            WHERE principal_id = ?
            """,
            (released, principal_id),
        )

    def reserve(
        self,
        principal_id: str,
        request_id: str,
        estimated_cost_micros: int,
        tenant_ids: list[str] | tuple[str, ...],
    ) -> Reservation:
        if not principal_id or not request_id:
            raise ValueError("principal_id and request_id are required")
        if estimated_cost_micros < 0:
            raise ValueError("estimated_cost_micros must be non-negative")
        now = time.time()
        expires_at = now + self.reservation_ttl_seconds
        tenants = tuple(tenant_ids)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO spending_balances(principal_id, budget_micros, budget_override)
                VALUES (?, ?, ?)
                ON CONFLICT(principal_id) DO NOTHING
                """,
                (principal_id, self.default_budget_micros, 0),
            )
            self._expire_for_principal(connection, principal_id, now)
            existing = connection.execute(
                "SELECT * FROM spending_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["principal_id"] != principal_id
                    or existing["estimated_cost_micros"] != estimated_cost_micros
                ):
                    raise ReservationConflict(
                        f"request_id already belongs to another reservation: {request_id}"
                    )
                if existing["status"] == "ACTIVE":
                    return self._reservation_from_row(existing)
                raise ReservationConflict(
                    f"request_id is already terminal: {request_id}"
                )

            # Follow an operator default-budget reload: rows seeded from the
            # default (budget_override = 0) must track the current default,
            # while an explicit set_budget() override (budget_override = 1) is
            # never touched. Without this, an existing principal keeps a stale
            # budget after a config reload — e.g. tightening the cap would not
            # apply to principals created earlier.
            connection.execute(
                """
                UPDATE spending_balances
                SET budget_micros = ?
                WHERE principal_id = ? AND budget_override = 0
                """,
                (self.default_budget_micros, principal_id),
            )

            balance = connection.execute(
                "SELECT * FROM spending_balances WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
            # The upsert above guarantees the row exists inside this transaction.
            assert balance is not None
            budget = balance["budget_micros"]
            if budget is not None:
                available = (
                    budget - balance["committed_micros"] - balance["reserved_micros"]
                )
                if estimated_cost_micros > available:
                    raise BudgetExceeded(
                        f"budget exceeded for {principal_id}: "
                        f"requested {estimated_cost_micros}, available {available}"
                    )
            connection.execute(
                """
                INSERT INTO spending_reservations(
                    request_id, principal_id, estimated_cost_micros,
                    status, tenant_ids_json, created_at, expires_at
                ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    request_id,
                    principal_id,
                    estimated_cost_micros,
                    json.dumps(tenants),
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                "UPDATE spending_balances SET reserved_micros = reserved_micros + ? "
                "WHERE principal_id = ?",
                (estimated_cost_micros, principal_id),
            )
            return Reservation(
                request_id, principal_id, estimated_cost_micros, expires_at, tenants
            )

    def commit(self, request_id: str, actual_cost_micros: int) -> None:
        """Record realized cost for a reservation.

        An expired reservation is still committed: the provider call already
        happened and the money is really owed. Refusing it would both lose the
        charge and fail a turn the user already received.
        """
        if actual_cost_micros < 0:
            raise ValueError("actual_cost_micros must be non-negative")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM spending_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ReservationConflict(f"unknown request_id: {request_id}")
            if row["status"] == "COMMITTED":
                return
            if row["status"] not in ("ACTIVE", "EXPIRED"):
                raise ReservationConflict(
                    f"reservation is not committable: {request_id} ({row['status']})"
                )
            # An EXPIRED reservation already returned its estimate to the
            # available pool, so only an ACTIVE one still holds capacity.
            held = row["estimated_cost_micros"] if row["status"] == "ACTIVE" else 0
            if row["status"] == "EXPIRED":
                logger.warning(
                    "[SPENDING] committing expired reservation %s for %s",
                    request_id,
                    row["principal_id"],
                )
            connection.execute(
                """
                UPDATE spending_balances
                SET reserved_micros = reserved_micros - ?,
                    committed_micros = committed_micros + ?
                WHERE principal_id = ?
                """,
                (held, actual_cost_micros, row["principal_id"]),
            )
            connection.execute(
                """
                UPDATE spending_reservations
                SET status = 'COMMITTED', committed_cost_micros = ?
                WHERE request_id = ? AND status IN ('ACTIVE', 'EXPIRED')
                """,
                (actual_cost_micros, request_id),
            )

    def release(self, request_id: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM spending_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "ACTIVE":
                return
            connection.execute(
                "UPDATE spending_balances SET reserved_micros = reserved_micros - ? "
                "WHERE principal_id = ?",
                (row["estimated_cost_micros"], row["principal_id"]),
            )
            connection.execute(
                "UPDATE spending_reservations SET status = 'RELEASED' "
                "WHERE request_id = ? AND status = 'ACTIVE'",
                (request_id,),
            )

    def expire_stale(self) -> None:
        with self._transaction() as connection:
            principals = connection.execute(
                "SELECT DISTINCT principal_id FROM spending_reservations "
                "WHERE status = 'ACTIVE'"
            ).fetchall()
            now = time.time()
            for row in principals:
                self._expire_for_principal(connection, row["principal_id"], now)

    def set_budget(self, principal_id: str, budget_micros: int | None) -> None:
        """Set an explicit principal budget. ``None`` means unlimited.

        The principal budget is owned here, not derived from tenant budgets: a
        composite turn has one billing principal and several usage dimensions.
        """
        if not principal_id:
            raise ValueError("principal_id is required")
        if budget_micros is not None and budget_micros < 0:
            raise ValueError("budget_micros must be non-negative or None")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO spending_balances(principal_id, budget_micros, budget_override)
                VALUES (?, ?, ?)
                ON CONFLICT(principal_id)
                DO UPDATE SET budget_micros = excluded.budget_micros,
                              budget_override = 1
                """,
                (principal_id, budget_micros, 1),
            )

    def balance(self, principal_id: str) -> SpendingBalance:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM spending_balances WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return SpendingBalance(principal_id, self.default_budget_micros, 0, 0)
        return SpendingBalance(
            principal_id=row["principal_id"],
            budget_micros=row["budget_micros"],
            committed_micros=row["committed_micros"],
            reserved_micros=row["reserved_micros"],
        )


# ── Singleton ────────────────────────────────────────────────────────────────

_spending_checker: SpendingChecker | None = None
_spending_ledger: SQLiteSpendingLedger | None = None


_DEFAULT_PERSISTENCE_PATH: str = ".data/spending.json"


def get_spending_checker() -> SpendingChecker:
    """Get or create singleton spending checker.

    Uses ``SPENDING_PERSISTENCE_PATH`` env var (default ``.data/spending.json``)
    so spending records survive service restarts.
    """
    global _spending_checker
    if _spending_checker is None:
        import os as _os

        persist_path = _os.environ.get(
            "SPENDING_PERSISTENCE_PATH", _DEFAULT_PERSISTENCE_PATH
        )
        _spending_checker = SpendingChecker(persistence_path=persist_path)
    return _spending_checker


def get_spending_ledger() -> SQLiteSpendingLedger:
    """Get or create the singleton admission ledger.

    Configuration is read from settings on every call, so an admin reload of
    the default principal budget or reservation TTL takes effect without a
    restart. Only the SQLite file location is fixed for the process lifetime;
    changing it requires a restart because in-flight reservations live there.
    """
    global _spending_ledger
    from helperium_sdk.settings import settings

    default_budget_micros = budget_usd_to_micros(
        settings.spending_principal_default_budget
    )
    ttl_seconds = max(1.0, settings.spending_reservation_ttl_seconds)
    if _spending_ledger is None:
        _spending_ledger = SQLiteSpendingLedger(
            settings.spending_ledger_path,
            default_budget_micros=default_budget_micros,
            reservation_ttl_seconds=ttl_seconds,
        )
    else:
        _spending_ledger.default_budget_micros = default_budget_micros
        _spending_ledger.reservation_ttl_seconds = ttl_seconds
    return _spending_ledger


def reset_spending_singletons() -> None:
    """Drop cached spending singletons.

    Used by tests and by admin reload paths that change the persistence or
    ledger location.
    """
    global _spending_checker, _spending_ledger
    _spending_checker = None
    _spending_ledger = None
