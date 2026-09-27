"""Open-loop stage scheduler (§2, §3, §10).

The arrival rate is the input and the achieved rate is the result: a tick is
planned at ``index / rps`` and a turn is executed when that time comes, whether
or not the previous turn has finished. That is what makes coordinated omission
real, and it is why the pool has to be sized from the criterion (§3) rather than
grown until the stage stops dropping ticks.

Concurrency is per-session: each worker owns one session for the whole stage,
because a second concurrent request on the same session would measure our own
queue instead of the platform's. Workers are threads, not an event loop, and
lateness is measured directly as tick lag - the quantity the flag in §10 is
actually about, and the one that turns into a dropped tick when it exceeds a
whole interval.

A late tick is not the same as a missed slot. Being a few milliseconds late is
normal and is exactly what the CO compensation in :mod:`agent_db.stress.records`
corrects for; a tick whose planned time is a whole interval or more in the past
cannot be run late without silently re-timing the stage, so it is dropped and
counted. Any dropped tick invalidates the stage (§3), and the answer is a
bigger pool, not a lower rate.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

from .driver import ToolCallTiming, TurnExecution
from .profile import LoadProfile, WorkloadStep
from .records import RawRequestRecord, StageStats, percentile, summarise
from .transport import McpSession

# §10: the generator's own lateness is first-class evidence, and a stage whose
# p99 exceeds this is invalid no matter how good the SUT looks.
LAG_INVALID_MS = 5.0

# How fast the generator may open its session pool, in sessions per second: it
# paces its own handshakes by this.
#
# Measured on the isolated stand 2026-09-27: opening a 168-session pool in a tight
# loop produced ~600-700 requests (a session costs ``initialize`` plus
# ``notifications/initialized``) inside ~200 ms - an instantaneous rate near
# 3000/s. The gateway rate-limits per IP, so the rung died with HTTP 429 *before
# its first tick*: the generator was measuring its own handshake burst. Pacing
# costs wall clock before the stage clock starts, and changes nothing measured
# inside the stage.
SESSION_OPEN_RATE = 100.0

# Share of one core the generator may spend before its own saturation becomes the
# explanation for the rung's numbers.
#
# Measured on the isolated stand 2026-09-27: at 160 rps the pool had been doubled
# twice (672 workers), the spin tails alone cost 117% of a core, the rung posted
# 19 dropped ticks and lag p99 6.3 ms - and doubling the pool again would have made
# it worse, because every extra worker adds another busy-wait tail. A stage whose
# generator is this busy is not evidence about the platform, so it is invalid and,
# unlike an undersized pool, it must not be answered with a bigger pool.
GENERATOR_CPU_LIMIT = 0.8

# A calibration sample above this would mean the host cannot keep time at all;
# the tail is capped so the generator does not answer that with a busy loop.
CALIBRATED_TAIL_CAP_MS = 25.0

# Longest sleep used for calibration. The overshoot grows with the duration, but
# probing for a full second per sample would cost more than the stage itself.
CALIBRATION_PROBE_CAP_S = 0.05


class Driver(Protocol):
    """What the scheduler needs from any layer's driver (§11)."""

    def open_session(self, tenant: str) -> McpSession: ...

    def close_session(self, session: McpSession) -> None: ...

    def execute_turn(
        self,
        session: McpSession,
        step: WorkloadStep,
        *,
        planned_ms: float,
        started_ms: float,
        tenant: str,
        scenario: str,
    ) -> TurnExecution: ...


