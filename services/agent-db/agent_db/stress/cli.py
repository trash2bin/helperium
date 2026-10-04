"""CLI for the load harness: one command, one measured stand, one artefact tree.

    uv run --package agent-db python -m agent_db.stress run \
        services/agent-db/agent_db/stress/profiles/mcp-tool-call-l1.json \
        --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
        --tenants stress-1,stress-2,stress-3,stress-4

What the command owns, in order: the run lock, the run directory, the preflight,
the ladder, and the report. Nothing here computes a number - the ladder does
that and :mod:`agent_db.stress.records` computes the percentiles - so this module
is deliberately thin and boring, and the two things it does add are the ones a
design doc cannot test: refusing to start when the stand is not ready, and
leaving behind an artefact tree that names what was measured (§6, §10).

Exit codes are part of the interface:

``0``   the run completed and its evidence is on disk (the *verdict* may still be
        a failure - "the platform held 40 rps" and "the platform did not" are
        both completed measurements, and a CI gate that conflated them with a
        crash would be less useful than an artefact);
``2``   the run never started: preflight refused, the profile is unrunnable, or
        another run owns the target;
``3``   the run started and stopped early, with ``status.json.abort_reason``
        naming why.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import typer

from .chat_driver import ChatDriver
from .chat_transport import ChatTransport
from .driver import McpToolDriver
from .evidence import (
    RunLayout,
    StageEvidenceSink,
    write_preflight,
    write_profile_snapshot,
    write_status,
)
from .fixture import effective_fixture, load_fixture
from .guard import StressRunGuard, StressRunInProgressError, write_json_atomic
from .ladder import (
    DEFAULT_RATES,
    JUDGED_METRIC_READY_LAYERS,
    LAYER_RULE,
    LadderPlan,
    Prediction,
    predict_tenant_ceiling_rps,
    run_ladder,
)
from .manifest import BinarySpec, RunManifest, collect_code, collect_environment
from .metrics_scrape import MetricsCollector, targets_from
from .preflight import (
    budget_overrides_from,
    budgets_for_run,
    chat_preflight,
    mcp_preflight,
)
from .profile import LoadProfile, ProfileValidationError, load_profile
from .report import build_report, render_markdown, write_report
from .runner import StageRunner, WorkloadPlan
from .transport import McpTransport

app = typer.Typer(
    help="Helperium load harness: knee ladders with a run manifest (doc/stress).",
    no_args_is_help=True,
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ABORTED = 3
# A ladder that produced no knee (first rung already over budget,
# or generator-saturated there) is a measured failure, not a success.
EXIT_FAILED = 4


def _driver_stack(
    profile: LoadProfile,
    *,
    mcp_url: str,
    api_key: str,
    chat_url: str,
    agent: str,
    timeout_s: float,
    turn_timeout_s: float,
    fixture: Any,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Transport, driver, preflight callable and their URLs for one layer.

    L1 measures the tool path (gateway, data-service, tenant DB); L2+ measure the
    chat path (SSE, session store, the per-tenant MCP client). Same scheduler,
    same ladder, same record - a different pipe (§1).
    """
    if profile.target_layer == "L1":
        transport: Any = McpTransport(
            mcp_url, api_key=api_key or None, timeout_s=timeout_s
        )
        driver: Any = McpToolDriver(
            transport, arguments=effective_fixture(fixture, profile)
        )
        return transport, driver, mcp_preflight, {"mcp_url": mcp_url}
    transport = ChatTransport(
        chat_url, agent=agent or None, timeout_s=max(timeout_s, turn_timeout_s)
    )
    driver = ChatDriver(transport, turn_timeout_s=turn_timeout_s)
    return transport, driver, chat_preflight, {"chat_url": chat_url, "agent": agent}


def _fail(message: str, code: int = EXIT_REFUSED) -> typer.Exit:
    typer.echo(f"❌ {message}", err=True)
    return typer.Exit(code)


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _tenants_for(profile: LoadProfile, raw: str) -> list[str]:
    """Resolve the tenant list, defaulting to names the profile itself implies.

    The count is a property of the profile, not a free parameter: the analytic
    forecast and the comparison between runs both assume it, so a caller that
    supplies a different number is running a different profile and is told so.
    """
    tenants = (
        _split_csv(raw)
        if raw and raw != "auto"
        else [f"stress-{index + 1}" for index in range(profile.tenants.count)]
    )
    if len(tenants) != profile.tenants.count:
        raise ProfileValidationError(
            f"profile {profile.name!r} declares tenants.count={profile.tenants.count} "
            f"but {len(tenants)} tenant id(s) were given: the fan-out is part of the "
            "measured shape, not a free parameter"
        )
    return tenants


