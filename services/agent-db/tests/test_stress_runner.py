"""Stage scheduler contract tests (doc/stress/README.md §2, §3, §10).

Timing is injected, so the tests are deterministic: a virtual clock advances
only when the sleeper is called, and a slow SUT is a driver that advances the
same clock. That makes "the generator fell behind" a reproducible input instead
of a flake on a busy machine - which matters, because the whole point of the
scheduler is to be able to tell a slow platform from a slow generator.
"""

from __future__ import annotations

from typing import Any

import time
import pytest
from dataclasses import replace

from agent_db.stress.profile import WorkloadStep
from agent_db.stress.records import ErrorClass, RawRequestRecord
from agent_db.stress.runner import (
    LAG_INVALID_MS,
    SessionRecycle,
    StageResult,
    StageRunner,
    StageSpec,
    WorkloadPlan,
)
from agent_db.stress.transport import McpSession

INTERVAL_MS = 200.0  # 5 rps


class VirtualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def step(
    name: str = "one_tool", weight: float = 1.0, tools: tuple[str, ...] = ("db_get",)
) -> WorkloadStep:
    return WorkloadStep.model_validate(
        {"weight": weight, "name": name, "tools": list(tools), "history_turns": 0}
    )


class _RealLatencyDriver:
    """A driver whose turns take real wall time, for abort-path tests."""

    def __init__(self, *, latency_s: float) -> None:
        self.latency_s = latency_s
        self.turns = 0

    def open_session(self, tenant: str) -> McpSession:
        return McpSession(
            tenant, None, "/mcp", timeout_s=5.0, initialised=True, session_id="s"
        )  # type: ignore[arg-type]

    def close_session(self, session: McpSession) -> None:
        return

    def execute_turn(
        self, session, the_step, *, planned_ms, started_ms, tenant, scenario
    ):
        from agent_db.stress.driver import TurnExecution

        time.sleep(self.latency_s)
        self.turns += 1
        return TurnExecution(
            record=RawRequestRecord(
                planned_ms=planned_ms,
                started_ms=started_ms,
                actual_ms=self.latency_s * 1000.0,
                t_complete=self.latency_s * 1000.0,
                session_id=session.session_id or "",
                tenant=tenant,
                scenario=scenario,
            ),
            calls=(),
        )


class FakeDriver:
    """Driver whose turn costs a fixed latency and advances the virtual clock."""

    def __init__(
        self, clock: VirtualClock, *, turn_ms: float = 5.0, error_every: int = 0
    ) -> None:
        self.clock = clock
        self.turn_ms = turn_ms
        self.error_every = error_every
        self.turns: list[dict[str, Any]] = []
        self.opened: list[str] = []
        self.closed = 0

    def open_session(self, tenant: str) -> McpSession:
        self.opened.append(tenant)
        return McpSession(
            tenant,
            None,
            "/mcp",
            timeout_s=5.0,
            initialised=True,
            session_id=f"s-{tenant}",
        )  # type: ignore[arg-type]

    def close_session(self, session: McpSession) -> None:
        self.closed += 1

    def execute_turn(
        self,
        session: McpSession,
        the_step: WorkloadStep,
        *,
        planned_ms: float,
        started_ms: float,
        tenant: str,
        scenario: str,
    ) -> Any:
        from agent_db.stress.driver import ToolCallTiming, TurnExecution

        self.turns.append(
            {
                "step": the_step.name,
                "planned_ms": planned_ms,
                "started_ms": started_ms,
                "tenant": tenant,
            }
        )
        self.clock.sleep(self.turn_ms / 1000.0)
        index = len(self.turns)
        failed = bool(self.error_every) and index % self.error_every == 0
        record = RawRequestRecord(
            planned_ms=planned_ms,
            started_ms=started_ms,
            actual_ms=self.turn_ms,
            t_complete=self.turn_ms,
            session_id=session.session_id or "",
            tenant=tenant,
            scenario=scenario,
            status="error" if failed else "ok",
            http_status=429 if failed else 200,
            error_class=ErrorClass.BUDGET_429 if failed else None,
        )
        call = ToolCallTiming(
            index=0, tool=the_step.tools[0], elapsed_ms=self.turn_ms, http_status=200
        )
        return TurnExecution(record=record, calls=(call,))