@dataclass(frozen=True)
class StageSpec:
    """One rung of the ladder: a rate, a duration and a warm-up window."""

    target_rps: float
    duration_s: float
    warmup_s: float

    def __post_init__(self) -> None:
        if self.target_rps <= 0:
            raise ValueError(f"target_rps must be positive, got {self.target_rps}")
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {self.duration_s}")
        if self.warmup_s < 0:
            raise ValueError(f"warmup_s must not be negative, got {self.warmup_s}")
        if self.warmup_s >= self.duration_s:
            raise ValueError(
                f"warmup_s ({self.warmup_s}) must be shorter than duration_s "
                f"({self.duration_s}): otherwise the stage measures nothing"
            )
        if self.warmup_ticks >= self.total_ticks:
            # The declared warm-up is shorter than the stage but covers every tick
            # of it (warm-up within one interval of the end): the measured window
            # would be empty and the failure would surface as a bare ValueError in
            # the middle of a rung, after the stand had already been asked to run.
            raise ValueError(
                f"warm-up covers all {self.total_ticks} tick(s) of a "
                f"{self.duration_s} s stage at {self.target_rps} rps "
                f"(ceil({self.warmup_s} x {self.target_rps}) = {self.warmup_ticks}): "
                "the measured window would be empty"
            )

    @property
    def interval_ms(self) -> float:
        return 1000.0 / self.target_rps

    @property
    def total_ticks(self) -> int:
        return int(math.ceil(self.duration_s * self.target_rps))

    @property
    def measured_duration_s(self) -> float:
        return self.duration_s - self.warmup_s

    @property
    def warmup_ticks(self) -> int:
        """Ticks whose planned time falls inside the warm-up window.

        Ceiled, and this is the *only* definition: the measured-window
        denominator and the ``planned_ms >= warmup`` filter both derive from it.
        A floored version disagreed with the filter by one tick, and the
        discrepancy made ``achieved_rps`` fall short of the target on short
        stages - a rung that executed every tick exactly on schedule could fail
        the ``achieved >= 0.98 x target`` half of §7.
        """
        return min(math.ceil(self.warmup_s * self.target_rps), self.total_ticks)

    @property
    def measured_window_s(self) -> float:
        """Wall time the measured ticks span: ``(total - warmup) / rps``.

        ``duration - warmup`` is the wall clock of the stage, but the ticks are
        integral: the rate a stage that ran every tick on schedule achieved is
        exactly ``rps`` only when the denominator is built from the tick count.
        """
        return (self.total_ticks - self.warmup_ticks) / self.target_rps