def _rates_for(profile: LoadProfile, raw: str) -> tuple[float, ...]:
    if not raw:
        return DEFAULT_RATES
    try:
        return tuple(float(item) for item in _split_csv(raw))
    except ValueError as exc:
        raise ProfileValidationError(f"rates must be numbers: {raw!r}") from exc


def _build_plan(
    profile: LoadProfile,
    *,
    rates: str,
    duration_s: float | None,
    warmup_s: float | None,
    repeats: int | None,
    t_budget_ms: float | None,
    tolerance: float | None,
    max_pool_expansions: int | None,
    pool_margin_s: float | None = None,
    pool_headroom: float | None = None,
) -> LadderPlan:
    overrides: dict[str, Any] = {}
    if repeats is not None:
        overrides["repeats"] = repeats
    if tolerance is not None:
        overrides["tolerance"] = tolerance
    if max_pool_expansions is not None:
        overrides["max_pool_expansions"] = max_pool_expansions
    # Pool sizing knobs (Трек 0 плана api-service-decomposition-plan.md: снять
    # потолок генератора, а не платформы). None = дефолт §2 (margin 1.0 s),
    # то есть поведение лестницы без этих флагов не меняется.
    if pool_margin_s is not None:
        overrides["pool_margin_s"] = pool_margin_s
    if pool_headroom is not None:
        overrides["pool_headroom"] = pool_headroom
    return LadderPlan.for_profile(
        profile,
        rates=_rates_for(profile, rates),
        t_budget_ms=t_budget_ms,
        duration_s=duration_s,
        warmup_s=warmup_s,
        **overrides,
    )


def _prediction_for(
    profile: LoadProfile, tool_p95_ms: float | None, tenants: Sequence[str]
) -> Prediction | None:
    """§3's analytic forecast, written before the ladder runs.

    It is what turns "the knee moved when the fan-out changed" from a story into
    a check: the model predicts a ceiling from the per-tenant serialization of
    tool calls, and the report prints predicted against found.
    """
    if tool_p95_ms is None:
        return None
    return predict_tenant_ceiling_rps(
        p95_tool_ms=tool_p95_ms,
        calls_per_turn=profile.expected_calls_per_turn(),
        tenants=len(tenants),
    )


def _binaries_from(raw: Sequence[str] | None) -> list[BinarySpec]:
    """Parse ``label=path[:source_root]`` into the manifest's §6 binary specs.

    On a native stand the binary is the only runtime artefact there is: no image
    digest exists, so without this the manifest has nothing tying the numbers to
    a build and refuses to call the run publishable - correctly, but with no way
    for the operator to fix it. ``source_root`` defaults to the conventional
    service directory for the label, because that is the pairing the freshness
    check exists for; passing it explicitly covers a stand that built from
    somewhere else.
    """
    specs: list[BinarySpec] = []
    for item in raw or ():
        label, sep, rest = item.partition("=")
        label = label.strip()
        if not sep or not label or not rest.strip():
            raise ValueError(
                f"binary {item!r} is not LABEL=PATH[:SOURCE_ROOT] (for example "
                "mcp-gateway=/tmp/stand/mcp-gateway)"
            )
        path_text, _, source_text = rest.partition(":")
        if not path_text.strip():
            raise ValueError(f"binary {item!r} names no path")
        source_root = (
            source_text.strip() if source_text.strip() else f"services/{label}"
        )
        specs.append(
            BinarySpec(label=label, path=path_text.strip(), source_root=source_root)
        )
    return specs


