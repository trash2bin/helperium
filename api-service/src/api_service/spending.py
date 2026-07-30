"""Per-tenant LLM spending tracking and limits.

Tracks spending in-memory and enforces hard limits.
Configurable via env vars and admin API.
"""

from __future__ import annotations

import json
import logging
import threading
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

        Safe to call concurrently: creates a temp file and atomically
        renames it to avoid partial writes from a crash mid-write.
        """
        path = Path(path)
        with self._lock:
            data = {
                "spending": dict(self._spending),
                "overrides": dict(self._overrides),
            }
        text = json.dumps(data, ensure_ascii=False, indent=2)

        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp then rename
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.rename(path)
        except OSError:
            # Fall back to direct write if rename across filesystems fails
            path.write_text(text, encoding="utf-8")

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


# ── Singleton ────────────────────────────────────────────────────────────────

_spending_checker: SpendingChecker | None = None


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
