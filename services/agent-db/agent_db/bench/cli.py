"""CLI for the Core Benchmark — run cases, aggregate, report.

Usage:
    uv run --package agent-db python -m agent_db.bench.cli run \\
        agent-db/agent_db/bench/cases/autoparts.json \\
        --agent-name autoparts-assistant --tenant-id autoparts \\
        --api-url http://127.0.0.1:8081 --backlog-dir ./backlog \\
        --output benchmark_report.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer

from .evaluator import DeterministicEvaluator
from .models import TestCase
from .report import aggregate_report, print_report, report_to_dict
from .runner import BenchmarkRunner

app = typer.Typer(help="Core Benchmark for Helperium (deterministic, no LLM judge).")


def _load_cases(cases_file: Path) -> list[TestCase]:
    """Load test cases from a JSON file."""
    if not cases_file.exists():
        raise typer.BadParameter(f"Cases file not found: {cases_file}")
    data = json.loads(cases_file.read_text(encoding="utf-8"))
    raw_cases = data.get("cases", data) if isinstance(data, dict) else data
    return [TestCase.from_dict(c) for c in raw_cases]


@app.command(name="run")
def run_cmd(
    cases_file: Path = typer.Argument(
        ..., help="Path to cases JSON (e.g. cases/autoparts.json)"
    ),
    agent_name: str = typer.Option("autoparts-assistant", help="Agent name"),
    tenant_id: str = typer.Option("autoparts", help="Tenant ID"),
    api_url: str = typer.Option("http://127.0.0.1:8081", help="API service URL"),
    backlog_dir: Path | None = typer.Option(
        None, help="Backlog directory (default: auto-detect)"
    ),
    bench_log_dir: Path = typer.Option(
        "", help="Отдельный каталог для bench-логов (default: ./bench-backlog)"
    ),
    output: Path = typer.Option(
        "benchmark_report.json", help="Output report JSON file"
    ),
    admin_token: str = typer.Option("", help="Bearer token for admin API"),
    timeout: float = typer.Option(120.0, help="Per-question timeout (seconds)"),
    delay: float = typer.Option(
        2.5, help="Delay between cases (seconds) — respects api-service rate limit"
    ),
    quiet: bool = typer.Option(False, help="Suppress per-case progress"),
) -> None:
    """Run the benchmark on test cases and write a report."""
    cases = _load_cases(cases_file)
    if not cases:
        typer.echo("No cases loaded — check cases file.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loaded {len(cases)} cases from {cases_file}")
    typer.echo(f"Agent: {agent_name} | Tenant: {tenant_id} | API: {api_url}")
    if backlog_dir:
        typer.echo(f"Backlog dir: {backlog_dir}")

    runner = BenchmarkRunner(
        api_url=api_url,
        agent_name=agent_name,
        tenant_id=tenant_id,
        admin_token=admin_token,
        backlog_dir=str(backlog_dir) if backlog_dir else None,
        bench_log_dir=str(bench_log_dir) if bench_log_dir else None,
        timeout=timeout,
    )
    evaluator = DeterministicEvaluator()

    runs = []
    evals = []
    t_start = time.monotonic()

    for i, case in enumerate(cases, 1):
        if not quiet:
            typer.echo(f"[{i}/{len(cases)}] {case.category}: {case.question}")
        run_res = runner.run_case(case.question)
        eval_res = evaluator.evaluate(case, run_res)
        runs.append(run_res)
        evals.append(eval_res)
        # Respect api-service rate limit (CHAT_RATE_LIMIT, default 30/min)
        if i < len(cases) and delay > 0:
            time.sleep(delay)

    wall = time.monotonic() - t_start

    report = aggregate_report(cases, runs, evals)
    report.total_duration_wall_ms = wall * 1000

    # run metadata (for regressions)
    try:
        import subprocess

        git_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except Exception:
        git_commit = ""
    report.run_metadata = {
        "git_commit": git_commit,
        "model": agent_name,
        "dataset": str(cases_file),
        "cases_count": len(cases),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Console report
    text = print_report(report)
    typer.echo(text)

    # JSON report
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = report_to_dict(report)
        data["wall_duration_ms"] = report.total_duration_wall_ms
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        typer.echo(f"\nReport written to {out_path}")

    # Exit code: fail if any WRONG/ERROR . PARTIAL → warning, not failure.
    wrong_or_error = report.verdict_counts.get("WRONG", 0) + report.verdict_counts.get(
        "ERROR", 0
    )
    if wrong_or_error > 0:
        typer.echo(f"⚠️  {wrong_or_error} WRONG/ERROR cases — exit 1")
        raise typer.Exit(1)
    if report.verdict_counts.get("PARTIAL", 0) > 0:
        partial = report.verdict_counts.get("PARTIAL", 0)
        typer.echo(
            f"ℹ️  {partial} PARTIAL cases (no critical errors, but defects) — exit 0"
        )


if __name__ == "__main__":
    app()