def _collect_code_safely(
    repo_root: Path,
    *,
    api_workers: int,
    binaries: Sequence[BinarySpec] | None = None,
):
    """Read the repository provenance, or record why it could not be read.

    A capacity run happens on a stand, and the stand is not always a checkout:
    a tarball on an offline host is a normal way to run a stress test. §6 wants
    the commit recorded, not a crash, so an unreadable tree becomes an empty
    ``CodeInfo`` - and because it has no commit and no binaries, the manifest
    then refuses to call the run publishable, which is the honest outcome for a
    number nobody can attribute to a build.
    """
    from .manifest import CodeInfo

    try:
        return collect_code(repo_root, api_workers=api_workers, binaries=binaries)
    except Exception as exc:  # noqa: BLE001 - provenance is evidence, not a gate
        typer.echo(
            f"⚠️  code provenance unreadable in {repo_root}: {exc}. "
            "The run is not publishable as capacity (§6).",
            err=True,
        )
        return CodeInfo(commit="", branch="", dirty=True, api_workers=api_workers)


def _write_server_metrics(
    layout: Any, collector: MetricsCollector, *, quiet: bool
) -> None:
    """Write ``server/<service>.json`` and drop the gap marker it contradicts.

    Writing is best-effort by design: this runs on the abort path, where the run
    already has a reason to report, and a failure to persist an observation must
    not replace that reason with an IO error from the observer.
    """
    try:
        payloads = collector.payloads()
        if not payloads:
            return
        for service, payload in payloads.items():
            write_json_atomic(layout.server_path(service), payload)
        # The marker says "not collected yet (phase 1)"; leaving it beside real
        # slices would put two contradictory claims in one artefact.
        layout.clear_gap_marker("server")
        if not quiet:
            for service, payload in sorted(payloads.items()):
                gaps = payload["gap_count"]
                detail = f"{payload['scrapes']} scrape(s)"
                if gaps:
                    detail += f", {gaps} gap(s): {'; '.join(payload['gap_reasons'])}"
                typer.echo(f"metrics  {service}: {detail}")
    except Exception as exc:  # noqa: BLE001 - an observation, not the run
        typer.echo(f"⚠️  server metrics not written: {exc}", err=True)


@app.command("check")
def check_cmd(
    profile_path: Path = typer.Argument(..., help="Load profile JSON"),
    mcp_url: str = typer.Option(
        "http://127.0.0.1:8083/mcp", help="MCP Streamable HTTP endpoint"
    ),
    api_key: str = typer.Option("", envvar="MCP_API_KEY", help="X-API-Key for /mcp"),
    tenants: str = typer.Option("auto", help="Comma-separated tenant ids, or 'auto'"),
    rates: str = typer.Option("", help="Rungs to plan (default: 5,10,20,40,80)"),
    budget: list[str] = typer.Option(
        None, "--budget", help="§5 budget in effect on the stand, NAME=VALUE"
    ),
    chat_url: str = typer.Option(
        "http://127.0.0.1:8081", help="api-service base URL, for L2+ layers"
    ),
    agent: str = typer.Option(
        "", help="Named agent for /api/chat/{name}; empty = direct chat"
    ),
    timeout_s: float = typer.Option(30.0, help="Per-call timeout (seconds)"),
    turn_timeout_s: float = typer.Option(
        120.0, help="How long one chat turn may stream"
    ),
    skip_probe: bool = typer.Option(False, help="Skip the one-turn-per-tenant probe"),
    json_output: Path | None = typer.Option(
        None, help="Also write the preflight payload here"
    ),
) -> None:
    """Preflight only: is this stand able to run this profile at all?

    Runs before any ladder and writes nothing into a run directory, so it is safe
    to point at a stand that is still being provisioned.
    """
    try:
        profile = load_profile(profile_path)
        fixture = load_fixture(profile.fixture)
        tenant_ids = _tenants_for(profile, tenants)
        overrides = budget_overrides_from(budget)
    except (ProfileValidationError, ValueError) as exc:
        raise _fail(str(exc)) from exc

    budgets = budgets_for_run(overrides)
    transport, driver, preflight, endpoints = _driver_stack(
        profile,
        mcp_url=mcp_url,
        api_key=api_key,
        chat_url=chat_url,
        agent=agent,
        timeout_s=timeout_s,
        turn_timeout_s=turn_timeout_s,
        fixture=fixture,
    )
    result = preflight(
        profile=profile,
        fixture=fixture,
        transport=transport,
        driver=driver,
        tenants=tenant_ids,
        top_rps=max(_rates_for(profile, rates)),
        budgets=budgets,
        probe_tenants=not skip_probe,
    )
    payload = result.payload() | {
        "profile": profile.name,
        "target_layer": profile.target_layer,
        "judged_metric": LAYER_RULE.get(profile.target_layer),
        "tenants": tenant_ids,
        "endpoints": endpoints,
        "budgets": budgets,
    }
    if json_output is not None:
        write_json_atomic(Path(json_output), payload)

    for check in result.checks:
        marker = "✅" if check["status"] != "failed" else "❌"
        detail = {
            key: value for key, value in check.items() if key not in {"check", "status"}
        }
        typer.echo(f"{marker} {check['check']}: {check['status']} {detail or ''}")
    if not result.ok:
        typer.echo("", err=True)
        raise _fail("preflight refused: " + "; ".join(result.refusals))
    typer.echo(f"\n✅ {profile.name} is runnable on {mcp_url}")