@dataclass
class StageResult:
    """Everything one stage produced, valid or not."""

    spec: StageSpec
    stats: StageStats
    records: list[RawRequestRecord] = field(default_factory=list)
    calls: list[ToolCallTiming] = field(default_factory=list)
    tick_lag_ms: list[float] = field(default_factory=list)
    dropped_ticks: int = 0
    reinitialisations: int = 0
    warmup_ticks: int = 0
    # The busy-wait tail this run used, in ms: a number the report prints rather
    # than a hidden knob, because it is part of how the generator behaved.
    spin_tail_ms: float = 0.0
    # Process CPU seconds spent by the stage, all threads summed. The generator
    # is part of the instrument, so its cost is evidence rather than trivia: a
    # stage measured by a saturated generator is a stage about the generator.
    cpu_s: float = 0.0
    # CPU spent inside the measured window only, sampled when the first measured
    # tick starts. The whole-stage figure cannot judge saturation: with a 300 s
    # stage and a 60 s warm-up, a generator burning 0.85 cores per measured second
    # averages 0.68 over the stage and would escape the gate - and then be
    # answered with the pool doubling the gate exists to prevent.
    cpu_measured_s: float = 0.0
    # Worker threads report here instead of dying silently: a raised driver or
    # transport error would otherwise lose its remaining tickets and quietly
    # shrink the sample (the accounting check in ``is_valid`` is the net).
    worker_failures: list[str] = field(default_factory=list)

    @property
    def tick_lag_p99(self) -> float:
        return percentile(self.tick_lag_ms, 0.99) if self.tick_lag_ms else 0.0

    @property
    def cpu_share(self) -> float:
        """Generator CPU per second of the *measured window*, in cores.

        Both halves come from the measured window, so neither dilutes nor inflates
        the ratio: the CPU clock is sampled when the first measured tick starts
        (not at the stage boundary, which would charge warm-up work to the measured
        ticks) and the denominator is the window the ticks span. The whole-stage
        ``cpu_s`` stays available for the run artefact so a reader can rebuild
        either figure.
        """
        window = self.spec.measured_window_s
        return self.cpu_measured_s / window if window > 0 else 0.0

    @property
    def generator_bound(self) -> bool:
        """True when the instrument, not the platform, was the bottleneck."""
        return self.cpu_share >= GENERATOR_CPU_LIMIT

    @property
    def accounted_ticks(self) -> int:
        """Ticks that either ran or were counted as dropped."""
        return len(self.records) + self.dropped_ticks

    @property
    def is_valid(self) -> bool:
        """§3 and §10: every tick accounted for, none dropped, the generator on schedule.

        The accounting check is not decoration: ``invalid_reasons()`` reports
        unaccounted ticks, and a validity rule that ignored its own report could
        publish a rung whose sample shrank because a worker died.
        """
        if self.generator_bound:
            return False
        if self.worker_failures:
            return False
        if self.dropped_ticks:
            return False
        if self.tick_lag_p99 >= LAG_INVALID_MS:
            return False
        return self.accounted_ticks == self.spec.total_ticks

    def invalid_reasons(self) -> list[str]:
        reasons = []
        if self.generator_bound:
            reasons.append(
                f"generator CPU {self.cpu_share * 100:.0f}% of one core during the "
                f"measured window >= "
                f"{GENERATOR_CPU_LIMIT * 100:.0f}%: the instrument saturated, so the "
                "latency measured here is partly its own queue"
            )
        if self.worker_failures:
            reasons.extend(
                f"worker failure: {failure}" for failure in self.worker_failures
            )
        if self.dropped_ticks:
            reasons.append(f"{self.dropped_ticks} dropped tick(s)")
        if self.tick_lag_p99 >= LAG_INVALID_MS:
            reasons.append(
                f"generator lag p99 {self.tick_lag_p99:.1f} ms >= {LAG_INVALID_MS} ms"
            )
        actual = len(self.records) + self.dropped_ticks
        if actual < self.spec.total_ticks:
            reasons.append(f"{self.spec.total_ticks - actual} tick(s) unaccounted for")
        elif actual > self.spec.total_ticks:
            reasons.append(
                f"{actual - self.spec.total_ticks} tick(s) more than the "
                f"{self.spec.total_ticks} planned"
            )
        return reasons

    def meets(self, t_budget_ms: float, max_error_rate: float = 0.01) -> bool:
        """The capacity criterion (§7), and only for a valid stage."""
        return self.is_valid and self.stats.meets(
            t_budget_ms, max_error_rate, target_rps=self.spec.target_rps
        )


class WorkloadPlan:
    """Deterministic step assignment: tick index in, workload step out.

    Seeded per index rather than drawn from one shared stream, so the workload a
    tick gets does not depend on which worker thread arrived first. Without that,
    two runs of the same profile would not be the same run.
    """

    def __init__(
        self, steps: Sequence[WorkloadStep], *, scenario: str, seed: int = 0
    ) -> None:
        if not steps:
            raise ValueError("a workload plan needs at least one step")
        self.steps = list(steps)
        self.scenario = scenario
        self.seed = seed
        self._cumulative: list[float] = []
        running = 0.0
        for step in self.steps:
            running += step.weight
            self._cumulative.append(running)

    @classmethod
    def from_profile(cls, profile: LoadProfile, *, seed: int = 0) -> WorkloadPlan:
        return cls(profile.workload, scenario=profile.fixture, seed=seed)

    def step_for(self, index: int) -> WorkloadStep:
        draw = random.Random(f"{self.seed}:{index}").random()
        for step, bound in zip(self.steps, self._cumulative):
            if draw <= bound:
                return step
        return self.steps[-1]

    def step_counts(self, ticks: int) -> dict[str, int]:
        """How often each step is planned to run - the run's shape, recorded."""
        counts: dict[str, int] = {step.name: 0 for step in self.steps}
        for index in range(ticks):
            counts[self.step_for(index).name] += 1
        return counts


