"""Aggregate benchmark case results into a BenchmarkReport and print it.

Success rules (ТЗ §7):
- case ``success`` = retrieval_ok AND answer_ok AND NOT hallucination
  (refusal handled separately via ``refusal_ok``).
"""

from __future__ import annotations

from typing import Any

from .models import BenchmarkReport, CaseResult, EvalResult, RunResult, TestCase
from .evaluator import DeterministicEvaluator


def aggregate_report(
    cases: list[TestCase],
    runs: list[RunResult],
    eval_results: list[EvalResult],
) -> BenchmarkReport:
    """Build a BenchmarkReport from case/runs/evaluations.

    Args:
        cases: The test cases (in order).
        runs: RunResults produced by the runner (same order as cases).
        eval_results: EvalResults from the evaluator (same order).

    Returns:
        Aggregated BenchmarkReport.
    """
    report = BenchmarkReport()
    report.total_cases = len(cases)

    durations: list[float] = []
    tokens_list: list[int] = []
    cost_list: list[float] = []
    tool_calls_list: list[int] = []
    llm_calls_list: list[int] = []
    n = len(cases)
    if n == 0:
        return report

    for case, run, ev in zip(cases, runs, eval_results):
        backlog = run.backlog
        duration = backlog.duration_ms if backlog else 0.0
        tokens = backlog.total_tokens if backlog else 0
        prompt = backlog.total_prompt_tokens if backlog else 0
        completion = backlog.total_completion_tokens if backlog else 0
        llm = backlog.llm_calls if backlog else 0
        tool_cnt = backlog.tool_calls_count if backlog else 0
        iters = backlog.iterations if backlog else 0
        cost = backlog.total_cost if backlog else 0.0

        durations.append(duration)
        tokens_list.append(tokens)
        cost_list.append(cost)
        tool_calls_list.append(tool_cnt)
        llm_calls_list.append(llm)

        case_res = CaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            tags=case.tags,
            eval_result=ev,
            duration_ms=duration,
            total_tokens=tokens,
            llm_calls=llm,
            tool_calls=tool_cnt,
            iterations=iters,
            cost_usd=cost,
            outcome=backlog.outcome if backlog else "final",
            final_text=run.final_text,
            errors=run.errors,
        )
        report.case_results.append(case_res)

        if ev.success:
            report.success_count += 1
        if ev.retrieval_ok:
            report.retrieval_count += 1
        if ev.answer_ok:
            report.answer_count += 1
        if ev.hallucination:
            report.hallucination_count += 1
        if ev.grounded:
            report.grounded_count += 1
        if case.expect_refusal and ev.refusal_ok:
            report.refusal_count += 1
        if backlog and backlog.tool_errors > 0:
            report.tool_error_turns += 1
            report.errors_total_count += 1
            if ev.success:
                report.errors_but_final_count += 1
        if ev.entity_name_ok:
            report.entity_name_ok_count += 1

        # verdict distribution + error class histogram
        report.verdict_counts[ev.verdict.value] = report.verdict_counts.get(ev.verdict.value, 0) + 1
        for cls in ev.error_classes:
            report.error_class_histogram[cls] = report.error_class_histogram.get(cls, 0) + 1
        report.avg_repeated_tool_calls += ev.repeated_tool_calls
        report.avg_unique_tool_calls += ev.unique_tool_calls
        report.avg_db_get += ev.db_get_count

        report.avg_prompt_tokens_sum += prompt
        report.avg_completion_tokens_sum += completion
        report.avg_total_tokens_sum += tokens
        report.avg_llm_calls_sum += llm
        report.avg_tool_calls_sum += tool_cnt
        report.avg_iterations_sum += iters
        report.avg_cost_usd_sum += cost
        report.avg_duration_sum += duration

    report.total_cost_usd = report.avg_cost_usd_sum

    # Rates
    report.success_rate = report.success_count / n
    report.retrieval_success_rate = report.retrieval_count / n
    report.answer_delivery_rate = report.answer_count / n
    report.hallucination_rate = report.hallucination_count / n
    report.groundedness_rate = report.grounded_count / n
    report.tool_error_rate = report.tool_error_turns / n
    report.entity_name_accuracy = report.entity_name_ok_count / n
    # Recovery rate: доля кейсов с tool_errors, где агент всё равно дошёл до final
    report.recovery_rate = (
        report.errors_but_final_count / report.errors_total_count
        if report.errors_total_count > 0
        else 1.0  # нет ошибок → идеальная устойчивость
    )

    refusal_cases = sum(1 for c in cases if c.expect_refusal)
    report.refusal_correct_rate = (
        report.refusal_count / refusal_cases if refusal_cases else 0.0
    )

    # Averages + p95
    report.avg_total_tokens = report.avg_total_tokens_sum / n
    report.avg_prompt_tokens = report.avg_prompt_tokens_sum / n
    report.avg_completion_tokens = report.avg_completion_tokens_sum / n
    report.avg_llm_calls = report.avg_llm_calls_sum / n
    report.avg_tool_calls = report.avg_tool_calls_sum / n
    report.avg_iterations = report.avg_iterations_sum / n
    report.avg_duration_ms = report.avg_duration_sum / n
    report.avg_cost_usd = report.avg_cost_usd_sum / n
    report.avg_repeated_tool_calls /= n
    report.avg_unique_tool_calls /= n
    report.avg_db_get /= n

    def _pct(sorted_list: list[float], p: float) -> float:
        if not sorted_list:
            return 0.0
        n = len(sorted_list)
        if n == 1:
            return sorted_list[0]
        # Linear interpolation between the two nearest ranks
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return sorted_list[lo] * (1 - frac) + sorted_list[hi] * frac

    report.p50_duration_ms = _pct(sorted(durations), 0.5)
    report.p95_duration_ms = _pct(sorted(durations), 0.95)
    report.p50_tokens = _pct(sorted(tokens_list), 0.5)
    report.p95_tokens = _pct(sorted(tokens_list), 0.95)
    report.p50_cost_usd = _pct(sorted(cost_list), 0.5)
    report.p95_cost_usd = _pct(sorted(cost_list), 0.95)
    report.p50_tool_calls = _pct(sorted(tool_calls_list), 0.5)
    report.p95_tool_calls = _pct(sorted(tool_calls_list), 0.95)
    report.p50_llm_calls = _pct(sorted(llm_calls_list), 0.5)
    report.p95_llm_calls = _pct(sorted(llm_calls_list), 0.95)

    return report


