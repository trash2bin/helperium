"""Reporter — format benchmark results into human-readable reports."""

from __future__ import annotations

from typing import Any

from .models import BenchReport, ToolCallEvent, TurnResult


def _pretty_duration(ms: float) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms < 1000:
        return f"{ms:.1f}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60_000:.1f}min"


def _pretty_cost(cost: float) -> str:
    """Format cost into a human-readable string."""
    if cost < 0.001:
        return f"${cost:.6f}"
    if cost < 1:
        return f"${cost:.4f}"
    return f"${cost:.3f}"


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_turn_detail(turn: TurnResult) -> str:
    """Format a single turn's details with tool calls, errors, and loop warnings."""
    lines: list[str] = []
    lines.append(f"  Question: {_truncate(turn.question, 120)}")
    lines.append(f"  Outcome: {turn.outcome}  |  Duration: {_pretty_duration(turn.duration_ms)}")
    lines.append(f"  Tokens: {turn.total_tokens} (prompt={turn.total_prompt_tokens}, completion={turn.total_completion_tokens})")
    lines.append(f"  Cost: {_pretty_cost(turn.total_cost)}  |  LLM calls: {turn.llm_calls}")
    lines.append(f"  Tool calls: {len(turn.tool_calls)}  |  Tool errors: {turn.tool_errors}")
    lines.append(f"  Empty results: {turn.empty_results}  |  Empty rounds: {turn.empty_rounds}")
    lines.append(f"  Iterations: {turn.iterations}  |  Final text: {_truncate(turn.final_text, 120)}")

    if turn.tool_calls:
        lines.append("  Tool calls:")
        for tc in turn.tool_calls:
            args_str = _truncate(str(tc.arguments), 100)
            lines.append(f"    · {tc.name}({args_str}) → {tc.result_chars}chars in {tc.duration_ms:.0f}ms")

    if turn.errors:
        lines.append("  Errors:")
        for err in turn.errors:
            lines.append(f"    ❌ {_truncate(err, 200)}")

    if turn.loop_warnings:
        lines.append("  ⚠️  Loop warnings:")
        for warn in turn.loop_warnings:
            lines.append(f"    {warn}")

    return "\n".join(lines)


def _compute_aggregates(report: BenchReport) -> dict[str, Any]:
    """Compute aggregate stats from a BenchReport's turns."""
    n = len(report.turns)
    if n == 0:
        return {}

    durations = sorted(t.duration_ms for t in report.turns)

    def _p(pct: float) -> float:
        idx = min(int(n * pct / 100), n - 1)
        return durations[idx]

    total_tokens = sum(t.total_tokens for t in report.turns)
    total_cost = sum(t.total_cost for t in report.turns)
    total_llm = sum(t.llm_calls for t in report.turns)
    total_tool = sum(len(t.tool_calls) for t in report.turns)
    total_tool_err = sum(t.tool_errors for t in report.turns)
    total_empty_res = sum(t.empty_results for t in report.turns)
    total_empty_rounds = sum(t.empty_rounds for t in report.turns)
    total_iterations = sum(t.iterations for t in report.turns)

    return {
        "n": n,
        "total_duration_ms": sum(durations),
        "avg_duration_ms": sum(durations) / n,
        "min_duration_ms": durations[0],
        "max_duration_ms": durations[-1],
        "p50_duration_ms": _p(50),
        "p95_duration_ms": _p(95),
        "p99_duration_ms": _p(99),
        "total_tokens": total_tokens,
        "avg_total_tokens": total_tokens / n,
        "total_cost": total_cost,
        "avg_cost": total_cost / n,
        "total_llm_calls": total_llm,
        "avg_llm_calls": total_llm / n,
        "total_tool_calls": total_tool,
        "avg_tool_calls": total_tool / n,
        "total_tool_errors": total_tool_err,
        "pct_tool_errors": (total_tool_err / n * 100) if n else 0.0,
        "total_empty_results": total_empty_res,
        "pct_empty_results": (total_empty_res / n * 100) if n else 0.0,
        "avg_empty_rounds": total_empty_rounds / n,
        "avg_iterations": total_iterations / n,
    }


def format_report(report: BenchReport) -> str:
    """Format a beautiful ASCII box benchmark report."""
    if not report.turns:
        return "  ℹ️  No data. Run some questions first."

    agg = _compute_aggregates(report)
    n = agg["n"]
    outcomes: dict[str, int] = {}
    for t in report.turns:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1

    lines: list[str] = []
    lines.append("╔══════════════════════════════════════════╗")
    lines.append("║            BENCHMARK REPORT              ║")
    lines.append("╚══════════════════════════════════════════╝")
    lines.append("")

    # Summary
    outcomes_str = "  |  ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
    lines.append(f"  Turns: {n}  |  Outcomes: {outcomes_str}")
    lines.append(f"  Passed: {report.passed}  |  Failed: {report.failed}  |  Errored: {report.errored}")
    lines.append("")

    # Duration
    lines.append("⏱  Duration")
    lines.append(
        f"   Total: {_pretty_duration(agg['total_duration_ms'])}  |  "
        f"Avg: {_pretty_duration(agg['avg_duration_ms'])}  |  "
        f"P50: {_pretty_duration(agg['p50_duration_ms'])}  |  "
        f"P95: {_pretty_duration(agg['p95_duration_ms'])}"
    )
    lines.append("")

    # Tokens
    lines.append("📊 Tokens")
    lines.append(
        f"   Total: {agg['total_tokens']}  |  "
        f"Avg: {agg['avg_total_tokens']:.1f}/turn"
    )
    lines.append("")

    # Cost
    lines.append("💰 Cost")
    lines.append(
        f"   Total: {_pretty_cost(agg['total_cost'])}  |  "
        f"Avg: {_pretty_cost(agg['avg_cost'])}/turn"
    )
    lines.append("")

    # Tools
    lines.append("🔧 Tools")
    lines.append(f"   LLM calls: {agg['total_llm_calls']} (avg {agg['avg_llm_calls']:.1f}/turn)")
    lines.append(f"   Tool calls: {agg['total_tool_calls']} (avg {agg['avg_tool_calls']:.1f}/turn)")
    lines.append(
        f"   Tool errors: {agg['total_tool_errors']} "
        f"({agg['pct_tool_errors']:.1f}% of turns)"
    )
    lines.append(
        f"   Empty results: {agg['total_empty_results']} "
        f"({agg['pct_empty_results']:.1f}% of turns)"
    )
    lines.append("")

    # Quality
    lines.append("📈 Quality")
    lines.append(f"   Avg empty rounds: {agg['avg_empty_rounds']:.1f}")
    lines.append(f"   Avg iterations: {agg['avg_iterations']:.1f}")

    # Per-turn breakdown
    if report.turns:
        lines.append("")
        lines.append("── Per-turn breakdown ──")
        for i, turn in enumerate(report.turns, 1):
            lines.append("")
            lines.append(f"── Turn {i}/{n} ──")
            lines.append(format_turn_detail(turn))

    return "\n".join(lines)
