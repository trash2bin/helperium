"""The one raw per-request record and the one percentile calculator (§10).

Every driver writes this shape, so L1-L3 stay comparable instead of becoming
apples and oranges. Aggregates from external tools are a reference column only:
the numbers that go into a verdict are computed here.

Coordinated omission. The measurement of a request is only meaningful against
the moment it was *supposed* to start, so a record carries both ``planned_ms``
(intended start offset) and ``started_ms`` (actual start offset). One extra field
beyond the list in §10 is deliberate: without it a calculator would have to guess
whether ``actual_ms`` was measured from the intended or the actual start, and the
correction would silently differ between drivers. ``finished_ms`` and the
compensated latency are then exact, not inferred.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence


class ErrorClass(str, Enum):
    """Structural error taxonomy (§7). Never derived from message text."""

    # transport / budgets
    BUDGET_429 = "429_budget"
    TIMEOUT_CLIENT = "timeout_client"
    TIMEOUT_SERVER = "timeout_server"
    SERVER_5XX = "5xx"
    RESET = "reset"
    SSE_ABORT = "sse_abort"
    # turn terminators, from the backlog ``outcome`` field
    LIMIT_MODEL_CALLS = "limit_model_calls"
    LIMIT_TOOL_CALLS = "limit_tool_calls"
    LIMIT_CONTEXT = "limit_context"
    TURN_LIMIT = "turn_limit"
    # our own scheduler: serialization on a tenant, not a dependency failure
    MCP_LOCK_TIMEOUT = "mcp_lock_timeout"
    OTHER = "other"


@dataclass
class RawRequestRecord:
    """One request as seen by a driver. Written to ``raw/*.jsonl`` verbatim."""

    planned_ms: float
    started_ms: float
    actual_ms: float
    ttfe_session: float | None = None
    ttfe: float | None = None
    ttfe_tool: float | None = None
    t_complete: float | None = None
    prompt_tokens: int | None = None
    history_turns: int = 0
    session_id: str = ""
    tenant: str = ""
    scenario: str = ""
    status: str = "ok"
    http_status: int | None = None
    sse_terminal_event: str | None = None
    error_class: ErrorClass | None = None
    terminator: str | None = None

    @property
    def finished_ms(self) -> float:
        """Completion offset from stage start."""
        return self.started_ms + self.actual_ms

    @property
    def is_error(self) -> bool:
        return self.status != "ok"

    def compensated_ms(self) -> float:
        """Latency from the intended start, but never better than measured.

        A client that fell behind (``started_ms > planned_ms``) carries its own
        slip, which is the whole point of the correction: without it a saturated
        generator understates latency and the knee looks higher than it is. A
        client that was early (idle pool) gets no invented improvement - the
        floor stays at the measured latency.
        """
        return max(self.actual_ms, self.finished_ms - self.planned_ms)

    def as_jsonl(self) -> str:
        payload = asdict(self)
        payload["error_class"] = self.error_class.value if self.error_class else None
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> RawRequestRecord:
        raw: dict[str, Any] = json.loads(line)
        if raw.get("error_class") is not None:
            raw["error_class"] = ErrorClass(raw["error_class"])
        return cls(**raw)


def write_raw_records(path: str | Path, records: Iterable[RawRequestRecord]) -> int:
    """Append records to a raw JSONL file, one request per line."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.as_jsonl() + "\n")
            written += 1
    return written


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, ``q`` in ``[0, 1]``.

    Same definition as the k6/prometheus_client histograms we cross-check
    against, so a mismatch in the report is a real difference, not a formula.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


@dataclass(frozen=True)
class StageStats:
    """Per-stage aggregates: the numbers a verdict is made of (§7)."""

    count: int
    errors: int
    achieved_rps: float
    p50: float
    p95: float
    p99: float
    compensated_p95: float
    t_complete_p95: float | None
    prompt_tokens_p50: float | None
    terminator_mix: dict[str, int] = field(default_factory=dict)
    error_mix: dict[str, int] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0.0

    def meets(self, t_budget_ms: float, max_error_rate: float = 0.01) -> bool:
        """Capacity criterion (§7): p95 within budget and errors under 1%."""
        return self.p95 <= t_budget_ms and self.error_rate <= max_error_rate


def summarise(records: Sequence[RawRequestRecord], duration_s: float) -> StageStats:
    """Aggregate one stage. Warm-up exclusion is the driver's job, not ours."""
    if not records:
        raise ValueError("cannot summarise an empty stage")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")

    latencies = [r.actual_ms for r in records]
    compensated = [r.compensated_ms() for r in records]
    completions = [r.t_complete for r in records if r.t_complete is not None]
    tokens = [float(r.prompt_tokens) for r in records if r.prompt_tokens is not None]

    terminators: dict[str, int] = {}
    errors: dict[str, int] = {}
    for record in records:
        if record.terminator:
            terminators[record.terminator] = terminators.get(record.terminator, 0) + 1
        if record.is_error:
            key = record.error_class.value if record.error_class else "unclassified"
            errors[key] = errors.get(key, 0) + 1

    return StageStats(
        count=len(records),
        errors=sum(1 for r in records if r.is_error),
        achieved_rps=len(records) / duration_s,
        p50=percentile(latencies, 0.50),
        p95=percentile(latencies, 0.95),
        p99=percentile(latencies, 0.99),
        compensated_p95=percentile(compensated, 0.95),
        t_complete_p95=percentile(completions, 0.95) if completions else None,
        prompt_tokens_p50=percentile(tokens, 0.50) if tokens else None,
        terminator_mix=terminators,
        error_mix=errors,
    )