class TestSessionRecycling:
    """§3's pool rotation has to be executed, not just declared.

    The profile declares ``recycle_after_turns`` precisely because the server
    refuses a session past ``ABUSE_MAX_USER_TURNS``. A harness that validates
    the field and never applies it turns that quota into a false ceiling: the
    run's tail is measured against the abuse gate instead of the platform, and
    the gate's fast refusals read as platform errors.
    """

    def test_sessions_are_replaced_after_the_declared_turn_count(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        # 5 ticks at 200 ms; a limit of 2 turns means a fresh session before
        # ticks 3 and 5 - so three sessions are opened and two are retired.
        result = virtual_runner(driver, clock).run(
            spec(),
            plan(),
            tenants=["t-1"],
            workers=1,
            recycle=SessionRecycle(turns=2, seconds=None),
        )
        assert len(driver.opened) == 3
        # Every session retired exactly once: the rotation closes as it goes, and
        # the stage closes whatever each worker still holds - not the pool list,
        # which would retire the first session twice and leak the live one.
        assert driver.closed == len(driver.opened) == 3
        assert result.session_recycles == 2

    def test_no_recycle_when_the_runner_is_not_told(self) -> None:
        # The default must stay "open once per worker": an un-declared rotation
        # would change every archived rung's meaning.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        result = virtual_runner(driver, clock).run(
            spec(), plan(), tenants=["t-1"], workers=1
        )
        assert len(driver.opened) == 1
        assert driver.closed == 1
        assert result.session_recycles == 0

    def test_a_session_is_not_replaced_after_the_last_tick(self) -> None:
        # The rotation is checked *after* a ticket is claimed, so a stage that
        # ends exactly on the limit does not pay for a handshake it will never
        # use - a wasted open would also show up as load the platform never got.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        result = virtual_runner(driver, clock).run(
            spec(rps=2.0, duration_s=1.0, warmup_s=0.0),
            plan(),
            tenants=["t-1"],
            workers=1,
            recycle=SessionRecycle(turns=2, seconds=None),
        )
        assert result.stats.count == 2
        assert len(driver.opened) == 1
        assert result.session_recycles == 0

    def test_sessions_are_replaced_after_the_declared_age(self) -> None:
        # A stage long enough to age out a session must rotate it even when the
        # turn count is nowhere near the limit: the quota is per session, and a
        # slow stage reaches it by wall clock rather than by turns.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        result = virtual_runner(driver, clock).run(
            spec(),
            plan(),
            tenants=["t-1"],
            workers=1,
            recycle=SessionRecycle(turns=None, seconds=0.5),
        )
        # Ticks land at 0, 200, 400, 600, 800 ms: only the last one is past 500 ms.
        assert len(driver.opened) == 2
        assert result.session_recycles == 1

    def test_a_turn_count_of_zero_does_not_recycle_forever(self) -> None:
        # Guards the boundary: a limit that is reached on the very first check
        # must still run the tick it claimed, not hand back its ticket.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        result = virtual_runner(driver, clock).run(
            spec(rps=5.0, duration_s=1.0, warmup_s=0.0),
            plan(),
            tenants=["t-1"],
            workers=1,
            recycle=SessionRecycle(turns=1, seconds=None),
        )
        assert result.stats.count == 5
        # One session for the first tick, then one per remaining tick.
        assert len(driver.opened) == 5
        assert driver.closed == 5

    def test_each_worker_recycles_its_own_session(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        result = virtual_runner(driver, clock).run(
            spec(rps=5.0, duration_s=1.0, warmup_s=0.0),
            plan(),
            tenants=["t-1", "t-1"],
            workers=2,
            recycle=SessionRecycle(turns=1, seconds=None),
        )
        # Five tickets over two workers, one turn per session. Which worker
        # claims which ticket is not deterministic (a virtual clock hands them
        # all to whoever wins the race), so the assertion is the invariant: each
        # worker's first session is free, every later turn buys a new one, and
        # every session is retired exactly once.
        assert result.stats.count == 5
        assert result.dropped_ticks == 0
        assert result.session_recycles == len(driver.opened) - 2
        assert driver.closed == len(driver.opened)

    def test_a_recycled_session_stays_with_its_tenant(self) -> None:
        # The replacement is a new session for the *same* tenant: rotating onto
        # a different tenant would silently rewrite the profile's tenant mix.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=5.0)
        virtual_runner(driver, clock).run(
            spec(rps=5.0, duration_s=1.0, warmup_s=0.0),
            plan(),
            tenants=["t-a", "t-b"],
            workers=2,
            recycle=SessionRecycle(turns=1, seconds=None),
        )
        assert set(driver.opened) == {"t-a", "t-b"}
        # A worker that never claimed a ticket still holds its session, and that
        # one has to be retired too - otherwise a rotation would trade a leak in
        # the measured window for a leak at the end of it.
        assert driver.closed == len(driver.opened)


def stats_ok():
    """A minimal valid aggregate, for tests that only need *a* stats object."""
    from agent_db.stress.records import summarise

    return summarise(
        [RawRequestRecord(planned_ms=0.0, started_ms=0.0, actual_ms=1.0)], 1.0
    )


def virtual_runner(driver: FakeDriver, clock: VirtualClock) -> StageRunner:
    """Runner on a virtual clock.

    ``spin_wait_s=0`` is mandatory here: the spin tail waits for a clock that
    advances, and a virtual clock only advances when the sleeper is called.
    """
    return StageRunner(driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0)


def spec(rps: float = 5.0, duration_s: float = 1.0, warmup_s: float = 0.4) -> StageSpec:
    return StageSpec(target_rps=rps, duration_s=duration_s, warmup_s=warmup_s)


def plan(*steps: WorkloadStep, seed: int = 0) -> WorkloadPlan:
    return WorkloadPlan(steps or (step(),), scenario="sqlite-testseed", seed=seed)


class TestStageSpec:
    def test_tick_budget(self) -> None:
        assert spec(rps=5.0, duration_s=1.0, warmup_s=0.4).total_ticks == 5
        assert spec(rps=0.5, duration_s=3.0, warmup_s=0.0).total_ticks == 2
        assert spec(rps=5.0, duration_s=1.0, warmup_s=0.4).warmup_ticks == 2

    def test_interval(self) -> None:
        assert spec(rps=5.0).interval_ms == pytest.approx(INTERVAL_MS)

    def test_measured_duration_excludes_warmup(self) -> None:
        assert spec(
            rps=5.0, duration_s=10.0, warmup_s=2.0
        ).measured_duration_s == pytest.approx(8.0)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"target_rps": 0.0, "duration_s": 1.0, "warmup_s": 0.0},
            {"target_rps": 1.0, "duration_s": 0.0, "warmup_s": 0.0},
            {"target_rps": 1.0, "duration_s": 10.0, "warmup_s": 10.0},
            {"target_rps": 1.0, "duration_s": 10.0, "warmup_s": 11.0},
            {"target_rps": 1.0, "duration_s": 10.0, "warmup_s": -1.0},
        ],
    )
    def test_impossible_stages_are_refused(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            StageSpec(**kwargs)


class TestWorkloadPlan:
    def test_single_step_always_wins(self) -> None:
        the_plan = plan()
        assert the_plan.step_for(0).name == "one_tool"
        assert the_plan.step_for(999).name == "one_tool"

    def test_weights_are_respected(self) -> None:
        the_plan = plan(step("heavy", 0.2), step("light", 0.8), seed=7)
        counts = the_plan.step_counts(1000)
        assert counts["light"] > counts["heavy"]
        assert 0.7 < counts["light"] / 1000 < 0.9

    def test_assignment_is_deterministic_for_an_index(self) -> None:
        # Two runs of the same profile have to schedule the same workload on the
        # same tick, whatever the thread interleaving.
        first = plan(step("a", 0.5), step("b", 0.5), seed=3)
        second = plan(step("a", 0.5), step("b", 0.5), seed=3)
        assert [first.step_for(i).name for i in range(50)] == [
            second.step_for(i).name for i in range(50)
        ]

    def test_seed_changes_the_workload(self) -> None:
        first = plan(step("a", 0.5), step("b", 0.5), seed=1)
        second = plan(step("a", 0.5), step("b", 0.5), seed=2)
        assert [first.step_for(i).name for i in range(50)] != [
            second.step_for(i).name for i in range(50)
        ]

    def test_from_profile_carries_the_scenario(self) -> None:
        from agent_db.stress import load_profile
        from pathlib import Path

        profile = load_profile(
            Path(__file__).resolve().parents[1]
            / "agent_db"
            / "stress"
            / "profiles"
            / "mcp-tool-call-l1.json"
        )
        the_plan = WorkloadPlan.from_profile(profile, seed=1)
        assert the_plan.scenario == profile.fixture
        assert the_plan.step_for(0).name in {s.name for s in profile.workload}

    def test_empty_workload_is_refused(self) -> None:
        with pytest.raises(ValueError):
            WorkloadPlan([], scenario="sqlite-testseed")


class TestAccounting:
    def test_a_mixed_llm_stamp_invalidates_the_rung_not_the_ladder(self) -> None:
        # The §7 derivation is all-or-nothing: a stage where some turns carry
        # the model's share and others do not must wear the refusal as an
        # invalidity reason - the ladder keeps going and the evidence survives.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=1.0)
        original = driver.execute_turn

        def half_stamped(session, step, **kwargs):
            execution = original(session, step, **kwargs)
            if len(driver.turns) % 2 == 0:
                return replace(
                    execution,
                    record=replace(execution.record, llm_latency_ms=0.5),
                )
            return execution

        driver.execute_turn = half_stamped  # type: ignore[method-assign]
        result = StageRunner(
            driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0
        ).run(spec(), plan(), tenants=["t-1", "t-1"], workers=2)
        assert result.derivation_failure is not None
        assert result.is_valid is False
        assert any(
            "llm latency derivation" in reason for reason in result.invalid_reasons()
        )
        # The compensated numbers still stand - the rung is invalid, not empty.
        assert result.stats.p95 > 0

    def test_stop_ends_a_running_stage_promptly(self) -> None:
        # An interrupted run must stop making load BEFORE the CLI releases the
        # lock: otherwise a successor run measures this run's leftover traffic.
        # Real time here on purpose: the point is that stop() bounds the exit,
        # not that the virtual schedule agrees.
        import threading as threading_module
        import time as time_module

        driver = _RealLatencyDriver(latency_s=0.05)
        runner = StageRunner(
            driver,
            clock=time_module.perf_counter,
            sleeper=time_module.sleep,
            spin_wait_s=0.0,
        )
        done = threading_module.Event()

        def stage() -> None:
            try:
                runner.run(
                    spec(rps=5.0, duration_s=30.0, warmup_s=0.0),
                    plan(),
                    tenants=["t-1"],
                )
            except ValueError:
                # A stopped stage has unclaimed ticks: the CLI's abort handler
                # turns this into the run's abort_reason.
                pass
            finally:
                done.set()

        thread = threading_module.Thread(target=stage, daemon=True)
        thread.start()
        time_module.sleep(0.4)  # real time: let the stage get going (calibration
        # alone sleeps ~0.4 s: 8 probes x min(interval, 50 ms) - on a loaded CI
        # runner it outlasts this sleep, and join_workers then finds no threads
        # yet: self._threads is only assigned after calibration)
        runner.stop()
        runner.join_workers(timeout_s=5.0)
        # The bound is the event wait, not the join: stop() must end the stage
        # well inside this budget on any host, fast or loaded.
        assert done.wait(timeout=15.0)
        # Unstopped, this stage runs 30 s; the stop must end it in seconds.
        assert driver.turns < 20

    def test_a_worker_that_dies_does_not_look_like_a_finished_stage(self) -> None:
        # A raised driver error used to lose the worker's remaining tickets
        # silently, and the accounting check that reports them was not part of
        # is_valid - so a shrunken sample could still be published as a pass.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=1.0)
        original = driver.execute_turn

        state = {"raised": False}

        def exploding(session, step, **kwargs):
            # Exactly one worker dies, so the survivor claims the remaining
            # tickets and the stage completes as an invalid rung.
            if not state["raised"] and len(driver.turns) >= 2:
                state["raised"] = True
                raise RuntimeError("driver gave up")
            return original(session, step, **kwargs)

        driver.execute_turn = exploding  # type: ignore[method-assign]
        # Two workers, so the survivor claims the dead worker's tickets and the
        # stage completes as an *invalid* rung instead of a ValueError.
        result = StageRunner(
            driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0
        ).run(spec(), plan(), tenants=["t-1", "t-1"], workers=2)
        assert result.worker_failures
        assert result.is_valid is False
        assert any("worker failure" in reason for reason in result.invalid_reasons())

    def test_a_lone_worker_dying_reports_its_own_error(self) -> None:
        # With one worker there is nobody left to claim the measured ticks; the
        # failure must surface as the driver error, not as "the warm-up covered
        # the whole stage", which would blame the profile for it.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=1.0)

        def exploding(session, step, **kwargs):
            raise RuntimeError("driver gave up")

        driver.execute_turn = exploding  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="driver gave up"):
            StageRunner(driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0).run(
                spec(), plan(), tenants=["t-1"]
            )

    def test_unaccounted_ticks_are_invalid_not_just_reported(self) -> None:
        stage = StageRunner(
            FakeDriver(VirtualClock()), clock=VirtualClock(), spin_wait_s=0.0
        ).run(spec(), plan(), tenants=["t-1"])
        stage.dropped_ticks = 0
        stage.records = stage.records[:-1]
        assert stage.accounted_ticks != stage.spec.total_ticks
        assert stage.is_valid is False
        assert any("unaccounted" in reason for reason in stage.invalid_reasons())

    def test_more_records_than_planned_ticks_is_worded_as_an_overshoot(self) -> None:
        # The count goes negative for an overshoot, and the string is spliced
        # verbatim into the report: "-3 tick(s) unaccounted for" reads as a broken
        # instrument, not as the arithmetic it is.
        stage = StageRunner(
            FakeDriver(VirtualClock()), clock=VirtualClock(), spin_wait_s=0.0
        ).run(spec(), plan(), tenants=["t-1"])
        stage.records = stage.records + stage.records
        assert stage.is_valid is False
        assert any(
            "more than the 5 planned" in reason for reason in stage.invalid_reasons()
        )