@dataclass
class _WorkerBuffer:
    records: list[RawRequestRecord] = field(default_factory=list)
    calls: list[ToolCallTiming] = field(default_factory=list)
    lag_ms: list[float] = field(default_factory=list)
    dropped: int = 0
    reinitialisations: int = 0
    failures: list[str] = field(default_factory=list)


class _WarmupBoundary:
    """Samples the CPU clock when the first measured tick is about to run.

    The whole-stage CPU figure cannot judge saturation (warm-up dilutes it) and a
    monitor thread or a timed read would fight the injected clock the tests use.
    Reading the clock at the boundary costs one call, needs no thread, and lands
    on the moment the measured window starts.
    """

    def __init__(self, cpu_clock: Callable[[], float], warmup_ms: float) -> None:
        self._cpu_clock = cpu_clock
        self._warmup_ms = warmup_ms
        self._lock = threading.Lock()
        self.cpu_at_boundary: float | None = None

    def note(self, planned_ms: float) -> None:
        """Called by a worker before a turn; the first measured tick wins."""
        if planned_ms < self._warmup_ms or self.cpu_at_boundary is not None:
            return
        with self._lock:
            if self.cpu_at_boundary is None:
                self.cpu_at_boundary = self._cpu_clock()


class _Schedule:
    """Shared ticket dispenser plus the counters workers cannot keep locally."""

    def __init__(self, total_ticks: int) -> None:
        self.total_ticks = total_ticks
        self._lock = threading.Lock()
        self._next = 0

    def next_index(self) -> int | None:
        with self._lock:
            if self._next >= self.total_ticks:
                return None
            index = self._next
            self._next += 1
            return index


