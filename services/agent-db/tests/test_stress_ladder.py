"""Tests for the ladder (§2, §7): verdicts, pool expansion, bisection, forecast."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_db.stress.ladder import (
    DEFAULT_RATES,
    LAYER_T_BUDGET_MS,
    LadderError,
    LadderPlan,
    predict_tenant_ceiling_rps,
    run_ladder,
)
from agent_db.stress.profile import load_profile
from agent_db.stress.records import ErrorClass, RawRequestRecord, summarise
from agent_db.stress.runner import StageResult, StageSpec, WorkloadPlan

STRESS_DIR = Path(__file__).resolve().parents[1] / "agent_db" / "stress"
L1_PROFILE = STRESS_DIR / "profiles" / "mcp-tool-call-l1.json"


@pytest.fixture
def profile():
    return load_profile(L1_PROFILE)


def make_result(
    spec: StageSpec,
    *,
    latency_ms: float = 10.0,
    errors: int = 0,
    dropped: int = 0,
    lag_ms: float = 0.2,
    cpu_s: float = 0.0,
) -> StageResult:
    """A synthetic stage: every measured turn has the same latency."""
    count = spec.total_ticks - spec.warmup_ticks
    records = []
    for index in range(count):
        record = RawRequestRecord(
            planned_ms=index * spec.interval_ms + spec.warmup_s * 1000.0,
            started_ms=index * spec.interval_ms + spec.warmup_s * 1000.0,
            actual_ms=latency_ms,
            t_complete=latency_ms,
        )
        if index < errors:
            record.status = "error"
            record.error_class = ErrorClass.OTHER
        records.append(record)
    return StageResult(
        spec=spec,
        stats=summarise(records, spec.measured_duration_s),
        records=records,
        tick_lag_ms=[lag_ms] * count,
        dropped_ticks=dropped,
        warmup_ticks=spec.warmup_ticks,
        cpu_s=cpu_s,
    )


class FakeRunner:
    """A platform that passes up to ``passing_rps`` and degrades above it."""

    def __init__(
        self,
        *,
        passing_rps: float | None = None,
        latency_for=None,
        always_dropping: bool = False,
        error_rate_above: float | None = None,
    ) -> None:
        self.passing_rps = passing_rps
        self.latency_for = latency_for
        self.always_dropping = always_dropping
        self.error_rate_above = error_rate_above
        self.calls: list[tuple[float, int]] = []

    def run(self, spec, plan, *, tenants, workers=None) -> StageResult:
        assert workers is not None
        self.calls.append((spec.target_rps, workers))
        rps = spec.target_rps
        # 1.5% of one core, the shape a real generator reports.
        cpu = round(spec.measured_duration_s * 0.015, 6)
        if self.always_dropping:
            return make_result(spec, dropped=3, cpu_s=cpu)
        latency = self.latency_for(rps) if self.latency_for else 5.0
        if self.passing_rps is not None and rps > self.passing_rps:
            latency = max(latency, 500.0)
        errors = 0
        if self.error_rate_above is not None and rps > self.error_rate_above:
            errors = max(1, int(spec.total_ticks * 0.2))
        return make_result(spec, latency_ms=latency, errors=errors, cpu_s=cpu)


def plan(**kwargs) -> LadderPlan:
    base = {"rates": (5.0, 10.0), "duration_s": 2.0, "warmup_s": 0.5}
    base.update(kwargs)
    return LadderPlan(**base)


def run(profile, runner, **kwargs):
    ladder = kwargs.pop("plan", plan())
    return run_ladder(
        runner=runner,
        plan=ladder,
        workload=WorkloadPlan.from_profile(profile),
        profile=profile,
        tenants=["t-1"],
    )


class TestLadderPlanValidation:
    def test_rates_must_ascend(self):
        with pytest.raises(LadderError, match="ascend"):
            plan(rates=(10.0, 5.0))

    def test_rates_must_be_distinct(self):
        with pytest.raises(LadderError, match="distinct"):
            plan(rates=(5.0, 5.0))

    def test_rates_must_be_positive(self):
        with pytest.raises(LadderError, match="positive"):
            plan(rates=(0.0, 5.0))

    def test_empty_rate_list_is_refused(self):
        with pytest.raises(LadderError, match="at least one rate"):
            plan(rates=())

    def test_tolerance_makes_sense(self):
        with pytest.raises(LadderError, match="tolerance"):
            plan(tolerance=1.5)

    def test_defaults_come_from_the_layer_and_the_profile(self, profile):
        ladder = LadderPlan.for_profile(profile)
        assert ladder.rates == DEFAULT_RATES
        assert ladder.t_budget_ms == LAYER_T_BUDGET_MS["L1"] == 50.0
        assert ladder.duration_s == profile.arrival.duration_s

    def test_a_layer_without_a_default_budget_is_refused(self, profile):
        # L4 has no documented T, and an invented one produces a verdict with no
        # basis; the caller has to supply it.
        layer_four = profile.model_copy(update={"target_layer": "L4"})
        with pytest.raises(LadderError, match="no default T"):
            LadderPlan.for_profile(layer_four)

    def test_l4_is_accepted_with_an_explicit_budget(self, profile):
        layer_four = profile.model_copy(update={"target_layer": "L4"})
        assert (
            LadderPlan.for_profile(layer_four, t_budget_ms=3000.0).t_budget_ms == 3000.0
        )

    def test_budget_preset_on_refuses_a_ladder(self, profile):
        with pytest.raises(LadderError, match="budget_preset"):
            LadderPlan.for_profile(profile.model_copy(update={"budget_preset": "on"}))


class TestPoolSizing:
    def test_workers_come_from_the_criterion_not_from_observation(self):
        ladder = plan(rates=(80.0,), t_budget_ms=50.0, pool_margin_s=1.0)
        # ceil(80 x (0.05 + 1.0)) = ceil(84.0)
        assert ladder.workers_for(80.0) == 84

    def test_the_profile_pool_is_a_floor(self, profile):
        ladder = plan(rates=(5.0,), t_budget_ms=50.0)
        assert ladder.workers_for(5.0, floor=profile.sessions.pool_size) == 32

    def test_headroom_multiplies_the_requirement(self):
        ladder = plan(rates=(20.0,), pool_margin_s=1.0, pool_headroom=2.0)
        assert ladder.workers_for(20.0) == 42


class TestKneeSearch:
    def test_the_knee_is_the_last_passing_rung(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0, 20.0, 40.0, 80.0)))
        assert result.status == "knee_found"
        assert [stage.verdict for stage in result.stages[:3]] == [
            "pass",
            "pass",
            "pass",
        ]
        assert result.stages[3].verdict == "fail"
        assert result.knee.rate is not None and 20.0 <= result.knee.rate <= 25.0

    def test_the_ladder_stops_at_the_first_failure(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        run(profile, runner, plan=plan(rates=(5.0, 10.0, 20.0, 40.0, 80.0)))
        assert 80.0 not in [rps for rps, _ in runner.calls]

    def test_bisection_narrows_the_bracket_to_the_tolerance(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        result = run(
            profile,
            runner,
            plan=plan(rates=(20.0, 40.0), tolerance=0.10, max_bisection_steps=8),
        )
        low, high = result.knee.bracket
        assert (high - low) / low <= 0.10
        assert result.knee.bisection_steps > 0

    def test_bisection_never_reports_a_rate_the_platform_did_not_hold(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        result = run(
            profile, runner, plan=plan(rates=(20.0, 40.0), max_bisection_steps=8)
        )
        assert result.knee.rate <= 25.0

    def test_the_first_rung_failing_is_not_a_knee_near_zero(self, profile):
        runner = FakeRunner(passing_rps=1.0)
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0)))
        assert result.status == "below_first_rung"
        assert result.knee is None

    def test_every_rung_passing_leaves_the_top_as_a_bracket(self, profile):
        runner = FakeRunner(passing_rps=1000.0)
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0)))
        assert result.knee.rate == 10.0
        assert result.knee.bracket == (10.0, 10.0)

    def test_an_error_rate_above_the_budget_fails_even_with_good_latency(self, profile):
        runner = FakeRunner(
            passing_rps=None, latency_for=lambda rps: 5.0, error_rate_above=5.0
        )
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0)))
        assert result.stages[0].verdict == "pass"
        assert result.stages[1].verdict == "fail"


class TestInvalidStages:
    def test_the_pool_is_doubled_twice_before_a_stage_is_declared_invalid(
        self, profile
    ):
        runner = FakeRunner(always_dropping=True)
        result = run(profile, runner, plan=plan(rates=(5.0,), max_pool_expansions=2))
        outcome = result.stages[0]
        assert outcome.verdict == "invalid"
        assert outcome.pool_expansions == 2
        workers = [workers for _, workers in runner.calls]
        assert workers == [workers[0], workers[0] * 2, workers[0] * 4]

    def test_a_stage_that_recovers_after_expansion_keeps_its_verdict(self, profile):
        class RecoveringRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None):
                if len(self.calls) == 0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=1)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        runner = RecoveringRunner(passing_rps=100.0)
        result = run(profile, runner, plan=plan(rates=(5.0,)))
        assert result.stages[0].verdict == "pass"
        assert result.stages[0].pool_expansions == 1

    def test_an_invalid_first_rung_gives_no_knee(self, profile):
        runner = FakeRunner(always_dropping=True)
        result = run(profile, runner, plan=plan(rates=(5.0,)))
        assert result.status == "invalid"
        assert result.knee is None

    def test_an_invalid_rung_after_a_pass_is_a_lower_bound_only(self, profile):
        class HalfBrokenRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None):
                if spec.target_rps > 5.0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=2)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        runner = HalfBrokenRunner(passing_rps=100.0)
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0)))
        assert result.status == "knee_found"
        assert result.knee.rate == 5.0
        assert result.knee.bracket is None


class TestGeneratorSaturation:
    def test_the_pool_is_not_doubled_when_the_generator_is_the_bottleneck(
        self, profile
    ):
        # §2 answers an invalid rung with a bigger pool, which is wrong when the
        # invalidity came from the generator's own CPU: every extra worker adds
        # another busy-wait tail. Measured live: 672 workers, 117% of a core,
        # 19 dropped ticks.
        class SaturatedRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None):
                self.calls.append((spec.target_rps, workers))
                return make_result(
                    spec,
                    cpu_s=spec.measured_duration_s * 0.95,
                    dropped=5,
                )

        runner = SaturatedRunner(always_dropping=True)
        result = run(profile, runner, plan=plan(rates=(5.0,), max_pool_expansions=2))
        assert len(runner.calls) == 1, (
            "a saturated generator must not be given more workers"
        )
        assert result.stages[0].verdict == "invalid"
        assert result.stages[0].generator_bound is True

    def test_an_undersized_pool_is_still_answered_with_more_workers(self, profile):
        class StarvedRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None):
                if len(self.calls) == 0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=4, cpu_s=0.1)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        runner = StarvedRunner(passing_rps=1000.0)
        result = run(profile, runner, plan=plan(rates=(5.0,), max_pool_expansions=2))
        assert result.stages[0].verdict == "pass"
        assert result.stages[0].pool_expansions == 1


class TestEvidenceSink:
    def test_every_rung_hands_its_raw_records_to_the_sink(self, profile):
        seen: list[tuple[float, int]] = []
        runner = FakeRunner(passing_rps=25.0)

        def sink(outcome, stage):
            seen.append((outcome.target_rps, len(stage.records)))

        result = run_ladder(
            runner=runner,
            plan=plan(rates=(5.0, 10.0, 20.0, 40.0)),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
            on_stage=sink,
        )
        assert [rps for rps, _ in seen] == [s.target_rps for s in result.stages]
        assert all(count > 0 for _, count in seen)

    def test_a_ladder_without_a_sink_still_works(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        assert run(profile, runner, plan=plan(rates=(5.0,))).stages[0].verdict == "pass"


class TestRepeats:
    def test_repeats_add_runs_and_a_spread(self, profile):
        runner = FakeRunner(passing_rps=1000.0)
        result = run(profile, runner, plan=plan(rates=(5.0, 10.0), repeats=3))
        assert len(result.repeats) == 2
        headline = result.headline()
        assert headline["runs"] == 3
        assert headline["p95_min"] <= headline["p95_median"] <= headline["p95_max"]

    def test_a_single_run_still_reports_its_own_measurement(self, profile):
        runner = FakeRunner(passing_rps=1000.0)
        result = run(profile, runner, plan=plan(rates=(5.0,), repeats=1))
        assert result.headline()["runs"] == 1


class TestPrediction:
    def test_the_model_is_per_tenant_serialization(self):
        prediction = predict_tenant_ceiling_rps(
            p95_tool_ms=10.0, calls_per_turn=2.0, tenants=4
        )
        # 1 / (0.010 s x 2 calls) x 4 tenants = 200 rps
        assert prediction.rate == pytest.approx(200.0)
        assert "call_lock" in prediction.model

    def test_inputs_are_recorded_with_the_forecast(self):
        prediction = predict_tenant_ceiling_rps(
            p95_tool_ms=8.0, calls_per_turn=3.0, tenants=8
        )
        assert prediction.inputs == {
            "p95_tool_ms": 8.0,
            "calls_per_turn": 3.0,
            "tenants": 8.0,
        }

    def test_a_nonsensical_input_is_refused(self):
        with pytest.raises(LadderError, match="p95_tool_ms"):
            predict_tenant_ceiling_rps(p95_tool_ms=0.0, calls_per_turn=2.0, tenants=1)

    def test_the_prediction_travels_with_the_result(self, profile):
        runner = FakeRunner(passing_rps=25.0)
        prediction = predict_tenant_ceiling_rps(
            p95_tool_ms=10.0, calls_per_turn=2.0, tenants=4
        )
        result = run_ladder(
            runner=runner,
            plan=plan(rates=(20.0, 40.0)),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
            prediction=prediction,
        )
        assert result.prediction is prediction