class TestWarmupArithmetic:
    def test_warmup_ticks_are_ceiled_so_the_filter_agrees_with_the_flag(self) -> None:
        stage_spec = spec(rps=5.0, duration_s=4.0, warmup_s=1.5)
        assert stage_spec.total_ticks == 20
        # ceil(7.5): the tick planned exactly at 1.5 s is a warm-up tick, and the
        # measured window is built from the same number.
        assert stage_spec.warmup_ticks == 8
        assert stage_spec.measured_window_s == pytest.approx(2.4)

    def test_a_stage_that_ran_every_tick_reports_the_target_rate(self) -> None:
        # With the floored warm-up count this was 12 / 2.5 s = 4.8 rps, i.e. below
        # the 0.98 x 5 = 4.9 gate in §7, and a perfectly on-schedule rung failed.
        clock = VirtualClock()
        stage_spec = spec(rps=5.0, duration_s=4.0, warmup_s=1.5)
        result = StageRunner(
            FakeDriver(clock, turn_ms=1.0),
            clock=clock,
            sleeper=clock.sleep,
            spin_wait_s=0.0,
        ).run(stage_spec, plan(), tenants=["t-1"])
        assert result.stats.achieved_rps == pytest.approx(5.0)
        assert result.meets(t_budget_ms=50.0) is True


