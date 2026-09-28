"""Report rendering: fixed columns, an ASCII knee chart, stated caveats (§10).

The columns are fixed so two runs can be compared by eye instead of by retelling:
``profile | scope | target rps | achieved | p50 | p95 | p99 | err% |
platform_overhead p95 | platform_overhead_model p95 | stub queue_wait p95 |
mcp_lock_wait p95 | prompt_tokens p50 | terminator mix | SUT CPU | generator CPU |
lag p99 | verdict | environment hash``.

A column the layer cannot produce is printed as ``n/a`` with a reason rather than
dropped: L1 has no stub, no lock wait and no prompt tokens, and a reader comparing
an L1 table with an L3 table has to see that the difference is the layer, not a
missing measurement. Nothing here invents a value, and the notes section carries
the interpretation caveats that make an otherwise correct number misleading -
starting with the one §1 insists on, that an L1 knee is not MCP-session
serialization because the per-tenant ``call_lock`` lives in api-service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .ladder import JUDGED_METRIC_READY_LAYERS, LAYER_RULE, LadderResult, StageOutcome
from .manifest import RunManifest

COLUMNS: tuple[str, ...] = (
    "profile",
    "scope",
    "target_rps",
    "achieved_rps",
    "p50",
    "p95",
    "p99",
    "err_pct",
    "platform_overhead_p95",
    "platform_overhead_model_p95",
    "stub_queue_wait_p95",
    "mcp_lock_wait_p95",
    "prompt_tokens_p50",
    "terminator_mix",
    "sut_cpu",
    "generator_cpu",
    "lag_p99",
    "verdict",
    "environment_hash",
    # Extensions beyond the §10 metric list: they are what tells a rung that held
    # at the nominal pool apart from one that needed a wider pool, and what the
    # generator cost while it measured. Without them the "invalid stage is an
    # action" record of §2 leaves no trace in the artefact.
    "pool_expansions",
    "dropped_ticks",
    "spin_tail_ms",
)

# Metrics a layer cannot produce in phase 1, and why. Kept next to the columns so
# the reason travels with the empty cell.
UNAVAILABLE_PHASE_1: dict[str, str] = {
    "platform_overhead_model_p95": "nominal LLM latencies need the stub (phase 2)",
    "stub_queue_wait_p95": "the stub reports its own timings from phase 2 on",
    "mcp_lock_wait_p95": "mcp_lock_wait_seconds is scraped from api-service /metrics",
    "sut_cpu": "server CPU comes from cgroup slices, collected separately",
    "generator_cpu": "cpu time is measured per stage by the harness",
    "platform_overhead_p95": "needs LLM timings to subtract (phase 2)",
    "pool_expansions": "pool growth is recorded per rung",
    "dropped_ticks": "tick accounting is recorded per rung",
    "spin_tail_ms": "the calibrated tail is recorded per rung",
}

# Layers whose turns contain no model latency, so the platform overhead *is* the
# compensated turn latency (§1 for L1; §7 explicitly allows t_complete on L2,
# where the stub is instant). On any other layer the value has to come from
# per-request LLM timings, and the metric exists exactly when the substrate
# reported them - never as a silent fallback to the turn latency (§7: an honest
# 2.5 s model must not be reported as a platform failure).
LAYER_HAS_NO_LLM_IN_TURN = JUDGED_METRIC_READY_LAYERS

# Columns whose emptiness is a property of the layer, not of the tooling.
LAYER_UNAVAILABLE: dict[str, dict[str, str]] = {
    "L1": {
        "platform_overhead_model_p95": "L1 does not call the LLM",
        "stub_queue_wait_p95": "L1 has no stub",
        "mcp_lock_wait_p95": "api-service is not on the L1 path",
        "prompt_tokens_p50": "L1 has no prompt",
        "terminator_mix": "L1 turns are not terminated by the agent loop",
    }
}


def stage_row(
    stage: StageOutcome,
    *,
    profile: str,
    scope: str,
    environment_hash: str,
    layer: str,
) -> dict[str, Any]:
    """One table row, with ``None`` where the layer has no such quantity."""
    stats = stage.stats
    unavailable = LAYER_UNAVAILABLE.get(layer, {})
    row: dict[str, Any] = {
        "profile": profile,
        "scope": scope,
        "target_rps": stage.target_rps,
        "achieved_rps": round(stats.achieved_rps, 2),
        "p50": round(stats.p50, 2),
        "p95": round(stats.p95, 2),
        "p99": round(stats.p99, 2),
        "err_pct": round(stats.error_rate * 100, 3),
        # Per stage, never a run-wide constant: stages differ in how much of the
        # turn was the model's, and §7 judges this column. Where a driver supplied
        # per-request LLM timings the value comes from the stage; on the layers
        # where nothing else happens inside the turn the identity with the
        # compensated percentile is asserted here, per layer, by name.
        "platform_overhead_p95": (
            round(stats.platform_overhead_p95, 2)
            if stats.platform_overhead_p95 is not None
            else (
                round(stats.compensated_p95, 2)
                if layer in LAYER_HAS_NO_LLM_IN_TURN
                else None
            )
        ),
        "platform_overhead_model_p95": None,
        "stub_queue_wait_p95": None,
        "mcp_lock_wait_p95": None,
        "prompt_tokens_p50": (
            round(stats.prompt_tokens_p50, 1)
            if stats.prompt_tokens_p50 is not None
            else None
        ),
        "terminator_mix": dict(stats.terminator_mix) or None,
        "sut_cpu": None,
        # The generator is part of the instrument, so its own CPU is measured
        # rather than assumed: a rung bought with a saturated generator is a
        # number about the generator. The denominator is the whole stage, the
        # same one the saturation gate uses.
        # ``is not None`` rather than truthiness: a measured 0.0 is a measurement,
        # and collapsing it into an empty cell would print "n/a" next to a reason
        # claiming the number is not collected yet - a false statement.
        "generator_cpu": (
            round(
                (stage.cpu_measured_s or stage.cpu_s) / stage.measured_window_s * 100,
                2,
            )
            if stage.measured_window_s
            else None
        ),
        "pool_expansions": stage.pool_expansions,
        "dropped_ticks": stage.dropped_ticks,
        "spin_tail_ms": (
            round(stage.spin_tail_ms, 3) if stage.spin_tail_ms is not None else None
        ),
        "lag_p99": round(stage.tick_lag_p99, 3),
        "verdict": stage.verdict,
        "environment_hash": environment_hash,
    }
    for column in unavailable:
        row[column] = None
    return row


def unavailable_reasons(row: dict[str, Any], *, layer: str) -> dict[str, str]:
    """Why each ``None`` cell is empty. Emptiness is an assertion too."""
    layer_reasons = LAYER_UNAVAILABLE.get(layer, {})
    reasons: dict[str, str] = {}
    for column in COLUMNS:
        if row.get(column) is not None:
            continue
        reasons[column] = layer_reasons.get(
            column, UNAVAILABLE_PHASE_1.get(column, "not measured by any driver yet")
        )
    return reasons


def interpretation_notes(result: LadderResult, manifest: RunManifest) -> list[str]:
    """What a reader must know so a correct number is not misread."""
    notes: list[str] = []
    layer = result.target_layer
    if layer == "L1":
        notes.append(
            "L1 knee measures mcp-gateway + data-service + the tenant DB: the "
            "per-tenant call_lock lives in api-service, which is not on this path "
            "(§1). MCP-session serialization is only observable on L2/L3."
        )
    if result.status == "unjudgeable":
        # §7's metric is uncomputable on this layer without per-request model
        # timings, and the run stopped before producing a number that would have
        # been about the model, not the platform.
        notes.append(
            f"no verdict: §7 judges platform_overhead p95 on {layer}, and the "
            "substrate reported no per-request service time, so the model's own "
            "latency could not be separated from the platform's work"
        )
    if result.plan.repeats < 2:
        notes.append(
            f"single run (repeats={result.plan.repeats}): §2 publishes a headline "
            "only as the median of 2-3 runs, so this number is a shape, not a rate."
        )
    if manifest.environment.budget_preset != "off":
        notes.append(
            "budgets were not off: the rung may measure the limiter rather than the "
            "platform (§5)."
        )
    blockers = manifest.publication_blockers()
    if blockers:
        notes.append(
            "the manifest is incomplete, so this run is not publishable as "
            "capacity: " + "; ".join(blockers)
        )
    if result.prediction is not None and result.knee and result.knee.rate:
        predicted = result.prediction.rate
        found = result.knee.rate
        ratio = found / predicted if predicted else 0.0
        notes.append(
            f"predicted {predicted:.1f} rps, found {found:.1f} rps (ratio {ratio:.2f}): "
            "a ratio near 1 supports the per-tenant serialization model, a large "
            "miss means some other ceiling dominates (§3)."
        )
    for stage in result.stages:
        if stage.verdict == "invalid":
            notes.append(
                f"stage {stage.target_rps:.0f} rps was invalid after "
                f"{stage.pool_expansions} pool expansion(s) and produced no verdict: "
                + "; ".join(stage.invalid_reasons)
            )
        elif stage.pool_expansions:
            # A valid rung that needed a wider pool is not the same evidence as
            # one that held at the nominal width (§2: invalid is an action).
            notes.append(
                f"stage {stage.target_rps:.0f} rps passed only after "
                f"{stage.pool_expansions} pool expansion(s), to {stage.workers} "
                "workers: its verdict holds at that width, not at the nominal one"
            )
        if stage.stats.error_mix:
            # §3: the report must separate working degradation from real refusals,
            # and that is a property of the stage, not only of the invalid ones.
            notes.append(
                f"stage {stage.target_rps:.0f} rps error mix: {stage.stats.error_mix}"
            )
    if result.stages and result.stages[-1].verdict == "invalid" and result.knee:
        # The rung above the knee never produced a verdict, so the knee is the
        # last rate that was *shown* to be held - not the last rate the platform
        # could hold. Publishing it as the ceiling would read as a measurement of
        # the platform when it is a measurement of the stand.
        notes.append(
            f"the rung above the knee ({result.stages[-1].target_rps:.0f} rps) was "
            f"invalid, so the knee {result.knee.rate:.0f} rps is a LOWER BOUND: the "
            "platform was never shown to fail above it"
        )
    if result.knee and result.knee.rate and result.knee.bracket is None:
        if result.stages and result.stages[-1].verdict == "pass":
            notes.append(
                f"the ladder's top rung ({result.knee.rate:.0f} rps) passed, so the "
                "knee is a LOWER BOUND at the top of the ladder: no rung above it "
                "was ever run, and the platform was never shown to fail"
            )
    headline = result.headline()
    if headline and headline["runs_discarded"]:
        notes.append(
            f"{headline['runs_discarded']:.0f} of {headline['runs_requested']:.0f} "
            "knee runs were invalid and left out of the median: the headline rests "
            "on the valid ones only (§2)"
        )
    return notes


def build_report(
    *,
    result: LadderResult,
    manifest: RunManifest,
    scope: str,
    profile_path: str | None = None,
    wall_clock_s: float | None = None,
) -> dict[str, Any]:
    """Machine-readable verdict and evidence summary (``report.json``)."""
    environment_hash = manifest.environment_hash(short=True)
    rows = [
        stage_row(
            stage,
            profile=result.profile_name,
            scope=scope,
            environment_hash=environment_hash,
            layer=result.target_layer,
        )
        for stage in result.stages
    ]
    if result.status == "unjudgeable":
        # The run stopped because §7 judges platform_overhead p95 on this
        # layer and the substrate brought no per-request timings. The rung's
        # fallback number decided nothing, so printing its verdict would put
        # the exact cell §7 forbids into the table.
        for row in rows:
            if row.get("verdict") is not None and row.get("verdict") != "invalid":
                row["verdict"] = None
    return {
        "report_version": 1,
        "run_uuid": manifest.run_uuid,
        "started_at": manifest.started_at,
        "wall_clock_s": wall_clock_s,
        "profile": result.profile_name,
        "profile_path": profile_path,
        "target_layer": result.target_layer,
        "layer_rule": LAYER_RULE.get(result.target_layer),
        "scope": scope,
        "t_budget_ms": result.plan.t_budget_ms,
        "max_error_rate": result.plan.max_error_rate,
        "environment_hash": environment_hash,
        "environment_hash_full": manifest.environment_hash(),
        "code": manifest.code.as_dict(),
        "ladder": {
            "rates": list(result.plan.rates),
            "status": result.status,
            "knee_rps": result.knee.rate if result.knee else None,
            "knee_bracket": list(result.knee.bracket)
            if result.knee and result.knee.bracket
            else None,
            "tolerance": result.plan.tolerance,
            "bisection_steps": result.knee.bisection_steps if result.knee else 0,
            "headline": result.headline(),
            "repeats": result.plan.repeats,
        },
        "prediction": result.prediction.as_dict() if result.prediction else None,
        "columns": list(COLUMNS),
        "rows": rows,
        "unavailable": {
            str(row["target_rps"]): unavailable_reasons(row, layer=result.target_layer)
            for row in rows
        },
        "notes": interpretation_notes(result, manifest),
        "blockers": manifest.publication_blockers(),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3g}"
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in value.items()) or "n/a"
    return str(value)


# The human-readable table carries every reportable column, because §10's whole
# point is that two runs are compared by looking at them: a column that exists
# only in report.json is a column nobody compares. Empty cells print as ``n/a``
# and are explained in the section below the table.
MARKDOWN_COLUMNS: tuple[str, ...] = COLUMNS

HEADERS: dict[str, str] = {
    "profile": "profile",
    "scope": "scope",
    "target_rps": "target rps",
    "achieved_rps": "achieved",
    "p50": "p50",
    "p95": "p95",
    "p99": "p99",
    "err_pct": "err%",
    "platform_overhead_p95": "overhead p95",
    "platform_overhead_model_p95": "overhead model p95",
    "stub_queue_wait_p95": "stub queue p95",
    "mcp_lock_wait_p95": "mcp lock p95",
    "prompt_tokens_p50": "prompt tokens p50",
    "terminator_mix": "terminator mix",
    "sut_cpu": "SUT CPU",
    "generator_cpu": "gen CPU %",
    "lag_p99": "lag p99",
    "verdict": "verdict",
    "environment_hash": "env hash",
    "pool_expansions": "pool x",
    "dropped_ticks": "dropped",
    "spin_tail_ms": "spin tail ms",
}


def render_markdown(report: dict[str, Any]) -> str:
    """The human-readable report: table, knee chart, notes, evidence pointers."""
    rows = report["rows"]
    budget = report["t_budget_ms"]
    lines: list[str] = [
        f"# Stress report: {report['profile']} ({report['target_layer']}, "
        f"{report['scope']})",
        "",
        f"- run: `{report['run_uuid']}` started {report['started_at']}",
        f"- code: `{report['code']['commit'][:12]}` on `{report['code']['branch']}`"
        f"{' (dirty)' if report['code']['dirty'] else ''}",
        f"- environment hash: `{report['environment_hash']}`",
        f"- judged metric: {report.get('layer_rule') or 'unstated'}",
        f"- criterion: p95 <= {budget} ms and error <= "
        f"{report['max_error_rate'] * 100:.0f}%",
        "",
        "| " + " | ".join(HEADERS[c] for c in MARKDOWN_COLUMNS) + " |",
        "|" + "|".join("---" for _ in MARKDOWN_COLUMNS) + "|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_fmt(row.get(column)) for column in MARKDOWN_COLUMNS)
            + " |"
        )

    knee = report["ladder"]
    lines += ["", "## Knee", ""]
    if knee["knee_rps"] is not None:
        lines.append(
            f"- knee: **{knee['knee_rps']:.2f} rps** "
            f"(status `{knee['status']}`, tolerance +/-{knee['tolerance'] * 100:.0f}%, "
            f"{knee['bisection_steps']} bisection step(s))"
        )
    else:
        lines.append(f"- knee: **not determined** (status `{knee['status']}`)")
    if knee["knee_bracket"]:
        low, high = knee["knee_bracket"]
        lines.append(f"- bracket: {low:.2f} - {high:.2f} rps")
    if knee["headline"]:
        headline = knee["headline"]
        lines.append(
            f"- repeats: {headline['runs']:.0f}, p95 median {headline['p95_median']:.2f} ms "
            f"(min {headline['p95_min']:.2f}, max {headline['p95_max']:.2f})"
        )
    if report["prediction"]:
        lines.append(
            f"- predicted: {report['prediction']['rate']:.2f} rps "
            f"({report['prediction']['model']})"
        )

    lines += ["", "## Knee chart (rps to p95)", "", "```"]
    chart = _knee_chart(rows, budget)
    lines.extend(chart)
    lines += ["```"]

    if report["unavailable"]:
        lines += [
            "",
            "## Empty cells",
            "",
            "| target rps | column | reason |",
            "|---|---|---|",
        ]
        for rps, reasons in report["unavailable"].items():
            for column, reason in reasons.items():
                lines.append(f"| {rps} | {column} | {reason} |")

    if report["notes"]:
        lines += ["", "## Notes and caveats", ""]
        lines += [f"- {note}" for note in report["notes"]]

    lines += [
        "",
        "## Evidence",
        "",
        "Raw per-request records live in `raw/<rps>-run<k>-attempt<n>.jsonl` - one "
        "file per rung attempt and per knee repeat, so the headline median can be "
        "recomputed from the evidence it claims to come from. The profile snapshot, "
        "the manifest and this report sit next to them (§10).",
        "",
    ]
    return "\n".join(lines)


def _knee_chart(
    rows: Sequence[dict[str, Any]], budget: float, width: int = 48
) -> list[str]:
    """ASCII chart of p95 against the rate, with the budget as the pass line.

    A chart rather than a plotting dependency: the artefact has to render in CI
    and in a terminal, and a knee is a shape - the numbers are already in the
    table above.
    """
    measured = [row for row in rows if row.get("p95") is not None]
    if not measured:
        return ["no measured stages"]
    top = max(max(row["p95"] for row in measured), budget * 1.05)
    lines = [f"budget {budget:.0f} ms at {budget / top * width:.0f} of {width}"]
    for row in measured:
        filled = int(round(row["p95"] / top * width))
        marker = "PASS" if row["verdict"] == "pass" else row["verdict"].upper()
        lines.append(
            f"{row['target_rps']:>6.1f} rps |{'#' * filled:<{width}}| "
            f"p95 {row['p95']:>8.2f} ms  {marker}"
        )
    lines.append(f"{'':>6}      +{'-' * width}+  (dashed line: budget {budget:.0f} ms)")
    return lines


def write_report(directory: str | Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report-<uuid>.md`` into the run directory."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "report.json"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path = target / f"report-{report['run_uuid']}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


__all__ = [
    "COLUMNS",
    "MARKDOWN_COLUMNS",
    "build_report",
    "interpretation_notes",
    "render_markdown",
    "stage_row",
    "unavailable_reasons",
    "write_report",
]