@app.command("run")
def run_cmd(
    profile_path: Path = typer.Argument(..., help="Load profile JSON"),
    mcp_url: str = typer.Option(
        "http://127.0.0.1:8083/mcp", help="MCP Streamable HTTP endpoint"
    ),
    api_key: str = typer.Option("", envvar="MCP_API_KEY", help="X-API-Key for /mcp"),
    chat_url: str = typer.Option(
        "http://127.0.0.1:8081", help="api-service base URL, for L2+ layers"
    ),
    agent: str = typer.Option(
        "", help="Named agent for /api/chat/{name}; empty = direct chat"
    ),
    turn_timeout_s: float = typer.Option(
        120.0, help="How long one chat turn may stream"
    ),
    tenants: str = typer.Option("auto", help="Comma-separated tenant ids, or 'auto'"),
    artifact_root: Path = typer.Option(
        Path("test-results/stress"), help="Where <run_uuid>/ evidence is written"
    ),
    repo_root: Path = typer.Option(Path("."), help="Repository root for the manifest"),
    rates: str = typer.Option("", help="Rungs to run (default: 5,10,20,40,80)"),
    duration_s: float = typer.Option(None, help="Stage length in seconds"),
    warmup_s: float = typer.Option(None, help="Warm-up inside each stage"),
    repeats: int = typer.Option(None, help="Knee repeats (≥2 makes a headline)"),
    t_budget_ms: float = typer.Option(None, help="§7 budget T for the verdict"),
    tolerance: float = typer.Option(None, help="Bisection tolerance (0.10 = ±10%)"),
    max_pool_expansions: int = typer.Option(
        None, help="How many times a rung may double its pool"
    ),
    no_expand_pool: bool = typer.Option(
        False, "--no-expand-pool", help="Do not widen the pool on an invalid stage"
    ),
    pool_margin_s: float = typer.Option(
        None,
        "--pool-margin-s",
        help="Pool size margin in seconds (§2: workers = rps x (T + margin) x "
        "headroom). Lower it to relieve the generator: every worker carries a "
        "busy-wait tail, so a wide pool burns the generator's core before the "
        "platform is reached (default: 1.0 s from §2)",
    ),
    pool_headroom: float = typer.Option(
        None,
        "--pool-headroom",
        help="Multiplier on the computed pool size (§2, default 1.0)",
    ),
    tool_p95_ms: float = typer.Option(
        None, help="Measured p95 tool latency, for §3's analytic forecast"
    ),
    budget: list[str] = typer.Option(
        None, "--budget", help="§5 budget in effect on the stand, NAME=VALUE"
    ),
    binary: list[str] = typer.Option(
        None,
        "--binary",
        help="Runtime artefact the stand ran, LABEL=PATH[:SOURCE_ROOT] (§6). "
        "Required for a publishable native run, where no image digest exists; "
        "a binary older than its sources is refused as stale",
    ),
    generator_host: str = typer.Option(
        "same-host", help="Where this generator runs, relative to the SUT (§6)"
    ),
    api_workers: int = typer.Option(1, help="Workers the api-service ran with"),
    tenant_engine: str = typer.Option(
        "", help="Tenant DB engine, e.g. sqlite/postgres"
    ),
    tenant_db_size: str = typer.Option("", help="Tenant DB size, as measured"),
    backlog_mode: str = typer.Option("", help="BACKLOG_MODE the services ran with"),
    no_host_probe: bool = typer.Option(
        False,
        "--no-host-probe",
        help="Skip host probes (governor, lsblk, docker); the manifest then "
        "records them as gaps and the run is not publishable",
    ),
    admin_token: str = typer.Option(
        "",
        envvar="ADMIN_TOKEN",
        help="Bearer for /metrics on data-service (fail-closed behind the same "
        "token as /admin/*)",
    ),
    api_bearer_token: str = typer.Option(
        "",
        envvar="API_BEARER_TOKEN",
        help="Bearer for /metrics on api-service. /metrics there sits behind the "
        "same dependency as /admin/* (private_router), so it answers to "
        "API_BEARER_TOKEN, not ADMIN_TOKEN: with the admin token the scrape "
        "gets a 403 and the run's server-side CPU/lock evidence becomes a gap "
        "(Трек 0 плана api-service-decomposition-plan.md, п.0.3)",
    ),
    metrics_target: list[str] = typer.Option(
        None,
        "--metrics-target",
        help="Extra /metrics endpoint to slice, SERVICE=URL. data-service is "
        "never derived: the harness does not talk to it directly",
    ),
    metrics_interval_s: float = typer.Option(
        5.0, help="How often server /metrics is sliced into server/<service>.json"
    ),
    no_server_metrics: bool = typer.Option(
        False,
        "--no-server-metrics",
        help="Do not scrape server /metrics; server/ keeps its gap marker and "
        "the run's numbers stay self-checked",
    ),
    timeout_s: float = typer.Option(30.0, help="Per-call timeout (seconds)"),
    skip_probe: bool = typer.Option(
        False, help="Skip the one-turn-per-tenant fixture probe"
    ),
    stub_log: Path = typer.Option(
        None,
        "--stub-log",
        help="Stub timing log (from the stub command) to copy into stub/ - "
        "§7's platform_overhead is computed from it",
    ),
    dry_run: bool = typer.Option(
        False, help="Preflight, plan and write evidence, but schedule no ticks"
    ),
    quiet: bool = typer.Option(False, help="Suppress per-stage progress"),
) -> None:
    """Run a knee ladder and write the §10 evidence tree."""
    try:
        profile = load_profile(profile_path)
        fixture = load_fixture(profile.fixture)
        tenant_ids = _tenants_for(profile, tenants)
        overrides = budget_overrides_from(budget)
        binary_specs = _binaries_from(binary)
        plan = _build_plan(
            profile,
            rates=rates,
            duration_s=duration_s,
            warmup_s=warmup_s,
            repeats=repeats,
            t_budget_ms=t_budget_ms,
            tolerance=tolerance,
            max_pool_expansions=max_pool_expansions,
            pool_margin_s=pool_margin_s,
            pool_headroom=pool_headroom,
        )
    except (ProfileValidationError, ValueError) as exc:
        raise _fail(str(exc)) from exc

    guard = StressRunGuard(
        target=mcp_url, lock_root=artifact_root, artifact_root=artifact_root
    )
    try:
        context = guard.acquire()
    except StressRunInProgressError as exc:
        raise _fail(str(exc)) from exc

    # One guarded section for everything the run writes: an error or a
    # Ctrl-C at ANY point after the lock must still leave a truthful
    # status.json (§10: aborted has a reason), stopped workers, and a
    # released lock. A tree stuck at "running" while the generator is
    # dead lies to the next reader; a lock freed while workers still fire
    # lets a successor measure the corpse's traffic.
    stopped: _RunStopped | None = None
    runner: StageRunner | None = None
    collector: MetricsCollector | None = None
    # Pre-bound because the finally block reads it: if RunLayout.create itself
    # fails, an unbound name there would replace the real cause with a NameError.
    layout: RunLayout | None = None
    try:
        layout = RunLayout.create(context.run_dir)
        budgets = budgets_for_run(overrides)
        transport, driver, preflight, endpoints = _driver_stack(
            profile,
            mcp_url=mcp_url,
            api_key=api_key,
            chat_url=chat_url,
            agent=agent,
            timeout_s=timeout_s,
            turn_timeout_s=turn_timeout_s,
            fixture=fixture,
        )
        prediction = _prediction_for(profile, tool_p95_ms, tenant_ids)

        typer.echo(f"run      {context.run_uuid}")
        typer.echo(
            f"profile  {profile.name} ({profile.target_layer}, {profile.tenants.scope})"
        )
        typer.echo(f"target   {endpoints}")
        typer.echo(f"tenants  {', '.join(tenant_ids)}")
        typer.echo(f"rungs    {', '.join(f'{rate:g}' for rate in plan.rates)} rps")
        typer.echo(
            f"stages   {plan.duration_s:g}s ({plan.warmup_s:g}s warm-up), "
            f"T={plan.t_budget_ms:g} ms, repeats={plan.repeats}"
        )
        typer.echo(f"evidence {layout.root}")
        typer.echo("")

        write_profile_snapshot(layout, profile, profile_path)
        if stub_log is not None:
            # §10's stub/ holds the substrate's per-request timings: without
            # them the report's platform_overhead column has nothing to
            # subtract. Copy at start, so an aborted run keeps its evidence.
            if not stub_log.is_file():
                raise _RunStopped(
                    "failed", f"stub log not found: {stub_log}", EXIT_REFUSED
                )
            shutil.copy2(stub_log, layout.stub_path("stub-timings"))
        write_status(
            layout, "running", detail={"endpoints": endpoints, "profile": profile.name}
        )

        try:
            checked = preflight(
                profile=profile,
                fixture=fixture,
                transport=transport,
                driver=driver,
                tenants=tenant_ids,
                top_rps=max(plan.rates),
                budgets=budgets,
                probe_tenants=not skip_probe,
            )
        except Exception as exc:  # noqa: BLE001 - the stand failed, not the profile
            write_preflight(layout, {"status": "failed", "error": str(exc)})
            raise _RunStopped(
                "failed", f"preflight crashed: {exc}", EXIT_REFUSED
            ) from exc
        preflight = checked

        payload = preflight.payload() | {
            "profile": profile.name,
            "target_layer": profile.target_layer,
            "judged_metric": LAYER_RULE.get(profile.target_layer),
            "tenants": tenant_ids,
            "endpoints": endpoints,
            "budgets": budgets,
            "dry_run": dry_run,
        }
        write_preflight(layout, payload)
        if not preflight.ok:
            for check in preflight.checks:
                if check["status"] == "failed":
                    typer.echo(
                        f"❌ {check['check']}: {check.get('reason', '')}", err=True
                    )
            raise _RunStopped(
                "failed",
                "preflight refused: " + "; ".join(preflight.refusals),
                EXIT_REFUSED,
            )
        typer.echo("✅ preflight ok")

        manifest = RunManifest(
            environment=collect_environment(
                profile,
                profile_path=profile_path,
                generator_host=generator_host,
                tenant_engine=tenant_engine,
                tenant_db_size=tenant_db_size or None,
                api_workers=api_workers,
                backlog_mode=backlog_mode or None,
                budget_values=overrides,
                probe_host=not no_host_probe,
            ),
            code=_collect_code_safely(
                repo_root, api_workers=api_workers, binaries=binary_specs
            ),
            # The run has ONE identity: the uuid the guard minted when it took the
            # lock and named the evidence directory. Letting RunManifest default
            # its own would print a second uuid into the manifest and into
            # report-<uuid>.md, so the lock record, the directory and the report
            # could not be correlated - and §10 rests on exactly that join
            # ("a number without its run manifest is not evidence").
            run_uuid=context.run_uuid,
        )
        write_json_atomic(layout.manifest_path, json.loads(manifest.to_json()))

        if dry_run:
            write_status(layout, "completed", detail={"dry_run": True})
            typer.echo("dry run: no ticks were scheduled")
            typer.echo(f"blockers: {manifest.publication_blockers() or 'none'}")
            raise typer.Exit(EXIT_OK)

        sink = StageEvidenceSink(layout)
        runner = StageRunner(driver)

        # Server-side slices run alongside the ladder: without them every number
        # in the report is checked only against the harness's own records, which
        # is arithmetic rather than corroboration (§10). Collection never gates
        # the run - an unreachable endpoint becomes a named gap.
        if not no_server_metrics:
            try:
                scrape_targets = targets_from(
                    endpoints,
                    api_key=api_key,
                    admin_token=admin_token,
                    api_bearer=api_bearer_token,
                    extra=metrics_target or (),
                )
            except ValueError as exc:
                raise _fail(str(exc)) from exc
            if scrape_targets:
                collector = MetricsCollector(
                    scrape_targets, interval_s=metrics_interval_s
                )
                collector.start()

        def on_stage(outcome: Any, stage: Any, ticket: Any) -> None:
            sink(outcome, stage, ticket)
            if not quiet:
                # The same §7 rule the report applies, not a second one: where the
                # turn contains no model call the overhead *is* the compensated
                # percentile (L1, and L2 where the stub is instant), and where it
                # is genuinely unknown the cell says so. Printing ``nan`` made a
                # correct L1 rung look like a failed measurement on the console
                # while report.json held the number.
                if outcome.has_platform_overhead:
                    overhead = f"{outcome.stats.platform_overhead_p95:>8.2f} ms"
                elif profile.target_layer in JUDGED_METRIC_READY_LAYERS:
                    overhead = f"{outcome.stats.compensated_p95:>8.2f} ms"
                else:
                    overhead = f"{'n/a':>8}   "
                typer.echo(
                    f"  {outcome.target_rps:>6.1f} rps  {outcome.verdict:<7} "
                    f"p95 {outcome.p95:>8.2f} ms  overhead {overhead}  "
                    f"err {outcome.stats.error_rate * 100:>5.2f}%  "
                    f"gen {(outcome.cpu_measured_s or outcome.cpu_s) / outcome.measured_window_s * 100 if outcome.measured_window_s else 0:>5.1f}%  "
                    f"lag p99 {outcome.tick_lag_p99:>6.2f} ms"
                )

        # Everything past the preflight is one guarded section: an exception or a
        # Ctrl-C here must still leave a truthful status.json (§10: aborted has a
        # reason) and a released lock - a tree stuck at "running" while the
        # generator is dead lies to the next reader.
        try:
            result = run_ladder(
                runner=runner,
                plan=plan,
                workload=WorkloadPlan.from_profile(profile),
                profile=profile,
                tenants=tenant_ids,
                prediction=prediction,
                expand_pool=not no_expand_pool,
                on_stage=on_stage,
            )

            report = build_report(
                result=result,
                manifest=manifest,
                scope=profile.tenants.scope,
                profile_path=str(profile_path),
            )
            json_path, md_path = write_report(layout.root, report)
        except KeyboardInterrupt as exc:
            raise _RunStopped(
                "aborted", "interrupted by the operator (Ctrl-C)", EXIT_ABORTED
            ) from exc
        except Exception as exc:  # noqa: BLE001 - the stand died, not the profile
            raise _RunStopped(
                "aborted", f"ladder crashed: {type(exc).__name__}: {exc}", EXIT_ABORTED
            ) from exc

        if result.status == "unjudgeable":
            status = "aborted"
            exit_code = EXIT_ABORTED
            abort_reason = "the judged metric could not be computed"
        elif result.knee is None:
            # No knee: the first rung was already over budget (below_first_rung)
            # or the generator saturated there (invalid). Nothing was measured
            # that could be published, so "completed" + exit 0 would let
            # automation read a dead ladder as a successful capacity run -
            # that exact lie is what a live L2 run showed on the Arch stand.
            status = "failed"
            exit_code = EXIT_FAILED
            abort_reason = f"the ladder produced no knee ({result.status})"
        else:
            status = "completed"
            exit_code = EXIT_OK
            abort_reason = None
        write_status(
            layout,
            status,
            abort_reason=abort_reason,
            detail={"ladder_status": result.status},
        )
        guard.release()

        typer.echo("")
        typer.echo(render_markdown(report))
        typer.echo(f"report   {md_path}")
        typer.echo(f"raw      {layout.root / 'raw'}")
        blockers = manifest.publication_blockers()
        if blockers:
            typer.echo("")
            typer.echo("⛔ not publishable as capacity:")
            for blocker in blockers:
                typer.echo(f"   - {blocker}")
        raise typer.Exit(exit_code)
    except typer.Exit:
        raise
    except _RunStopped as stopped_run:
        stopped = stopped_run
    except KeyboardInterrupt:
        stopped = _RunStopped(
            "aborted", "interrupted by the operator (Ctrl-C)", EXIT_ABORTED
        )
    except Exception as exc:  # noqa: BLE001 - recorded, then exit 3
        stopped = _RunStopped(
            "aborted",
            f"ladder crashed: {type(exc).__name__}: {exc}",
            EXIT_ABORTED,
        )
    finally:
        # Stop the load before anything else: status and lock both promise
        # that this run's traffic is over.
        if runner is not None:
            runner.stop()
            runner.join_workers()
        # Then the observer, and only then write what it saw: the last slice is
        # taken after the final tick, so a stage's delta is bracketed on both
        # sides. This runs on the abort path too - a partial window with its
        # gaps named is evidence, while discarding it would leave a run that
        # measured the server and recorded nothing about it.
        if collector is not None and layout is not None:
            collector.stop()
            _write_server_metrics(layout, collector, quiet=quiet)
        if stopped is not None:
            assert layout is not None  # noqa: S101 - layout precedes every stop
            write_status(layout, stopped.status, abort_reason=stopped.reason)
            typer.echo(stopped.reason, err=True)
        guard.release_quiet()
    if stopped is not None:
        raise typer.Exit(stopped.exit_code)