def print_report(report: BenchmarkReport) -> str:
    """Return a human-readable report string (also prints nothing)."""
    lines: list[str] = []
    lines.append("╔══════════════════════════════════════════════╗")
    lines.append("║           CORE BENCHMARK REPORT              ║")
    lines.append("╚══════════════════════════════════════════════╝")
    lines.append("")
    lines.append(f"Total cases: {report.total_cases}")
    lines.append(f"Success rate: {report.success_rate:.1%}")
    lines.append(f"Retrieval success: {report.retrieval_success_rate:.1%}")
    lines.append(f"Answer delivery: {report.answer_delivery_rate:.1%}")
    lines.append(f"Hallucination rate: {report.hallucination_rate:.1%}")
    lines.append(f"Groundedness: {report.groundedness_rate:.1%}")
    lines.append(f"Refusal correct: {report.refusal_correct_rate:.1%}")
    lines.append(f"Tool error rate: {report.tool_error_rate:.1%}")
    lines.append(f"Entity name accuracy: {report.entity_name_accuracy:.1%}")
    lines.append(f"Recovery rate (errors→final): {report.recovery_rate:.1%}")
    lines.append("")
    # verdict distribution
    lines.append("Verdicts:")
    for v in ("CORRECT", "PARTIAL", "WRONG", "ERROR"):
        cnt = report.verdict_counts.get(v, 0)
        lines.append(f"  {v:<8} {cnt} ({cnt / report.total_cases:.1%})" if report.total_cases else f"  {v:<8} 0")
    if report.error_class_histogram:
        lines.append("")
        lines.append("Errors by class:")
        for cls, cnt in sorted(report.error_class_histogram.items(), key=lambda x: -x[1]):
            cls_name = cls.value if hasattr(cls, "value") else cls
            lines.append(f"  {cls_name:<22} {cnt}")
    lines.append("")
    lines.append(f"Avg tokens: {report.avg_total_tokens:.0f}")
    lines.append(f"P50 tokens: {report.p50_tokens:.0f}  P95: {report.p95_tokens:.0f}")
    lines.append(f"Avg duration: {report.avg_duration_ms:.0f}ms")
    lines.append(f"P50 duration: {report.p50_duration_ms:.0f}ms  P95: {report.p95_duration_ms:.0f}ms")
    lines.append(f"Avg tool calls: {report.avg_tool_calls:.1f}")
    lines.append(f"P50 tool calls: {report.p50_tool_calls:.0f}  P95: {report.p95_tool_calls:.0f}")
    lines.append(f"Avg llm calls: {report.avg_llm_calls:.1f}")
    lines.append(f"P50 llm calls: {report.p50_llm_calls:.0f}  P95: {report.p95_llm_calls:.0f}")
    lines.append(f"Avg cost: ${report.avg_cost_usd:.4f}")
    lines.append(f"P50 cost: ${report.p50_cost_usd:.4f}  P95: ${report.p95_cost_usd:.4f}")
    lines.append(f"Total cost: ${report.total_cost_usd:.4f}")
    lines.append("")
    lines.append("Failed cases:")
    if not report.case_results:
        lines.append("  (no cases)")
    for case in report.case_results:
        if case.success:
            continue
        ev = case.eval_result
        reason = "; ".join(ev.reasons[:3]) if ev else "no eval"
        lines.append(f"  - {case.case_id}: {case.question}")
        lines.append(f"      verdict={ev.verdict.value if ev else '-'} "
                     f"tool={ev.tool_ok if ev else '-'} "
                     f"retrieval={ev.retrieval_ok if ev else '-'} "
                     f"answer={ev.answer_ok if ev else '-'} "
                     f"halluc={ev.hallucination if ev else '-'} "
                     f"| {reason}")
    return "\n".join(lines)