class TestGeneratorSaturation:
    def test_a_generator_that_saturated_only_while_measuring_is_caught(self) -> None:
        # 0.1 CPU-s during the warm-up, 1.35 during the measured window: with a
        # whole-stage denominator this stage averaged well under the gate, and a
        # generator saturated while measuring would then be answered with the pool
        # doubling the gate exists to prevent.
        clock = VirtualClock()
        driver = FakeDriver(clock)
        reads = {"n": 0}

        def cpu_clock() -> float:
            # Three reads: before the stage, at the warm-up boundary, and after.
            reads["n"] += 1
            return {1: 0.0, 2: 0.10, 3: 1.45}[reads["n"]]

        runner = StageRunner(
            driver,
            clock=clock,
            sleeper=clock.sleep,
            spin_wait_s=0.0,
            cpu_clock=cpu_clock,
        )
        stage_spec = spec()
        result = runner.run(stage_spec, plan(), tenants=["t-1"])
        # Both halves are the measured window: the sample is taken when the first
        # measured tick starts, and the denominator is the window those ticks span.
        assert result.cpu_measured_s == pytest.approx(1.35)
        assert result.cpu_s == pytest.approx(1.45)
        assert result.cpu_share == pytest.approx(
            1.35 / stage_spec.measured_window_s, rel=0.01
        )
        assert result.generator_bound is True
        assert result.is_valid is False
        assert any("generator CPU" in reason for reason in result.invalid_reasons())

    def test_a_quiet_generator_is_not_a_reason_for_invalidity(self) -> None:
        result = virtual_runner(FakeDriver(VirtualClock()), VirtualClock()).run(
            spec(), plan(), tenants=["t-1"]
        )
        assert result.generator_bound is False
        assert result.is_valid is True


