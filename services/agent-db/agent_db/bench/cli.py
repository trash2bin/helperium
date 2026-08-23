"""CLI for the deterministic Helperium agent benchmark.

Usage:
    uv run --package agent-db python -m agent_db.bench.cli run \
        agent-db/agent_db/bench/cases/autoparts.json \
        --agent-name autoparts-assistant --tenant-id autoparts \
        --api-url http://127.0.0.1:8081 --backlog-dir ./backlog
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests
import typer

from .agent_policy import (
    AUTOPARTS_BENCHMARK_POLICY_NAME,
    sync_autoparts_benchmark_agent_policy,
)
from .evaluator import DeterministicEvaluator
from .models import TestCase
from .reader import find_backlog_dir
from .report import aggregate_report, print_report, report_to_dict
from .run_guard import BenchmarkRunGuard, BenchmarkRunInProgressError
from .runner import BenchmarkPreflightError, BenchmarkRunner

app = typer.Typer(help="Core Benchmark for Helperium (deterministic, no LLM judge).")


def _load_cases(
    cases_file: Path, *, include_deprecated: bool = False
) -> list[TestCase]:
    """Load active cases, optionally including deprecated fixture history."""
    if not cases_file.exists():
        raise typer.BadParameter(f"Cases file not found: {cases_file}")
    data = json.loads(cases_file.read_text(encoding="utf-8"))
    raw_cases = data.get("cases", data) if isinstance(data, dict) else data
    cases = [TestCase.from_dict(c) for c in raw_cases]
    if include_deprecated:
        return cases
    return [case for case in cases if not case.deprecated]


def _git_commit() -> str:
    """Return the current short commit when the CLI is run from a Git worktree."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except Exception:
        return ""


def _write_report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


@app.command(name="sync-agent-policy")
def sync_agent_policy_cmd(
    agent_name: str = typer.Option("autoparts-assistant", help="Agent name"),
    api_url: str = typer.Option("http://127.0.0.1:8081", help="API service URL"),
    admin_token: str = typer.Option("", help="Bearer token for admin API"),
) -> None:
    """Synchronize the committed autoparts benchmark policy through Agent API."""
    try:
        payload = sync_autoparts_benchmark_agent_policy(
            api_url=api_url,
            agent_name=agent_name,
            admin_token=admin_token,
        )
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        typer.echo(f"Unable to synchronize benchmark policy: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Synchronized {AUTOPARTS_BENCHMARK_POLICY_NAME} for agent "
        f"{payload.get('name', agent_name)}"
    )


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
    bench_log_dir: Path | None = typer.Option(
        None,
        help="Artifact root; each run writes to runs/<run_uuid>/ (default: ./bench-backlog)",
    ),
    output: Path | None = typer.Option(
        None,
        help="Optional additional report copy; the primary report always stays in the run directory",
    ),
    admin_token: str = typer.Option("", help="Bearer token for admin API"),
    timeout: float = typer.Option(300.0, help="Per-question timeout (seconds)"),
    delay: float = typer.Option(
        2.5, help="Delay between cases (seconds) — respects api-service rate limit"
    ),
    quiet: bool = typer.Option(False, help="Suppress per-case progress"),
) -> None:
    """Run one exclusive benchmark and write isolated UUID-scoped evidence."""
    cases = _load_cases(cases_file)
    if not cases:
        typer.echo("No cases loaded — check cases file.", err=True)
        raise typer.Exit(1)

    resolved_backlog_dir = Path(backlog_dir) if backlog_dir else find_backlog_dir()
    artifact_root = (
        Path(bench_log_dir) if bench_log_dir else Path.cwd() / "bench-backlog"
    )
    guard = BenchmarkRunGuard(
        api_url=api_url,
        lock_root=resolved_backlog_dir,
        artifact_root=artifact_root,
    )
    try:
        context = guard.acquire()
    except BenchmarkRunInProgressError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    report_path = context.run_dir / "benchmark_report.json"
    completed = False
    try:
        typer.echo(f"Loaded {len(cases)} cases from {cases_file}")
        typer.echo(f"Agent: {agent_name} | Tenant: {tenant_id} | API: {api_url}")
        typer.echo(f"Backlog dir: {resolved_backlog_dir}")
        typer.echo(f"Run UUID: {context.run_uuid}")
        typer.echo(f"Run evidence: {context.run_dir}")

        runner = BenchmarkRunner(
            api_url=api_url,
            agent_name=agent_name,
            tenant_id=tenant_id,
            admin_token=admin_token,
            backlog_dir=resolved_backlog_dir,
            bench_log_dir=context.run_dir,
            timeout=timeout,
        )
        evaluator = DeterministicEvaluator()

        try:
            preflight = runner.preflight()
        except BenchmarkPreflightError as exc:
            _write_report(
                context.run_dir / "preflight.json",
                {"status": "failed", "error": str(exc)},
            )
            typer.echo("\n❌ Benchmark preflight failed — кейсы не запускались.", err=True)
            typer.echo(f"   {exc}", err=True)
            typer.echo(
                f"   Evidence: {context.run_dir / 'run-manifest.json'}", err=True
            )
            raise typer.Exit(2) from exc
        _write_report(
            context.run_dir / "preflight.json",
            {"status": "ok", **preflight},
        )
        typer.echo(
            f"✅ Preflight OK: API healthy, agent '{preflight['agent_name']}' reachable"
        )

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

        report = aggregate_report(cases, runs, evals)
        report.total_duration_wall_ms = (time.monotonic() - t_start) * 1000
        report.run_metadata = {
            "api_url": api_url.rstrip("/"),
            "artifact_dir": str(context.run_dir),
            "cases_count": len(cases),
            "dataset": str(cases_file),
            "git_commit": _git_commit(),
            "manifest_path": str(context.manifest_path),
            "model": agent_name,
            "run_uuid": context.run_uuid,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        typer.echo(print_report(report))

        data = report_to_dict(report)
        data["wall_duration_ms"] = report.total_duration_wall_ms
        _write_report(report_path, data)
        typer.echo(f"\nReport written to {report_path}")

        if output:
            additional_path = Path(output)
            if additional_path.resolve() != report_path.resolve():
                _write_report(additional_path, data)
                typer.echo(f"Additional report copy written to {additional_path}")

        completed = True
    finally:
        guard.finalize(
            status="completed" if completed else "failed",
            report_path=report_path if completed else None,
        )

    # Exit code: fail if any WRONG/ERROR. PARTIAL → warning, not failure.
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