def report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
    """Serialize a BenchmarkReport to a plain dict (for JSON output)."""
    def _ec(v: Any) -> str:
        """ErrorClass → its string value (HALLUCINATED_SKU, not ErrorClass.HALLUCINATED_SKU)."""
        return v.value if hasattr(v, "value") else str(v)

    return {
        "total_cases": report.total_cases,
        "success_rate": report.success_rate,
        "retrieval_success_rate": report.retrieval_success_rate,
        "answer_delivery_rate": report.answer_delivery_rate,
        "hallucination_rate": report.hallucination_rate,
        "groundedness_rate": report.groundedness_rate,
        "refusal_correct_rate": report.refusal_correct_rate,
        "tool_error_rate": report.tool_error_rate,
        "entity_name_accuracy": report.entity_name_accuracy,
        "recovery_rate": report.recovery_rate,
        # verdict + error taxonomy
        "verdicts": report.verdict_counts,
        "error_classes": {_ec(k): v for k, v in report.error_class_histogram.items()},
        "avg_total_tokens": report.avg_total_tokens,
        "avg_prompt_tokens": report.avg_prompt_tokens,
        "avg_completion_tokens": report.avg_completion_tokens,
        "avg_llm_calls": report.avg_llm_calls,
        "avg_tool_calls": report.avg_tool_calls,
        "avg_iterations": report.avg_iterations,
        "avg_duration_ms": report.avg_duration_ms,
        "p50_duration_ms": report.p50_duration_ms,
        "p95_duration_ms": report.p95_duration_ms,
        "p50_tokens": report.p50_tokens,
        "p95_tokens": report.p95_tokens,
        "p50_cost_usd": report.p50_cost_usd,
        "p95_cost_usd": report.p95_cost_usd,
        "p50_tool_calls": report.p50_tool_calls,
        "p95_tool_calls": report.p95_tool_calls,
        "p50_llm_calls": report.p50_llm_calls,
        "p95_llm_calls": report.p95_llm_calls,
        "avg_repeated_tool_calls": report.avg_repeated_tool_calls,
        "avg_unique_tool_calls": report.avg_unique_tool_calls,
        "avg_db_get": report.avg_db_get,
        "avg_cost_usd": report.avg_cost_usd,
        "total_cost_usd": report.total_cost_usd,
        "run_metadata": report.run_metadata,
        "cases": [
            {
                "case_id": c.case_id,
                "question": c.question,
                "category": c.category,
                "success": c.success,
                "eval": {
                    "verdict": c.eval_result.verdict.value if c.eval_result else None,
                    "error_classes": [_ec(cls) for cls in (c.eval_result.error_classes if c.eval_result else [])],
                    "error_source": c.eval_result.error_source if c.eval_result else None,
                    "tool_ok": c.eval_result.tool_ok if c.eval_result else None,
                    "retrieval_ok": c.eval_result.retrieval_ok if c.eval_result else None,
                    "answer_ok": c.eval_result.answer_ok if c.eval_result else None,
                    "hallucination": c.eval_result.hallucination if c.eval_result else None,
                    "grounded": c.eval_result.grounded if c.eval_result else None,
                    "refusal_ok": c.eval_result.refusal_ok if c.eval_result else None,
                    "entity_name_ok": c.eval_result.entity_name_ok if c.eval_result else None,
                    "reasons": c.eval_result.reasons if c.eval_result else [],
                },
                "metrics": {
                    "duration_ms": c.duration_ms,
                    "total_tokens": c.total_tokens,
                    "llm_calls": c.llm_calls,
                    "tool_calls": c.tool_calls,
                    "iterations": c.iterations,
                    "cost_usd": c.cost_usd,
                    "repeated_tool_calls": c.eval_result.repeated_tool_calls if c.eval_result else 0,
                    "unique_tool_calls": c.eval_result.unique_tool_calls if c.eval_result else 0,
                    "db_get_count": c.eval_result.db_get_count if c.eval_result else 0,
                },
                "outcome": c.outcome,
                "final_text": c.final_text[:500],
            }
            for c in report.case_results
        ],
    }


