"""Evidence layout for one stress run (doc/stress/README.md §10).

The layout is a contract: the numbers in a report are only checkable if the
records they were computed from are on disk in the shape the report names, and
the report itself is only comparable if the manifest next to it says what was
measured. So this module owns *where* things go, and nothing else computes a
percentile: the raw records written here are the canonical source §2 defines,
and :mod:`agent_db.stress.records` is the only calculator.

    test-results/stress/<run_uuid>/
      run-manifest.json    # environment/code split (§6), blockers included
      preflight.json       # what was checked before the first tick
      profile.json         # the profile exactly as it was run
      status.json          # running | completed | failed | aborted (+ reason)
      raw/<ticket>.jsonl   # one record per request, warm-up included; the ticket
                           # is <rps>rps-run<k>-attempt<n> (repeats and pool-expansion
                           # attempts land in their own files, not one mixed stage file)
      raw/<ticket>.calls.jsonl  # per-call decomposition, no planned offsets of its own
      generator/<stage>.json  # generator CPU, lag, dropped ticks, pool width
      server/<service>.json   # /metrics snapshots taken around the run
      host/<service>.json     # container CPU/RAM from `docker stats`, when available
      stub/<stage>.jsonl      # the stub's own per-request timings (§4)
      report.json
      report-<run_uuid>.md
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Sequence

from .driver import ToolCallTiming
from .guard import write_json_atomic
from .ladder import StageOutcome, StageTicket
from .profile import LoadProfile
from .records import RawRequestRecord, write_raw_records
from .runner import StageResult


@dataclass(frozen=True)
class RunLayout:
    """Every path one run writes to, created up front."""

    root: Path

    #: Directories the harness declares in §10 but does not fill yet. An empty
    #: directory carries no information, so each gets a gap marker naming why
    #: it is empty - either a measured value or a gap with the reason.
    GAP_MARKERS: ClassVar[dict[str, str]] = {
        "server": (
            "not collected yet (phase 1): server /metrics slices and backlog "
            "sweeps are the documented next step; see README §10"
        ),
        "host": (
            "not collected yet (phase 1): host cgroup CPU/RAM/IO/FD snapshots "
            "are the documented next step; see README §10"
        ),
    }

    @classmethod
    def create(cls, run_dir: str | Path) -> RunLayout:
        root = Path(run_dir)
        for name in ("raw", "generator", "server", "host", "stub"):
            (root / name).mkdir(parents=True, exist_ok=True)
        for name, reason in cls.GAP_MARKERS.items():
            marker = root / name / "GAP.md"
            if not marker.exists():
                marker.write_text(f"# gap\n\n{reason}\n", encoding="utf-8")
        return cls(root=root)

    @property
    def manifest_path(self) -> Path:
        return self.root / "run-manifest.json"

    @property
    def preflight_path(self) -> Path:
        return self.root / "preflight.json"

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    def raw_path(self, label: str) -> Path:
        return self.root / "raw" / f"{label}.jsonl"

    def calls_path(self, label: str) -> Path:
        return self.root / "raw" / f"{label}.calls.jsonl"

    def generator_path(self, label: str) -> Path:
        return self.root / "generator" / f"{label}.json"

    def server_path(self, service: str) -> Path:
        return self.root / "server" / f"{service}.json"

    def host_path(self, service: str) -> Path:
        return self.root / "host" / f"{service}.json"

    def stub_path(self, label: str) -> Path:
        return self.root / "stub" / f"{label}.jsonl"

    def clear_gap_marker(self, name: str) -> None:
        """Drop a gap marker once the directory it describes has real data.

        The marker says "not collected yet (phase 1)". Leaving it next to a
        collected slice would put two contradictory claims in one artefact, and
        the reader has no way to tell which one is current. Collection is per
        run, so the marker is removed only when this run actually wrote there.
        """
        marker = self.root / name / "GAP.md"
        if marker.exists():
            marker.unlink()


def stage_summary(
    stage: StageResult, outcome: StageOutcome, ticket: StageTicket
) -> dict[str, Any]:
    """The generator-side facts of one stage, next to the records they explain.

    These are the §10 extension columns: without them a rung that held at the
    nominal pool width is indistinguishable from one that needed a wider pool,
    and the cost of measuring (busy-wait tail, generator CPU, dropped ticks) is
    invisible in the artefact.
    """
    return {
        "ticket": {
            "target_rps": ticket.target_rps,
            "attempt": ticket.attempt,
            "run_index": ticket.run_index,
            "label": ticket.label(),
        },
        "target_rps": stage.spec.target_rps,
        "duration_s": stage.spec.duration_s,
        "warmup_s": stage.spec.warmup_s,
        "total_ticks": stage.spec.total_ticks,
        "warmup_ticks": stage.spec.warmup_ticks,
        "measured_window_s": stage.spec.measured_window_s,
        "workers": outcome.workers,
        "pool_expansions": outcome.pool_expansions,
        "dropped_ticks": stage.dropped_ticks,
        # The judged half next to the whole-stage count, for the same reason
        # cpu_measured_s sits next to cpu_s: a tick dropped during warm-up is
        # not in the sample, the rate or the latency, so it is evidence about the
        # generator's cold start rather than a verdict on the rung.
        "measured_dropped_ticks": stage.gating_dropped_ticks,
        "warmup_dropped_ticks": stage.warmup_dropped_ticks,
        "tick_lag_p99_ms": stage.tick_lag_p99,
        # The whole-stage figure next to the judged one, for the same reason
        # cpu_s sits next to cpu_measured_s: a gap between them says the
        # generator needed its warm-up, which is not a defect of the rung.
        "stage_lag_p99_ms": stage.stage_lag_p99,
        "spin_tail_ms": stage.spin_tail_ms,
        "cpu_s": stage.cpu_s,
        "cpu_measured_s": stage.cpu_measured_s,
        "cpu_share_of_core": stage.cpu_share,
        "generator_bound": stage.generator_bound,
        "valid": outcome.valid,
        "invalid_reasons": list(outcome.invalid_reasons),
        "verdict": outcome.verdict,
        "reinitialisations": stage.reinitialisations,
        # The generator's own §3 rotation, next to the reactive replay above and
        # deliberately not merged with it: one is the profile's declared policy,
        # the other is the server having forgotten a session.
        "session_recycles": stage.session_recycles,
        "worker_failures": list(stage.worker_failures),
        "step_counts": outcome.step_counts,
        "stats": {
            "count": outcome.stats.count,
            "errors": outcome.stats.errors,
            "achieved_rps": outcome.stats.achieved_rps,
            "p50": outcome.stats.p50,
            "p95": outcome.stats.p95,
            "p99": outcome.stats.p99,
            "compensated_p95": outcome.stats.compensated_p95,
            "t_complete_p95": outcome.stats.t_complete_p95,
            "platform_overhead_p95": outcome.stats.platform_overhead_p95,
            "prompt_tokens_p50": outcome.stats.prompt_tokens_p50,
            "terminator_mix": outcome.stats.terminator_mix,
            "error_mix": outcome.stats.error_mix,
        },
    }


class StageEvidenceSink:
    """Writes one stage's records, call decomposition and generator facts.

    A callable matching :class:`agent_db.stress.ladder.StageSink`, so the ladder
    stays unaware of the filesystem. Every attempt of every rung lands on disk,
    including the ones discarded by a pool expansion: "how wide did the pool have
    to be" is evidence, and a sink that only kept the winner would drop it.
    """

    def __init__(self, layout: RunLayout) -> None:
        self.layout = layout
        self.written: list[str] = []

    def __call__(
        self, outcome: StageOutcome, stage: StageResult, ticket: StageTicket
    ) -> None:
        label = ticket.label()
        records = list(stage.records)
        write_raw_records(self.layout.raw_path(label), records)
        calls = list(stage.calls)
        if calls:
            write_calls(self.layout.calls_path(label), calls)
        write_json_atomic(
            self.layout.generator_path(label), stage_summary(stage, outcome, ticket)
        )
        self.written.append(label)


def write_calls(path: str | Path, calls: Sequence[ToolCallTiming]) -> int:
    """Write the per-call decomposition of every turn in a stage.

    These rows carry no planned offset of their own on purpose (§10): the turn is
    the scheduling unit, and giving each call its own planned start would count
    the turn's slip twice in the compensation.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for call in calls:
            handle.write(call.as_jsonl() + "\n")
    return len(calls)