@app.command("stub")
def stub_cmd(
    p50_ms: float = typer.Option(800.0, help="Nominal service time (profile's p50)"),
    p95_ms: float = typer.Option(2500.0, help="Tail service time (profile's p95)"),
    prompt_ms_per_token: float = typer.Option(
        2.0,
        help="Latency added per prompt token (§4: latency as a function of the prompt)",
    ),
    concurrency: int = typer.Option(
        256, help="Stub's own parallelism (§3 provider_concurrency)"
    ),
    script: Path | None = typer.Option(
        None, help="JSONL of rounds: {tool, arguments}; the turn finishes after them"
    ),
    log: Path = typer.Option(
        Path("stub-timings.jsonl"), help="Where the per-request timing log goes"
    ),
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(9099, help="Port"),
    response_chunks: int = typer.Option(
        1,
        "--response-chunks",
        help="Deliver the response body in N chunked-encoding pieces (1 = one "
        "Content-Length body, the default and the pre-Track-0 behaviour). The "
        "payload is unchanged: chunking is transport realism for L2, not a new "
        "response contract",
    ),
    chunk_delay_ms: float = typer.Option(
        0.0,
        "--chunk-delay-ms",
        help="Pause between response chunks. Adds to service_ms so §7 does not "
        "charge the platform for the stub's own delivery time",
    ),
) -> None:
    """Run the stub LLM (§4): OpenAI-compatible, non-streaming, scripted tool calls.

    Point the agent's llm_config at ``http://<host>:<port>/v1`` (provider
    ``openai``, any api_key). The timing log is evidence: copy it into the run
    directory afterwards, because platform_overhead is computed from it.
    """
    from .stub_llm import LatencyModel, StubServer

    rounds: list[dict[str, Any]] = []
    if script is not None:
        for number, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            entry = json.loads(stripped)
            if not isinstance(entry, dict):
                raise _fail(f"{script}:{number}: a script line must be an object")
            rounds.append(entry)

    server = StubServer(
        latency=LatencyModel.from_profile(
            p50_ms=p50_ms,
            p95_ms=p95_ms,
            prompt_ms_per_token=prompt_ms_per_token,
        ),
        script=rounds,
        log_path=log,
        concurrency=concurrency,
        host=host,
        port=port,
        response_chunks=response_chunks,
        chunk_delay_ms=chunk_delay_ms,
    )
    server.start()
    typer.echo(f"stub llm on {server.base_url}/v1 (log: {log})")
    if response_chunks > 1 or chunk_delay_ms > 0:
        typer.echo(
            f"chunked delivery: {response_chunks} chunk(s), "
            f"{chunk_delay_ms:g} ms between them"
        )
    typer.echo("press Ctrl+C to stop")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
        typer.echo("\nstub stopped")


class _RunStopped(Exception):
    """A started run that stopped early: terminal status + exit code attached.

    Raised by the guarded section's inner paths so exactly one place writes the
    terminal status, stops the workers, releases the lock, and picks the exit
    code - a path that forgets any of those steps leaves a tree that lies.
    """

    def __init__(self, status: str, reason: str, exit_code: int) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.exit_code = exit_code


def main() -> None:
    """Entry point for ``python -m agent_db.stress`` and the console script."""
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - operator action
        sys.exit(130)


__all__ = ["app", "main"]