def diff_reports(prev: BenchmarkReport, cur: BenchmarkReport) -> dict[str, Any]:
    """Compare two runs case-by-case (regression detection).

    Returns:
        {
          "case_diff": [
            {"case_id": ..., "prev_verdict": ..., "cur_verdict": ...,
             "changed": true, "prev_errors": [...], "cur_errors": [...]},
            ...
          ],
          "verdict_delta": {"CORRECT": +1, "WRONG": -1, ...},
          "total_changed": N,
          "improved": M,
          "regressed": K
        }
    """
    prev_by_id = {c.case_id: c for c in prev.case_results}
    cur_by_id = {c.case_id: c for c in cur.case_results}

    case_diff: list[dict[str, Any]] = []
    verdict_delta: dict[str, int] = {}

    def _verdict(c: CaseResult | None) -> str | None:
        return c.eval_result.verdict.value if c and c.eval_result else None

    def _errors(c: CaseResult | None) -> list[str]:
        if not c or not c.eval_result:
            return []
        return [ec.value if hasattr(ec, "value") else str(ec) for ec in c.eval_result.error_classes]

    for case_id in sorted(set(prev_by_id) | set(cur_by_id)):
        p = prev_by_id.get(case_id)
        c = cur_by_id.get(case_id)
        pv, cv = _verdict(p), _verdict(c)
        if pv is None:
            verdict_delta[cv or "?"] = verdict_delta.get(cv or "?", 0) + 1
        elif cv is None:
            verdict_delta[pv] = verdict_delta.get(pv, 0) - 1
        elif pv != cv:
            verdict_delta[cv] = verdict_delta.get(cv, 0) + 1
            verdict_delta[pv] = verdict_delta.get(pv, 0) - 1
        case_diff.append({
            "case_id": case_id,
            "prev_verdict": pv,
            "cur_verdict": cv,
            "changed": pv != cv,
            "prev_errors": _errors(p),
            "cur_errors": _errors(c),
        })

    changed = [d for d in case_diff if d["changed"]]
    improved = sum(1 for d in changed if _improved(d))
    regressed = len(changed) - improved

    return {
        "case_diff": case_diff,
        "verdict_delta": verdict_delta,
        "total_changed": len(changed),
        "improved": improved,
        "regressed": regressed,
    }


def _improved(d: dict[str, Any]) -> bool:
    """True if cur_verdict is better than prev_verdict (rank order)."""
    rank = {"ERROR": 0, "WRONG": 1, "PARTIAL": 2, "CORRECT": 3}
    p, c = d.get("prev_verdict"), d.get("cur_verdict")
    if p is None or c is None:
        return False
    return rank.get(c, 0) > rank.get(p, 0)