class TestWarmupLag:
    """Lateness is judged over the measured window, like generator CPU is.

    A warm-up exists to absorb cold pools, thread starts and first-touch page
    faults, and that is precisely when a generator is late. Charging that
    lateness to the gate in §10 invalidated rungs whose measured window ran
    exactly on schedule - and the ladder answers an invalid rung that is not
    generator-bound by doubling the pool and re-running it, so the cost was two
    wasted rungs and a knee reported as a lower bound for a reason that had
    already passed.
    """

    class _ColdStartDriver(FakeDriver):
        """Late only while inside the warm-up, exactly on schedule afterwards."""

        def __init__(
            self, clock: VirtualClock, *, stall_ms: float, until_ms: float
        ) -> None:
            super().__init__(clock, turn_ms=1.0)
            self.stall_ms = stall_ms
            self.until_ms = until_ms

        def execute_turn(self, session, the_step, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs["planned_ms"] < self.until_ms:
                # Overrun the tick's own slot, so the next tick starts late.
                self.clock.sleep(self.stall_ms / 1000.0)
            return super().execute_turn(session, the_step, **kwargs)

    def _cold_start_stage(self):
        # 5 rps / 200 ms interval, 4 s stage, 2 s warm-up = 10 warm-up ticks.
        stage_spec = spec(rps=5.0, duration_s=4.0, warmup_s=2.0)
        clock = VirtualClock()
        driver = self._ColdStartDriver(clock, stall_ms=210.0, until_ms=1000.0)
        result = StageRunner(
            driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0
        ).run(stage_spec, plan(), tenants=["t-1"])
        return stage_spec, result

    def test_lateness_confined_to_the_warmup_keeps_the_stage_valid(self) -> None:
        stage_spec, result = self._cold_start_stage()

        # The premise: the measured window is whole and ran on time.
        assert result.dropped_ticks == 0
        assert result.stats.count == stage_spec.total_ticks - stage_spec.warmup_ticks
        assert result.stats.achieved_rps == pytest.approx(5.0)
        assert max(result.measured_lag_ms) < 1.0

        # So the gate, which reads that window, lets the rung stand.
        assert result.tick_lag_p99 < LAG_INVALID_MS
        assert result.is_valid is True
        assert result.invalid_reasons() == []

    def test_the_whole_stage_lag_is_still_reported_as_evidence(self) -> None:
        _, result = self._cold_start_stage()
        # Not lost, just not a verdict: the gap between the two figures is the
        # signature of a generator that needed its warm-up.
        assert result.stage_lag_p99 > LAG_INVALID_MS
        assert result.stage_lag_p99 > result.tick_lag_p99
        assert len(result.tick_lag_ms) > len(result.measured_lag_ms)

    def test_lateness_inside_the_measured_window_still_invalidates(self) -> None:
        # The gate must keep catching what it exists for: the same stall, but
        # reaching past the warm-up boundary.
        stage_spec = spec(rps=5.0, duration_s=4.0, warmup_s=2.0)
        clock = VirtualClock()
        driver = self._ColdStartDriver(clock, stall_ms=205.0, until_ms=3000.0)
        result = StageRunner(
            driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0
        ).run(stage_spec, plan(), tenants=["t-1"])

        assert max(result.measured_lag_ms) >= LAG_INVALID_MS
        assert result.tick_lag_p99 >= LAG_INVALID_MS
        assert result.is_valid is False
        assert any("lag" in reason for reason in result.invalid_reasons())

    def test_a_hand_built_result_still_reads_its_only_lag_list(self) -> None:
        # Callers that build a StageResult directly (the ladder's tests do) pass
        # tick_lag_ms alone; the gate must not silently pass them.
        lagging = StageResult(
            spec=spec(),
            stats=stats_ok(),
            tick_lag_ms=[1.0, 2.0, LAG_INVALID_MS + 1.0],
        )
        assert lagging.tick_lag_p99 >= LAG_INVALID_MS
        assert lagging.is_valid is False


class TestWarmupDroppedTicks:
    """A dropped tick is judged over the measured window, like lag and CPU are.

    A tick dropped during warm-up costs the rung nothing: it is not in the
    sample, not in the achieved rate and not in the latency. Charging it to the
    gate invalidated stages whose measured window was whole and exactly on
    schedule - and because such a stage is not generator-bound, the ladder's
    answer was to double the pool and re-run, which cannot help a cold start
    that has already ended.
    """

    class _ColdDropDriver(FakeDriver):
        """Blows a whole tick slot inside the warm-up, perfect afterwards."""

        def __init__(
            self, clock: VirtualClock, *, stall_ms: float, until_ms: float
        ) -> None:
            super().__init__(clock, turn_ms=1.0)
            self.stall_ms = stall_ms
            self.until_ms = until_ms

        def execute_turn(self, session, the_step, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs["planned_ms"] < self.until_ms:
                self.clock.sleep(self.stall_ms / 1000.0)
            return super().execute_turn(session, the_step, **kwargs)

    def _cold_drop_stage(self, *, until_ms: float):
        # 5 rps / 200 ms interval, 4 s stage, 2 s warm-up. A 450 ms turn overruns
        # its own slot by more than one interval, so the next tick is dropped.
        stage_spec = spec(rps=5.0, duration_s=4.0, warmup_s=2.0)
        clock = VirtualClock()
        driver = self._ColdDropDriver(clock, stall_ms=450.0, until_ms=until_ms)
        result = StageRunner(
            driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0
        ).run(stage_spec, plan(), tenants=["t-1"])
        return stage_spec, result

    def test_ticks_dropped_in_the_warmup_keep_the_stage_valid(self) -> None:
        stage_spec, result = self._cold_drop_stage(until_ms=600.0)

        # The premise: ticks really were dropped, and the measured window is
        # nonetheless whole, on schedule and at the target rate.
        assert result.dropped_ticks > 0
        assert result.stats.count == stage_spec.total_ticks - stage_spec.warmup_ticks
        assert result.stats.achieved_rps == pytest.approx(5.0)

        # The verdict, asserted before the new fields so that a revert fails
        # here on semantics rather than on a missing attribute.
        assert result.is_valid is True
        assert result.invalid_reasons() == []

        assert result.warmup_dropped_ticks == result.dropped_ticks
        assert result.gating_dropped_ticks == 0

    def test_ticks_dropped_in_the_measured_window_still_invalidate(self) -> None:
        # The gate must keep catching what it exists for: the same stall, but
        # reaching past the warm-up boundary.
        _, result = self._cold_drop_stage(until_ms=3000.0)

        assert result.is_valid is False
        assert any("dropped tick" in reason for reason in result.invalid_reasons())
        assert result.gating_dropped_ticks > 0

    def test_every_planned_tick_is_still_accounted_for(self) -> None:
        # Splitting the verdict must not weaken the accounting invariant: a
        # worker that died would still shrink the sample, and that is the net.
        _, result = self._cold_drop_stage(until_ms=600.0)
        assert result.accounted_ticks == result.spec.total_ticks
        assert len(result.records) + result.dropped_ticks == result.spec.total_ticks

    def test_a_hand_built_result_still_judges_on_its_only_count(self) -> None:
        # Callers that build a StageResult directly (the ladder's tests do) pass
        # dropped_ticks alone; the gate must not silently pass them.
        dropped = StageResult(spec=spec(), stats=stats_ok(), dropped_ticks=1)
        assert dropped.gating_dropped_ticks == 1
        assert dropped.is_valid is False


class TestSessionOpening:
    def test_the_pool_is_opened_at_a_paced_rate(self) -> None:
        # Opening 20 sessions at 50/s cannot be a burst: the gaps between the
        # openings sum to 19/50 s, and no single wait exceeds one gap.
        clock = VirtualClock()
        sleeps: list[float] = []

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            clock.sleep(seconds)

        driver = FakeDriver(clock)
        runner = StageRunner(
            driver,
            clock=clock,
            sleeper=sleeper,
            spin_wait_s=0.0,
            session_open_rate=50.0,
        )
        runner.run(
            spec(rps=5.0, duration_s=0.4, warmup_s=0.1),
            plan(),
            tenants=["t-1"],
            workers=20,
        )
        assert len(driver.opened) == 20
        assert sum(sleeps[:19]) == pytest.approx(19 / 50.0, rel=0.02)
        assert max(sleeps[:19]) <= 1 / 50.0 + 1e-9

    def test_pacing_does_not_eat_the_stage_clock(self) -> None:
        # The bug this guards against already happened twice (calibration after
        # the start, dropped ticks at 20 rps): slow setup must finish *before* the
        # clock the ticks are measured against.
        clock = VirtualClock()
        driver = FakeDriver(clock)
        runner = StageRunner(
            driver,
            clock=clock,
            sleeper=clock.sleep,
            spin_wait_s=0.0,
            session_open_rate=10.0,
        )
        result = runner.run(spec(), plan(), tenants=["t-1"], workers=5)
        assert result.tick_lag_ms[0] == pytest.approx(0.0, abs=1e-9)
        assert result.dropped_ticks == 0

    def test_an_open_rate_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError, match="session_open_rate"):
            StageRunner(FakeDriver(VirtualClock()), session_open_rate=0.0)

    def test_sessions_are_spread_across_tenants_in_order(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        runner = StageRunner(driver, clock=clock, sleeper=clock.sleep, spin_wait_s=0.0)
        runner.run(spec(), plan(), tenants=["t-1", "t-2"], workers=4)
        assert driver.opened == ["t-1", "t-2", "t-1", "t-2"]


class TestStageRun:
    def test_runs_every_tick_of_the_stage(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        result = virtual_runner(driver, clock).run(spec(), plan(), tenants=["t-1"])
        assert len(result.records) == 5
        assert result.dropped_ticks == 0
        assert result.accounted_ticks == result.spec.total_ticks
        assert result.is_valid
        assert result.invalid_reasons() == []

    def test_planned_offsets_follow_the_rate(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        result = virtual_runner(driver, clock).run(spec(), plan(), tenants=["t-1"])
        assert [record.planned_ms for record in result.records] == [
            0.0,
            200.0,
            400.0,
            600.0,
            800.0,
        ]
        assert [turn["planned_ms"] for turn in driver.turns] == [
            0.0,
            200.0,
            400.0,
            600.0,
            800.0,
        ]

    def test_warmup_is_excluded_from_the_statistics_but_kept_in_raw(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        result = virtual_runner(driver, clock).run(
            spec(warmup_s=0.4), plan(), tenants=["t-1"]
        )
        assert result.warmup_ticks == 2
        assert result.stats.count == 3
        assert len(result.records) == 5
        # achieved rps is measured over the measured window only.
        assert result.stats.achieved_rps == pytest.approx(3 / 0.6)

    def test_sessions_are_opened_per_worker_and_closed(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        virtual_runner(driver, clock).run(
            spec(), plan(), tenants=["t-1", "t-2"], workers=3
        )
        assert driver.opened == ["t-1", "t-2", "t-1"]
        assert driver.closed == 3

    def test_tenants_are_distributed_round_robin(self) -> None:
        # Real time on purpose: a virtual clock only advances when someone
        # sleeps, so one worker would take every ticket and the second tenant
        # would never be exercised. The point here is the schedule, not physics.
        driver = FakeDriver(VirtualClock(), turn_ms=5.0)
        result = StageRunner(driver).run(
            spec(rps=20.0, duration_s=0.5, warmup_s=0.1),
            plan(),
            tenants=["a", "b"],
            workers=2,
        )
        assert {record.tenant for record in result.records} == {"a", "b"}
        assert result.dropped_ticks == 0

    def test_turns_run_concurrently_across_workers(self) -> None:
        # A turn slower than the interval is fine while workers are free: the
        # stage must not silently degrade to a sequential loop. Real time again:
        # with a shared virtual clock the workers would drift apart and the
        # second one would look late though it is only waiting its turn.
        driver = FakeDriver(VirtualClock(), turn_ms=150.0)
        result = StageRunner(driver).run(
            spec(rps=10.0, duration_s=0.6, warmup_s=0.1),
            plan(),
            tenants=["t-1", "t-2"],
            workers=2,
        )
        assert result.dropped_ticks == 0
        assert len(result.records) == 6
        # Deliberately no is_valid assert: this runs on whatever machine the
        # suite runs on, and tick lag above 5 ms there says something about the
        # machine, not about the scheduler. The lag gate is exercised elsewhere.

    def test_a_too_small_pool_drops_ticks_and_invalidates_the_stage(self) -> None:
        # One worker, a turn four times the interval: the generator cannot keep
        # the schedule, which must be reported as dropped ticks, not hidden.
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=800.0)
        result = virtual_runner(driver, clock).run(
            spec(rps=5.0, duration_s=1.0, warmup_s=0.2),
            plan(),
            tenants=["t-1"],
            workers=1,
        )
        assert result.dropped_ticks > 0
        assert result.is_valid is False
        assert any("dropped tick" in reason for reason in result.invalid_reasons())
        assert result.accounted_ticks == result.spec.total_ticks

    def test_verdict_refuses_an_invalid_stage_even_with_good_latency(self) -> None:
        # An invalid stage has no verdict at all: a fast p95 measured while the
        # generator was dropping ticks describes the generator, not the SUT.
        invalid = StageResult(spec=spec(), stats=stats_ok(), dropped_ticks=1)
        assert invalid.stats.p95 <= 50.0
        assert invalid.meets(t_budget_ms=50.0) is False

    def test_error_rate_is_measured_from_the_records(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=1.0, error_every=2)
        result = virtual_runner(driver, clock).run(
            spec(rps=5.0, duration_s=2.0, warmup_s=0.0), plan(), tenants=["t-1"]
        )
        assert result.stats.error_rate == pytest.approx(0.5)
        assert result.stats.error_mix == {"429_budget": 5}
        assert result.meets(t_budget_ms=50.0, max_error_rate=0.01) is False

    def test_decomposition_is_collected_for_every_turn(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock)
        result = virtual_runner(driver, clock).run(spec(), plan(), tenants=["t-1"])
        assert len(result.calls) == len(result.records) == 5
        assert result.calls[0].tool == "db_get"

    def test_generator_lag_is_reported_and_can_invalidate_a_stage(self) -> None:
        clock = VirtualClock()
        driver = FakeDriver(clock, turn_ms=1.0)
        result = virtual_runner(driver, clock).run(spec(), plan(), tenants=["t-1"])
        # An on-schedule generator has no lag at all under a virtual clock.
        assert max(abs(value) for value in result.tick_lag_ms) < 1e-6
        assert result.tick_lag_p99 == pytest.approx(0.0, abs=1e-6)

        lagging = StageResult(
            spec=spec(),
            stats=result.stats,
            tick_lag_ms=[1.0, 2.0, LAG_INVALID_MS + 1.0],
        )
        assert lagging.is_valid is False
        assert any("lag" in reason for reason in lagging.invalid_reasons())

    def test_calibrated_tail_keeps_the_generator_close_to_the_deadline(self) -> None:
        # Real time, and deliberately so: sleep granularity is what the tail
        # exists for and a virtual clock cannot express it. The tail is
        # calibrated rather than guessed - a 1 ms guess left this host 4 ms late
        # and a 10 ms guess burned 30% of a core. The median is asserted instead
        # of the max so one hiccup on a shared machine is not a flake.
        driver = FakeDriver(VirtualClock(), turn_ms=1.0)
        result = StageRunner(driver).run(
            spec(rps=20.0, duration_s=0.5, warmup_s=0.1), plan(), tenants=["t-1"]
        )
        median_lag = sorted(abs(value) for value in result.tick_lag_ms)[
            len(result.tick_lag_ms) // 2
        ]
        assert result.spin_tail_ms > 0
        assert median_lag < 2.0
        assert result.dropped_ticks == 0

    def test_an_explicit_tail_is_used_as_given(self) -> None:
        # No virtual clock here: with a pinned tail the runner spins until the
        # clock says the deadline has come, and a virtual clock only advances
        # when the sleeper is called.
        driver = FakeDriver(VirtualClock(), turn_ms=1.0)
        result = StageRunner(driver, spin_wait_s=0.003).run(
            spec(rps=20.0, duration_s=0.3, warmup_s=0.1), plan(), tenants=["t-1"]
        )
        assert result.spin_tail_ms == pytest.approx(3.0)

        zero_tail = virtual_runner(FakeDriver(VirtualClock()), VirtualClock()).run(
            spec(), plan(), tenants=["t-1"]
        )
        assert zero_tail.spin_tail_ms == 0.0

    def test_unaccounted_ticks_are_reported(self) -> None:
        result = StageResult(spec=spec(), stats=stats_ok(), records=[])
        assert any("unaccounted" in reason for reason in result.invalid_reasons())

    def test_a_spec_whose_warmup_covers_every_tick_is_refused_up_front(self) -> None:
        # Warm-up shorter than the stage but covering all of its ticks: the
        # measured window would be empty. Refusing in the spec keeps the failure
        # from surfacing as a bare ValueError in the middle of a rung, after the
        # stand has already been put to work.
        with pytest.raises(ValueError, match="measured window would be empty"):
            spec(rps=1.0, duration_s=1.0, warmup_s=0.5)

    def test_a_stage_whose_measured_ticks_all_dropped_is_refused(self) -> None:
        clock = VirtualClock()
        # One worker whose single turn outlasts the whole stage: every measured
        # tick is more than an interval late, so all of them are dropped and there
        # is nothing to summarise.
        driver = FakeDriver(clock, turn_ms=3000.0)
        with pytest.raises(ValueError, match="no measured ticks"):
            virtual_runner(driver, clock).run(
                spec(rps=5.0, duration_s=1.0, warmup_s=0.4), plan(), tenants=["t-1"]
            )

    def test_tenants_and_workers_are_validated(self) -> None:
        clock = VirtualClock()
        runner = virtual_runner(FakeDriver(clock), clock)
        with pytest.raises(ValueError, match="tenant"):
            runner.run(spec(), plan(), tenants=[])
        with pytest.raises(ValueError, match="workers"):
            runner.run(spec(), plan(), tenants=["t-1"], workers=0)