def read_raw_records(path: str | Path) -> list[RawRequestRecord]:
    """Read a ``raw/*.jsonl`` file back. Used by the report re-renderer."""
    target = Path(path)
    if not target.exists():
        return []
    return [
        RawRequestRecord.from_jsonl(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_profile_snapshot(
    layout: RunLayout, profile: LoadProfile, source_path: str | Path | None
) -> None:
    """Freeze the profile that ran, next to the profile file it came from.

    The manifest carries its hash, but a hash does not let a reader see what the
    workload was; §10 keeps the file too, and for the same reason it keeps the
    source path: a profile edited between two runs is a different environment.
    """
    payload = profile.model_dump(mode="json")
    if source_path is not None:
        payload["_source_path"] = str(source_path)
    write_json_atomic(layout.profile_path, payload)


def write_preflight(layout: RunLayout, payload: dict[str, Any]) -> None:
    write_json_atomic(layout.preflight_path, payload)


def write_status(
    layout: RunLayout,
    status: str,
    *,
    abort_reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Terminal state of the run, in §10's separate ``status.json``.

    Kept out of the manifest on purpose: the manifest describes the *environment*
    and *code* a number belongs to and must not change while the run is going,
    while the status is the run's own lifecycle and does change.
    """
    payload: dict[str, Any] = {"status": status}
    if abort_reason:
        payload["abort_reason"] = abort_reason
    if detail:
        payload.update(detail)
    write_json_atomic(layout.status_path, payload)


def copy_into(layout: RunLayout, source: str | Path, name: str) -> Path:
    """Copy an external artefact (a stub timing log, a backlog export) in.

    Evidence has to be self-contained: a path pointing at a temp directory that
    is gone next week is not evidence, and the digest of what was copied is
    recorded by the manifest's own hash of the run directory.
    """
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"cannot copy missing evidence: {src}")
    destination = layout.root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, destination)
    return destination


__all__ = [
    "RunLayout",
    "StageEvidenceSink",
    "copy_into",
    "read_raw_records",
    "stage_summary",
    "write_calls",
    "write_preflight",
    "write_profile_snapshot",
    "write_status",
]
