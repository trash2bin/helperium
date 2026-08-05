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

    sorted_dur = sorted(durations)
    idx = min(int(n * 0.95), n - 1)
    report.p95_duration_ms = sorted_dur[idx] if sorted_dur else 0.0

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
    lines.append(f"Avg tokens: {report.avg_total_tokens:.0f}")
    lines.append(f"Avg duration: {report.avg_duration_ms:.0f}ms")
    lines.append(f"P95 duration: {report.p95_duration_ms:.0f}ms")
    lines.append(f"Avg cost: ${report.avg_cost_usd:.4f}")
    lines.append(f"Total cost: ${report.total_cost_usd:.4f}")
    lines.append("")
    lines.append("Failed cases:")
    if not report.case_results:
        lines.append("  (no cases)")
    for case in report.case_results:
        if not case.success:
            ev = case.eval_result
            reason = "; ".join(ev.reasons[:3]) if ev else "no eval"
            lines.append(f"  - {case.case_id}: {case.question}")
            lines.append(f"      tool={ev.tool_ok if ev else '-'} "
                         f"retrieval={ev.retrieval_ok if ev else '-'} "
                         f"answer={ev.answer_ok if ev else '-'} "
                         f"halluc={ev.hallucination if ev else '-'} "
                         f"| {reason}")
    return "\n".join(lines)


def report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
    """Serialize a BenchmarkReport to a plain dict (for JSON output)."""
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
        "avg_total_tokens": report.avg_total_tokens,
        "avg_prompt_tokens": report.avg_prompt_tokens,
        "avg_completion_tokens": report.avg_completion_tokens,
        "avg_llm_calls": report.avg_llm_calls,
        "avg_tool_calls": report.avg_tool_calls,
        "avg_iterations": report.avg_iterations,
        "avg_duration_ms": report.avg_duration_ms,
        "p95_duration_ms": report.p95_duration_ms,
        "avg_cost_usd": report.avg_cost_usd,
        "total_cost_usd": report.total_cost_usd,
        "cases": [
            {
                "case_id": c.case_id,
                "question": c.question,
                "category": c.category,
                "success": c.success,
                "eval": {
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
                },
                "outcome": c.outcome,
                "final_text": c.final_text[:500],
            }
            for c in report.case_results
        ],
    }