class StageRunner:
    """Runs one stage of the ladder and reports what happened."""

    def __init__(
        self,
        driver: Driver,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        spin_wait_s: float | None = None,
        cpu_clock: Callable[[], float] | None = None,
        session_open_rate: float = SESSION_OPEN_RATE,
    ) -> None:
        """``spin_wait_s`` is the busy-wait tail before a deadline.

        ``sleep`` is granular, and the overshoot is a property of the host, not
        of the load: measured on Darwin it comes in quanta of 5-10 ms with the
        generator at 2% CPU, which alone breaks the ``p99 < 5 ms`` gate in §10
        and would make every stage invalid for a reason that has nothing to do
        with the SUT. Sleeping all but the tail and then waiting keeps the
        generator's own error under the gate.

        ``None`` (the default) calibrates the tail once per run by measuring how
        late this host actually wakes up. A guessed tail is either useless (1 ms
        leaves a 4 ms floor) or expensive (10 ms burns 30% of a core at 50 rps),
        and the right value differs by an order of magnitude between hosts. Pass
        an explicit value to pin it, or 0 for plain sleeps - which tests on a
        virtual clock must do, since spinning waits for a clock that advances.
        """
        self.driver = driver
        self.clock = clock or time.perf_counter
        self.clock_cpu = cpu_clock or time.process_time
        self.sleeper = sleeper or time.sleep
        if session_open_rate <= 0:
            raise ValueError(
                f"session_open_rate must be positive, got {session_open_rate}"
            )
        self.session_open_rate = session_open_rate
        self._spin_wait_s = spin_wait_s
        self._calibrated_tail_ms: float | None = None

    def run(
        self,
        spec: StageSpec,
        plan: WorkloadPlan,
        *,
        tenants: Sequence[str],
        workers: int | None = None,
    ) -> StageResult:
        if not tenants:
            raise ValueError("a stage needs at least one tenant")
        worker_count = len(tenants) if workers is None else workers
        if worker_count < 1:
            raise ValueError(f"workers must be positive, got {worker_count}")

        sessions = self._open_sessions(tenants, worker_count)
        schedule = _Schedule(spec.total_ticks)
        buffers = [_WorkerBuffer() for _ in range(worker_count)]
        interval = spec.interval_ms
        # Calibration must finish before the stage clock starts: it sleeps for a
        # few hundred ms, and doing that after the start would make the first
        # ticks late and drop them (measured: 9 dropped ticks at 20 rps).
        tail_ms = self._tail_ms(interval / 1000.0 * worker_count)
        warmup_ms = spec.warmup_s * 1000.0

        boundary = _WarmupBoundary(self.clock_cpu, warmup_ms)

        def worker(worker_id: int) -> None:
            session = sessions[worker_id]
            buffer = buffers[worker_id]
            # Workers are created before the stage clock starts and released only
            # after it is read: with a 168-worker pool, starting threads inside
            # the stage would spend 20-30 ms of it, and at 160 rps that lateness
            # lands inside the warm-up where it invalidates the rung for no
            # reason but our own startup. Being late is not the same as being
            # unable to keep up.
            gate.wait()
            try:
                while True:
                    index = schedule.next_index()
                    if index is None:
                        return
                    planned_ms = index * interval
                    wait = planned_ms - (self.clock() - started) * 1000.0
                    if wait > 0:
                        self._wait_until(planned_ms, started, tail_ms)
                    now_ms = (self.clock() - started) * 1000.0
                    buffer.lag_ms.append(now_ms - planned_ms)
                    if now_ms - planned_ms >= interval:
                        # Running this tick now would re-time the stage: the slot
                        # is gone, and calling it "late but fine" would hide the
                        # fact that the generator, not the platform, set the
                        # ceiling.
                        buffer.dropped += 1
                        continue
                    boundary.note(planned_ms)
                    execution = self.driver.execute_turn(
                        session,
                        plan.step_for(index),
                        planned_ms=planned_ms,
                        started_ms=now_ms,
                        tenant=session.tenant,
                        scenario=plan.scenario,
                    )
                    buffer.records.append(execution.record)
                    buffer.calls.extend(execution.calls)
                    buffer.reinitialisations += execution.reinitialisations
            except BaseException as exc:  # noqa: BLE001 - reported as invalidity
                # A worker that dies must not look like a stage that finished:
                # its remaining tickets would simply never be claimed, and the
                # accounting check turns that into an invalid rung.
                buffer.failures.append(f"{type(exc).__name__}: {exc}")
                worker_errors.append(exc)

        worker_errors: list[BaseException] = []
        gate = threading.Event()
        threads = [
            threading.Thread(
                target=worker, args=(index,), name=f"stress-worker-{index}"
            )
            for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        started = self.clock()
        cpu_before = self.clock_cpu()
        gate.set()
        for thread in threads:
            thread.join()
        cpu_after = self.clock_cpu()
        for session in sessions:
            self.driver.close_session(session)

        records = [record for buffer in buffers for record in buffer.records]
        records.sort(key=lambda record: record.planned_ms)
        calls = [call for buffer in buffers for call in buffer.calls]
        lag = [value for buffer in buffers for value in buffer.lag_ms]
        lag.sort()
        measured = [record for record in records if record.planned_ms >= warmup_ms]
        if not measured:
            if worker_errors:
                # The explanation is a dead worker, not the warm-up: with a
                # single worker its death leaves no one to claim the measured
                # ticks, and "no measured ticks" would blame the profile for a
                # failure the driver caused.
                raise worker_errors[0]
            raise ValueError(
                f"stage {spec.target_rps} rps produced no measured ticks: "
                f"warm-up covers {spec.warmup_ticks} of {spec.total_ticks} ticks"
            )

        return StageResult(
            spec=spec,
            stats=summarise(measured, spec.measured_window_s),
            records=records,
            calls=calls,
            tick_lag_ms=lag,
            dropped_ticks=sum(buffer.dropped for buffer in buffers),
            reinitialisations=sum(buffer.reinitialisations for buffer in buffers),
            warmup_ticks=len(records) - len(measured),
            spin_tail_ms=tail_ms,
            cpu_s=cpu_after - cpu_before,
            cpu_measured_s=(
                cpu_after - boundary.cpu_at_boundary
                if boundary.cpu_at_boundary is not None
                else cpu_after - cpu_before
            ),
            worker_failures=[
                failure for buffer in buffers for failure in buffer.failures
            ],
        )

    def _open_sessions(
        self, tenants: Sequence[str], worker_count: int
    ) -> list[McpSession]:
        """Open one session per worker, paced so the handshakes are not a burst.

        Pacing lives here rather than in a driver because it is a property of the
        generator, not of the layer: whatever is being measured, opening a wide
        pool in a tight loop spends a rate budget the stage has not started using
        yet. Sessions are opened sequentially in one thread, which is also what
        makes the pacing meaningful.
        """
        minimum_gap = 1.0 / self.session_open_rate
        started = self.clock()
        sessions: list[McpSession] = []
        for index in range(worker_count):
            due = index * minimum_gap
            spent = self.clock() - started
            if spent < due:
                self.sleeper(due - spent)
            sessions.append(self.driver.open_session(tenants[index % len(tenants)]))
        return sessions

    def _wait_until(self, deadline_ms: float, started: float, tail_ms: float) -> None:
        """Sleep up to the deadline, then hold the tail by waiting.

        The spin loop assumes a clock that advances by itself; a virtual clock in
        tests never does, which is why those tests pass ``spin_wait_s=0``.
        """
        remaining_s = (deadline_ms - (self.clock() - started) * 1000.0) / 1000.0
        tail_s = tail_ms / 1000.0
        if tail_s > 0 and remaining_s > tail_s:
            self.sleeper(remaining_s - tail_s)
            while (self.clock() - started) * 1000.0 < deadline_ms:
                pass
            return
        if remaining_s > 0:
            self.sleeper(remaining_s)

    def _tail_ms(self, expected_wait_s: float) -> float:
        """The busy-wait tail to use, calibrating this host once if needed."""
        if self._spin_wait_s is not None:
            return self._spin_wait_s * 1000.0
        if self._calibrated_tail_ms is None:
            self._calibrated_tail_ms = self._calibrate_tail_ms(expected_wait_s)
        return self._calibrated_tail_ms

    def _calibrate_tail_ms(self, expected_wait_s: float, samples: int = 8) -> float:
        """Measure how late this host wakes from a sleep of the length we will use.

        The overshoot depends on the requested duration, so calibrating with an
        unrelated probe misleads in both directions: a 5 ms probe on Darwin
        reports ~2.5 ms while a 20 ms wait actually wakes ~10 ms late. The probe
        is therefore the wait a worker will really take (``interval x workers``),
        clamped to ``CALIBRATION_PROBE_CAP_S`` (50 ms) because realistic rungs
        ask for far longer sleeps (168 workers at 160 rps wait ~1 s) and probing
        a full second per sample would cost more than the stage it is preparing.
        The result is cached for the whole run, so later rungs reuse it.

        Calibration is per run, not per tick: the overshoot is a property of the
        ``sleep`` implementation and the host's timer coalescing, both stable
        within a run. Capped so a pathological sample cannot turn the generator
        into a busy loop.
        """
        probe_s = min(max(expected_wait_s, 0.005), CALIBRATION_PROBE_CAP_S)
        worst_ms = 0.0
        for _ in range(samples):
            before = self.clock()
            self.sleeper(probe_s)
            overshoot_ms = (self.clock() - before - probe_s) * 1000.0
            worst_ms = max(worst_ms, overshoot_ms)
        return min(max(worst_ms + 0.5, 0.5), CALIBRATED_TAIL_CAP_MS)
