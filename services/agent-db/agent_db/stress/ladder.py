"""The ladder: rates in ascending order, a knee, and a verdict that admits doubt.

Three rules from §2 and §7 shape this module:

- **A knee, not a maximum.** Capacity is the last rung where ``p95 <= T`` and
  ``error <= 1%``. The stage where the platform already degraded is not defended
  by anyone, so reporting it would be honest arithmetic and dishonest advice.
- **A knee is a range until bisection says otherwise.** "Between 40 and 80" is
  not a number, so the bracket left by the ladder is halved until it is within
  ``tolerance`` (default ±10%).
- **An invalid stage is an action, not a verdict.** Dropped ticks mean the
  *generator* saturated, so the pool is doubled and the rung is re-run - up to
  ``max_pool_expansions`` times, after which the stage is declared invalid and
  the ladder stops, because a knee measured through a generator knee is the
  generator's knee.

``budget_preset: on`` is refused outright: at 30/minute per path the arrival rate
never reaches the platform, and any "knee" found there would be slowapi's (§5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from .profile import LoadProfile
from .records import StageStats
from .runner import (
    GENERATOR_CPU_LIMIT,
    SessionRecycle,
    StageResult,
    StageSpec,
    WorkloadPlan,
)

# §7 defaults. L4 is a soak/overload layer whose budget depends on the scenario,
# so it has no default: an invented T would be a verdict with no basis.
# §7's documented defaults. L3's entry is the design number and is deliberately
# still here while L3 is not plannable: removing it would make L3 fail for the
# wrong reason ("no default T") and would silently reintroduce an L3 budget the
# day someone drops the judged-metric fence. The fence lives in both
# ``LadderPlan.for_profile`` and ``run_ladder``.
LAYER_T_BUDGET_MS: dict[str, float] = {"L1": 50.0, "L2": 500.0, "L3": 500.0}

# §2: 5 -> 10 -> 20 -> 40 -> 80, doubling.
DEFAULT_RATES: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0, 80.0)

# Layers whose judged percentile this harness can compute, and what the verdict
# rests on when the overhead could not be separated from the model (§7).
#
# §7 judges ``platform_overhead p95`` - the turn without the model's own latency -
# and says a stage must not fail because the model was slow. On L1 there is no LLM
# inside the turn, so the overhead is the compensated turn latency and the metric
# exists by identity. On L2 the stub is ~0-latency and §7 explicitly allows
# ``t_complete`` as the criterion. On L3 the model is real and slow, so the overhead
# is the only honest criterion - and it is computable exactly when the substrate
# reports its own per-request service time (§4). Those layers therefore do not
# need a fence, they need a substrate: a driver that supplies no model timings
# produces a stage whose judged metric is absent, and the ladder refuses *that*
# instead of judging an honest 2.5 s model as a platform failure.
LAYER_RULE: dict[str, str] = {
    "L1": "compensated turn latency (no LLM inside the turn)",
    "L2": "compensated turn latency (stub is ~0-latency; seconds are not a criterion)",
    "L3": "platform overhead (requires per-request substrate timings)",
    "L4": "platform overhead (requires per-request substrate timings)",
}

# The historical name for the layers whose verdict can never need a substrate.
JUDGED_METRIC_READY_LAYERS = ("L1", "L2")


class LadderError(ValueError):
    """The ladder cannot be run as specified."""


@dataclass(frozen=True)
class StageTicket:
    """Which run of which rung one piece of evidence belongs to (§10).

    ``attempt`` counts pool expansions within the rung; ``run_index`` is 0 for the
    rung that produced the verdict and 1.. for the knee repeats. Both are needed:
    without them ``raw/<rps>.jsonl`` interleaves the repeats and the discarded
    attempts into one file that cannot be split again, so the headline median -
    which §2 defines over 2-3 runs - would not be reproducible from the raw
    evidence it claims to come from.
    """

    target_rps: float
    attempt: int
    run_index: int

    @property
    def is_repeat(self) -> bool:
        return self.run_index > 0

    def label(self) -> str:
        """File-name-safe label for this run of this rung."""
        return f"{self.target_rps:g}rps-run{self.run_index}-attempt{self.attempt}"


class StageSink(Protocol):
    """Where a rung's raw evidence goes (§10: ``raw/<stage>.jsonl`` per stage).

    A callback rather than a field on :class:`StageOutcome`: a 48-hour soak would
    otherwise hold every record in memory, and the caller is the one that knows
    the run directory.
    """

    def __call__(
        self, outcome: StageOutcome, stage: StageResult, ticket: StageTicket
    ) -> None: ...


class StageRunnerPort(Protocol):
    """What the ladder needs from :class:`agent_db.stress.runner.StageRunner`."""

    def run(
        self,
        spec: StageSpec,
        plan: WorkloadPlan,
        *,
        tenants: Sequence[str],
        workers: int | None = None,
        recycle: SessionRecycle | None = None,
    ) -> StageResult: ...


def session_recycle_for(profile: LoadProfile) -> SessionRecycle | None:
    """§3's rotation for this profile, or ``None`` where it does not apply.

    The limits exist because api-service counts user turns per session and
    refuses the ones past ``ABUSE_MAX_USER_TURNS``. That gate is on the chat
    path, so it is L2 and above that have to rotate: an L1 rung never reaches it.
    Rotating L1 sessions anyway would be worse than useless, because opening an
    MCP session costs ``GET mapping/schema`` and ``list_tools`` - work §1 counts
    as warm-up. Mid-stage it would land inside the measured window and buy a
    knee with schema fetches, not with tool calls.
    """
    if profile.target_layer not in {"L2", "L3", "L4"}:
        return None
    sessions = profile.sessions
    return SessionRecycle(
        turns=sessions.recycle_after_turns,
        seconds=float(sessions.recycle_after_seconds),
    )


@dataclass(frozen=True)
class LadderPlan:
    """One ladder, fully specified before the first tick is scheduled."""

    rates: tuple[float, ...] = DEFAULT_RATES
    t_budget_ms: float = 50.0
    duration_s: float = 300.0
    warmup_s: float = 60.0
    max_error_rate: float = 0.01
    tolerance: float = 0.10
    max_bisection_steps: int = 6
    repeats: int = 1
    max_pool_expansions: int = 2
    # §2: pool_size = ceil(rps x (T + margin)) x headroom, recomputed per stage.
    # From the criterion, not from the previous stage's p95: otherwise the pool
    # grows with the very degradation the ladder is looking for.
    pool_margin_s: float = 1.0
    pool_headroom: float = 1.0
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.rates:
            raise LadderError("the ladder needs at least one rate")
        if any(rate <= 0 for rate in self.rates):
            raise LadderError(f"rates must be positive, got {self.rates}")
        if list(self.rates) != sorted(self.rates):
            raise LadderError(f"rates must ascend, got {self.rates}")
        if len(set(self.rates)) != len(self.rates):
            raise LadderError(f"rates must be distinct, got {self.rates}")
        if self.t_budget_ms <= 0:
            raise LadderError(f"t_budget_ms must be positive, got {self.t_budget_ms}")
        if not 0.0 < self.tolerance < 1.0:
            raise LadderError(f"tolerance must be in (0, 1), got {self.tolerance}")
        if self.repeats < 1:
            raise LadderError(f"repeats must be at least 1, got {self.repeats}")
        if self.max_pool_expansions < 0:
            raise LadderError(
                f"max_pool_expansions must not be negative, got {self.max_pool_expansions}"
            )
        if self.pool_margin_s < 0:
            raise LadderError(
                f"pool_margin_s must not be negative, got {self.pool_margin_s}"
            )
        if self.pool_headroom <= 0:
            raise LadderError(
                f"pool_headroom must be positive, got {self.pool_headroom}"
            )

    @classmethod
    def for_profile(
        cls,
        profile: LoadProfile,
        *,
        rates: Sequence[float] | None = None,
        t_budget_ms: float | None = None,
        duration_s: float | None = None,
        warmup_s: float | None = None,
        **kwargs: Any,
    ) -> LadderPlan:
        """Defaults from §7 and the profile, with no invented budget for L4."""
        if profile.budget_preset != "off":
            raise LadderError(
                f"profile {profile.name!r} has budget_preset="
                f"{profile.budget_preset!r}; a knee ladder is only meaningful with "
                "the budgets off (§5), otherwise the rung measures slowapi"
            )
        if profile.target_layer not in LAYER_RULE:
            raise LadderError(
                f"layer {profile.target_layer} has no judged-metric rule: add one "
                "to LAYER_RULE before running a ladder on it"
            )
        budget = t_budget_ms
        if budget is None:
            budget = LAYER_T_BUDGET_MS.get(profile.target_layer)
        if budget is None:
            raise LadderError(
                f"layer {profile.target_layer} has no default T in the design; pass "
                "t_budget_ms explicitly rather than measuring against an invented one"
            )
        return cls(
            rates=tuple(rates) if rates is not None else DEFAULT_RATES,
            t_budget_ms=budget,
            duration_s=duration_s
            if duration_s is not None
            else profile.arrival.duration_s,
            warmup_s=warmup_s if warmup_s is not None else profile.arrival.warmup_s,
            **kwargs,
        )

    def workers_for(self, rps: float, *, floor: int = 1) -> int:
        """Concurrent sessions this rung needs, from the criterion (§2)."""
        needed = math.ceil(rps * (self.t_budget_ms / 1000.0 + self.pool_margin_s))
        return max(floor, math.ceil(needed * self.pool_headroom))

    def spec_for(self, rps: float) -> StageSpec:
        return StageSpec(
            target_rps=rps, duration_s=self.duration_s, warmup_s=self.warmup_s
        )


@dataclass(frozen=True)
class StageOutcome:
    """One rung, after any pool expansion, with the verdict it earned."""

    target_rps: float
    workers: int
    pool_expansions: int
    valid: bool
    invalid_reasons: tuple[str, ...]
    stats: StageStats
    tick_lag_p99: float
    dropped_ticks: int
    t_budget_ms: float
    # Ticks dropped inside the measured window - the half the verdict is about.
    # The whole-stage ``dropped_ticks`` stays next to it so a reader can tell a
    # rung that dropped ticks while warming up from one that dropped them while
    # being measured; only the latter invalidates (§3).
    measured_dropped_ticks: int = 0
    cpu_s: float = 0.0
    cpu_measured_s: float = 0.0
    # The window the measured ticks span, ``(total_ticks - warmup_ticks) / rps``:
    # the denominator both the saturation gate and the reported generator share
    # use, so the artefact and the gate cannot drift apart.
    measured_window_s: float = 0.0
    spin_tail_ms: float = 0.0
    max_error_rate: float = 0.01
    step_counts: dict[str, int] = field(default_factory=dict)

    @property
    def generator_bound(self) -> bool:
        """True when the rung is unmeasurable because the generator saturated.

        Same rule as ``StageResult.generator_bound`` and over the same quantities:
        the CPU spent inside the measured window (sampled at the warm-up boundary)
        against the length of that window. A whole-stage average would let a
        generator that saturated while measuring escape the gate, and the gate is
        the only thing standing between that and a pool doubling that makes it
        worse.
        """
        if self.measured_window_s <= 0:
            return False
        cpu_measured = self.cpu_measured_s if self.cpu_measured_s else self.cpu_s
        return cpu_measured >= GENERATOR_CPU_LIMIT * self.measured_window_s

    @property
    def has_platform_overhead(self) -> bool:
        """True when §7's judged percentile was computable for this rung."""
        return self.stats.platform_overhead_p95 is not None

    @property
    def judged_p95(self) -> float:
        """The percentile the verdict was made on (§7)."""
        if self.stats.platform_overhead_p95 is not None:
            return self.stats.platform_overhead_p95
        return self.stats.compensated_p95

    @property
    def verdict(self) -> Literal["pass", "fail", "invalid"]:
        if not self.valid:
            return "invalid"
        return "pass" if self.meets else "fail"

    @property
    def meets(self) -> bool:
        return self.stats.meets(
            self.t_budget_ms, self.max_error_rate, target_rps=self.target_rps
        )

    @property
    def p95(self) -> float:
        return self.stats.p95

    @classmethod
    def from_result(
        cls,
        result: StageResult,
        *,
        t_budget_ms: float,
        max_error_rate: float,
        workers: int,
        pool_expansions: int,
        step_counts: dict[str, int] | None = None,
    ) -> StageOutcome:
        return cls(
            target_rps=result.spec.target_rps,
            workers=workers,
            pool_expansions=pool_expansions,
            valid=result.is_valid,
            invalid_reasons=tuple(result.invalid_reasons()),
            stats=result.stats,
            tick_lag_p99=result.tick_lag_p99,
            dropped_ticks=result.dropped_ticks,
            measured_dropped_ticks=result.gating_dropped_ticks,
            cpu_s=result.cpu_s,
            cpu_measured_s=result.cpu_measured_s,
            measured_window_s=result.spec.measured_window_s,
            spin_tail_ms=result.spin_tail_ms,
            step_counts=dict(step_counts or {}),
            t_budget_ms=t_budget_ms,
            max_error_rate=max_error_rate,
        )


@dataclass(frozen=True)
class KneeEstimate:
    """What the ladder concluded, with the uncertainty it concluded it at."""

    rate: float | None
    bracket: tuple[float, float] | None
    tolerance: float
    bisection_steps: int

    @property
    def relative_width(self) -> float | None:
        if self.bracket is None:
            return None
        low, high = self.bracket
        return (high - low) / low if low else None


@dataclass(frozen=True)
class Prediction:
    """The analytic forecast written before the ladder (§3)."""

    rate: float
    model: str
    inputs: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {"rate": self.rate, "model": self.model, "inputs": self.inputs}


@dataclass
class LadderResult:
    """The whole run: every rung, the knee, the prediction, the status."""

    plan: LadderPlan
    profile_name: str
    target_layer: str
    stages: list[StageOutcome] = field(default_factory=list)
    knee: KneeEstimate | None = None
    repeats: list[StageOutcome] = field(default_factory=list)
    prediction: Prediction | None = None
    status: Literal[
        "knee_found",
        "knee_lower_bound",
        "below_first_rung",
        "invalid",
        "budget_preset_on",
        "unjudgeable",
    ] = "knee_found"

    def stage_at(self, rps: float) -> StageOutcome | None:
        for stage in self.stages:
            if math.isclose(stage.target_rps, rps):
                return stage
        return None

    def headline_sample(self) -> list[StageOutcome]:
        """The knee stage and its repeats, as one sample."""
        knee = self.stage_at(self.knee.rate) if self.knee and self.knee.rate else None
        return ([knee] if knee is not None else []) + list(self.repeats)

    def headline(self) -> dict[str, float] | None:
        """Median judged percentile and spread over the knee repeats (§2).

        A single run is not a headline: the doc requires a median of 2-3 runs, so
        with fewer than two *valid* runs the caller marks the result
        unpublishable instead of publishing one sample. The percentile is the one
        the verdict used (``judged_p95``): reporting an easier metric as the
        headline while judging on a harder one would be two claims about one run.
        Runs that were invalid are counted and reported, not silently dropped.
        """
        sample = self.headline_sample()
        measured = [stage for stage in sample if stage.valid]
        if not measured:
            return None
        values = sorted(stage.judged_p95 for stage in measured)
        middle = (
            values[len(values) // 2]
            if len(values) % 2
            else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
        )
        return {
            "runs": float(len(measured)),
            "runs_requested": float(len(sample)),
            "runs_discarded": float(len(sample) - len(measured)),
            "p95_median": middle,
            "p95_min": values[0],
            "p95_max": values[-1],
        }


def predict_tenant_ceiling_rps(
    *, p95_tool_ms: float, calls_per_turn: float, tenants: int
) -> Prediction:
    """Analytic ceiling from per-tenant serialization of tool calls (§3).

    Every tool call of a turn - preload included - goes through one tenant's
    ``call_lock``, so one tenant serves at most ``1 / p95(tool)`` calls per second,
    and a turn costs ``calls_per_turn`` calls. On ``n`` tenants with separate
    connections the ceiling scales by ``n``; a composite scope keeps one lock and
    does not scale, which is why the scope is an axis of its own.
    """
    if p95_tool_ms <= 0:
        raise LadderError(f"p95_tool_ms must be positive, got {p95_tool_ms}")
    if calls_per_turn <= 0:
        raise LadderError(f"calls_per_turn must be positive, got {calls_per_turn}")
    if tenants < 1:
        raise LadderError(f"tenants must be at least 1, got {tenants}")
    per_tenant = 1.0 / (p95_tool_ms / 1000.0 * calls_per_turn)
    return Prediction(
        rate=per_tenant * tenants,
        model=(
            "1 / (p95_tool_latency x calls_per_turn) x tenants.count; assumes every "
            "call of a turn serializes on one tenant's call_lock and that separate "
            "scopes do not share it"
        ),
        inputs={
            "p95_tool_ms": p95_tool_ms,
            "calls_per_turn": calls_per_turn,
            "tenants": float(tenants),
        },
    )


def run_ladder(
    *,
    runner: StageRunnerPort,
    plan: LadderPlan,
    workload: WorkloadPlan,
    profile: LoadProfile,
    tenants: Sequence[str],
    prediction: Prediction | None = None,
    expand_pool: bool = True,
    on_stage: StageSink | None = None,
) -> LadderResult:
    """Run the rungs in order and stop at the first that fails or is invalid.

    Stopping at the first failure is the point: the rungs above it measure a
    degraded platform, and continuing would spend minutes producing numbers that
    the report is not allowed to use.
    """
    if profile.budget_preset != "off":
        raise LadderError(
            f"profile {profile.name!r} has budget_preset={profile.budget_preset!r}: "
            "the ladder is only defined with the budgets off (§5)"
        )
    if profile.target_layer not in LAYER_RULE:
        raise LadderError(
            f"layer {profile.target_layer} has no judged-metric rule: add one to "
            "LAYER_RULE before running a ladder on it"
        )
    result = LadderResult(
        plan=plan,
        profile_name=profile.name,
        target_layer=profile.target_layer,
        prediction=prediction,
    )

    passing: float | None = None
    failing: float | None = None
    # Layers judged on the platform overhead need it on every rung: a substrate
    # that reports no per-request service time leaves §7's metric uncomputable,
    # and the honest answer is to refuse the run, not to judge the model's own
    # 2.5 s as a platform failure (§7).
    layer_needs_overhead = profile.target_layer not in JUDGED_METRIC_READY_LAYERS
    for rps in plan.rates:
        outcome = _run_rung(
            runner=runner,
            plan=plan,
            workload=workload,
            profile=profile,
            tenants=tenants,
            rps=rps,
            expand_pool=expand_pool,
            on_stage=on_stage,
        )
        result.stages.append(outcome)
        if (
            layer_needs_overhead
            and outcome.verdict != "invalid"
            and (not outcome.has_platform_overhead)
        ):
            # Nothing above this rung would be measurable either, and a knee
            # computed from the turn latency would be a verdict about the model.
            result.status = "unjudgeable"
            return result
        if outcome.verdict == "pass":
            passing = rps
            continue
        if outcome.verdict == "invalid":
            # Not a verdict at all: the generator was the bottleneck even after
            # the pool grew, so the rung above this one was never measured. The
            # last passing rung becomes a *lower bound* on the knee, and saying
            # that is the answer - reporting the rung as a failure would blame
            # the platform for our own saturation.
            if passing is None:
                result.status = "invalid"
                return result
            # The passing rungs below were already shown to carry the judged
            # metric (the check above returns otherwise), so a lower bound here
            # rests on measurements §7 accepts.
            result.status = "knee_lower_bound"
            result.knee = KneeEstimate(
                rate=passing, bracket=None, tolerance=plan.tolerance, bisection_steps=0
            )
            _repeat_knee(
                runner,
                plan,
                workload,
                profile,
                tenants,
                passing,
                result,
                on_stage=on_stage,
                expand_pool=expand_pool,
            )
            return result
        failing = rps
        break

    if failing is None:
        # Every rung passed: nothing above the top was ever run, so the top is
        # a lower bound and there is no bracket. A zero-width bracket would
        # read as "the ceiling is exactly this rate", a claim no rung supports.
        # The status names it: "knee_found" on a ladder whose top rung passed
        # read as "the capacity is settled" (the da85632e report did exactly
        # that), which is the one conclusion the caveat §10 asks for forbids.
        result.status = (
            "knee_lower_bound" if passing is not None else "below_first_rung"
        )
        result.knee = (
            KneeEstimate(
                rate=passing,
                bracket=None,
                tolerance=plan.tolerance,
                bisection_steps=0,
            )
            if passing is not None
            else None
        )
        if passing is not None:
            _repeat_knee(
                runner,
                plan,
                workload,
                profile,
                tenants,
                passing,
                result,
                on_stage=on_stage,
                expand_pool=expand_pool,
            )
        return result

    if passing is None:
        result.status = "below_first_rung"
        return result

    low, high = passing, failing
    steps = 0
    while (high - low) / low > plan.tolerance and steps < plan.max_bisection_steps:
        # Geometric midpoint: the ladder is a factor-of-two ladder, so the point
        # that halves the *ratio* is the informative probe, not the arithmetic
        # mean (which always leans towards the failing end).
        mid = math.sqrt(low * high)
        if math.isclose(mid, low, rel_tol=1e-9) or math.isclose(
            mid, high, rel_tol=1e-9
        ):
            break
        steps += 1
        outcome = _run_rung(
            runner=runner,
            plan=plan,
            workload=workload,
            profile=profile,
            tenants=tenants,
            rps=mid,
            expand_pool=expand_pool,
            on_stage=on_stage,
        )
        result.stages.append(outcome)
        if outcome.verdict == "invalid":
            result.knee = KneeEstimate(
                rate=low,
                bracket=(low, high),
                tolerance=plan.tolerance,
                bisection_steps=steps,
            )
            _repeat_knee(
                runner,
                plan,
                workload,
                profile,
                tenants,
                low,
                result,
                on_stage=on_stage,
                expand_pool=expand_pool,
            )
            return result
        if outcome.verdict == "pass":
            low = mid
        else:
            high = mid

    result.knee = KneeEstimate(
        rate=low, bracket=(low, high), tolerance=plan.tolerance, bisection_steps=steps
    )
    _repeat_knee(
        runner,
        plan,
        workload,
        profile,
        tenants,
        low,
        result,
        on_stage=on_stage,
        expand_pool=expand_pool,
    )
    return result


def _repeat_knee(
    runner: StageRunnerPort,
    plan: LadderPlan,
    workload: WorkloadPlan,
    profile: LoadProfile,
    tenants: Sequence[str],
    rps: float,
    result: LadderResult,
    *,
    on_stage: StageSink | None = None,
    expand_pool: bool = True,
) -> None:
    """Re-run the knee rung to get the spread §2 asks for.

    Repeats write their evidence through the same sink as the rungs: the headline
    is computed from these records, so §10's canonical source for a percentile
    has to contain them. ``expand_pool`` is the caller's setting, not a constant,
    so a repeat re-measures the same rung the same way.
    """
    for index in range(max(plan.repeats - 1, 0)):
        result.repeats.append(
            _run_rung(
                runner=runner,
                plan=plan,
                workload=workload,
                profile=profile,
                tenants=tenants,
                rps=rps,
                expand_pool=expand_pool,
                on_stage=on_stage,
                run_index=index + 1,
            )
        )


def _run_rung(
    *,
    runner: StageRunnerPort,
    plan: LadderPlan,
    workload: WorkloadPlan,
    profile: LoadProfile,
    tenants: Sequence[str],
    rps: float,
    expand_pool: bool,
    on_stage: StageSink | None = None,
    run_index: int = 0,
) -> StageOutcome:
    """Run one rung, doubling the pool while the generator is the bottleneck.

    ``run_index`` is 0 for the rung that produces the verdict and 1.. for the knee
    repeats; it travels to the evidence sink so a repeat's records are not mixed
    with the run they are supposed to be a repeat of.
    """
    workers = plan.workers_for(rps, floor=profile.sessions.pool_size)
    spec = plan.spec_for(rps)
    recycle = session_recycle_for(profile)
    expansions = 0
    while True:
        stage = runner.run(
            spec, workload, tenants=tenants, workers=workers, recycle=recycle
        )
        attempt = expansions
        # §2 says an invalid stage is answered with a bigger pool, but that rule
        # assumes the invalidity came from too few slots. When the generator ran
        # out of CPU, every extra worker adds another busy-wait tail and the rung
        # gets worse: measured at 672 workers, 117% of a core, 19 dropped ticks.
        # A saturated instrument is not a reason to buy more instrument.
        generator_bound = stage.generator_bound
        if (
            stage.is_valid
            or generator_bound
            or not expand_pool
            or expansions >= plan.max_pool_expansions
        ):
            outcome = StageOutcome.from_result(
                stage,
                t_budget_ms=plan.t_budget_ms,
                max_error_rate=plan.max_error_rate,
                workers=workers,
                pool_expansions=expansions,
                step_counts=workload.step_counts(spec.total_ticks),
            )
            if on_stage is not None:
                on_stage(
                    outcome,
                    stage,
                    StageTicket(
                        target_rps=spec.target_rps,
                        attempt=attempt,
                        run_index=run_index,
                    ),
                )
            return outcome
        if on_stage is not None:
            # The discarded attempt is evidence too: it is how a rung that needed
            # a wider pool is told apart from one that held at the nominal width.
            on_stage(
                StageOutcome.from_result(
                    stage,
                    t_budget_ms=plan.t_budget_ms,
                    max_error_rate=plan.max_error_rate,
                    workers=workers,
                    pool_expansions=expansions,
                    step_counts=workload.step_counts(spec.total_ticks),
                ),
                stage,
                StageTicket(
                    target_rps=spec.target_rps,
                    attempt=attempt,
                    run_index=run_index,
                ),
            )
        workers *= 2
        expansions += 1
