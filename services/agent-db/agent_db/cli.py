#!/usr/bin/env python3
"""Centralized CLI for seed management, tenant registration, and e2e testing."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import click
import requests

from agent_db.core import PROJECT_ROOT, SCENARIOS_DIR, DATA_SERVICE_URL
import agent_db.core as _core


# ============================================================================
# Helpers
# ============================================================================


def run(
    cmd: list[str], cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run command and return result."""
    return subprocess.run(
        cmd, cwd=cwd or PROJECT_ROOT, capture_output=True, text=True, env=env
    )


def admin_headers() -> dict:
    """Build auth headers for data-service admin API.

    Requires ADMIN_TOKEN from env (or --admin-token option).
    Prints a loud warning if token is missing to avoid silent 401s.
    """
    token = _core.ADMIN_TOKEN
    if not token:
        click.secho(
            "  ❌ ADMIN_TOKEN not set — admin API calls will get 401.\n"
            "     Set it:  export ADMIN_TOKEN=secret\n"
            "     Or pass:  --admin-token secret\n"
            "     (значение должно совпадать с ADMIN_TOKEN в .env / data-service)",
            fg="red",
            bold=True,
            err=True,
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_scenario_config(scenario: str) -> dict:
    """Load full config.json for a scenario."""
    config_path = SCENARIOS_DIR / scenario / "config.json"
    if not config_path.exists():
        click.echo(f"❌ Config not found: {config_path}", err=True)
        sys.exit(1)
    return json.loads(config_path.read_text())


def get_scenario_names() -> list[str]:
    """Get all scenario directory names."""
    return sorted(
        [
            d.name
            for d in SCENARIOS_DIR.iterdir()
            if d.is_dir() and (d / "config.json").exists()
        ]
    )


# ============================================================================
# Commands
# ============================================================================


@click.group()
@click.option(
    "--admin-token",
    "--token",
    envvar="ADMIN_TOKEN",
    help="Bearer token for data-service admin API (или export ADMIN_TOKEN=...)",
)
@click.pass_context
def cli(ctx, admin_token: str):
    """agent-db: Centralized DB/seed/tenant/e2e management."""
    if admin_token:
        _core.ADMIN_TOKEN = admin_token
    ctx.ensure_object(dict)


# ---- Materialize ----


@cli.command()
@click.argument("scenario")
@click.option("--force", is_flag=True, help="Remove existing DB first")
def materialize(scenario: str, force: bool):
    """Materialize a scenario database (config.json + seed.json → SQLite)."""
    config = get_scenario_config(scenario)
    driver = config.get("data_source", {}).get("driver")
    dsn = config.get("data_source", {}).get("dsn")

    if driver != "sqlite":
        click.echo(f"⚠️  Only SQLite supported for materialize (got {driver})", err=True)
        sys.exit(1)

    db_path = PROJECT_ROOT / dsn.lstrip("/") if dsn.startswith("services/") else PROJECT_ROOT / dsn
    if force and db_path.exists():
        click.echo(f"🗑️  Removing existing: {db_path}")
        db_path.unlink(missing_ok=True)

    # Materialize via Python seedgen (scenario dir holds config.json + seed.json)
    from agent_db.seedgen import materialize as py_materialize

    click.echo(f"🔨 Materializing {scenario} → {db_path}")
    try:
        py_materialize(
            scenario_dir=str(SCENARIOS_DIR / scenario),
            output_dsn=str(db_path),
            force=force,
        )
    except Exception as exc:
        click.echo(f"❌ Failed:\n{exc}", err=True)
        sys.exit(1)

    # Run bootstrap script if it exists (e.g. for 'shop' scenario)
    bootstrap_script = SCENARIOS_DIR / scenario / "bootstrap.sh"
    if bootstrap_script.exists():
        click.echo(f"🚀 Running bootstrap script for {scenario}...")
        click.echo(f"DEBUG: bootstrap_script path: {bootstrap_script}")
        bootstrap_result = run(
            ["bash", str(bootstrap_script)],
            cwd=SCENARIOS_DIR / scenario,
            env={
                **dict(subprocess.os.environ),
                "SHOP_DB": str(db_path),
                "DATA_SERVICE_DIR": str(PROJECT_ROOT / "services" / "data-service"),
            },
        )
        if bootstrap_result.returncode != 0:
            click.echo(f"❌ Bootstrap failed:\n{bootstrap_result.stderr}", err=True)
            sys.exit(1)

    click.echo("✅ Done")


@cli.command()
@click.option("--all", "all_scenarios", is_flag=True, help="Materialize all scenarios")
@click.option("--force", is_flag=True, help="Remove existing DBs first")
def materialize_all(all_scenarios: bool, force: bool):
    """Materialize all scenarios."""
    scenarios = get_scenario_names()
    for s in scenarios:
        click.echo(f"\n--- {s} ---")
        ctx = click.get_current_context()
        ctx.invoke(materialize, scenario=s, force=force)


# ---- Serve --


@cli.command()
@click.argument("scenario")
@click.option("--port", default=8084, help="Port for data-service")
def serve(scenario: str, port: int):
    """Run data-service for a scenario (foreground)."""
    config_path = SCENARIOS_DIR / scenario / "config.json"

    click.echo(f"🚀 Serving {scenario} on :{port}")
    click.echo(f"   Config: {config_path}")

    # Build data-service first
    result = run(["go", "build", "./cmd/server/"], cwd=PROJECT_ROOT / "data-service")
    if result.returncode != 0:
        click.echo("❌ Build failed:", err=True)
        click.echo(result.stderr)
        sys.exit(1)

    # Run with config
    os.execvpe(
        str(PROJECT_ROOT / "data-service" / "bin" / "data-service"),
        ["data-service", "--config", str(config_path)],
        {
            **os.environ,
            "PORT": str(port),
        },
    )


# ---- Test --


@cli.command()
@click.option("--tenants", default="default,shop", help="Comma-separated tenant IDs")
@click.option("--skip-materialize", is_flag=True, help="Skip DB materialization")
@click.option("--skip-register", is_flag=True, help="Skip tenant registration")
def test(tenants: str, skip_materialize: bool, skip_register: bool):
    """Run test suite: isolation + dynamic-tools."""
    tenant_list = [t.strip() for t in tenants.split(",")]

    if not skip_materialize:
        click.echo("\n=== MATERIALIZE ===")
        for t in tenant_list:
            ctx = click.get_current_context()
            ctx.invoke(materialize, scenario=t, force=True)

    if not skip_register:
        click.echo("\n=== REGISTER TENANTS ===")
        for t in tenant_list:
            ctx = click.get_current_context()
            ctx.invoke(register, tenant_id=t, scenario=t)

    click.echo("\n=== ISOLATION TESTS ===")
    _run_isolation_tests(tenant_list)

    click.echo("\n=== DYNAMIC TOOLS TESTS ===")
    _run_dynamic_tools_tests(tenant_list)

    click.echo("\n🎉 ALL TESTS PASSED")


def _run_isolation_tests(tenants: list[str]):
    """Test tenant isolation via data-service."""
    for tid in tenants:
        headers = {"X-Tenant-ID": tid}
        r = requests.get(f"{DATA_SERVICE_URL}/students", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            click.echo(f"  ✅ {tid}: /students ({len(data)} items)")
        else:
            click.echo(f"  ⚠️  {tid}: /students not found ({r.status_code})")


def _run_dynamic_tools_tests(tenants: list[str]):
    """Test MCP dynamic tools via mcp-gateway."""
    for tid in tenants:
        headers = {"X-Tenant-ID": tid}
        r = requests.get(
            "http://127.0.0.1:8083/mcp/manifest", headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            tools = data.get("tools", [])
            click.echo(f"  ✅ {tid}: manifest ({len(tools)} tools)")
        else:
            click.echo(f"  ❌ {tid}: manifest failed ({r.status_code})")


# ---- Drop --


@cli.command()
@click.argument("scenario")
def drop(scenario: str):
    """Drop a materialized database."""
    config = get_scenario_config(scenario)
    driver = config.get("data_source", {}).get("driver")
    dsn = config.get("data_source", {}).get("dsn")

    if driver == "sqlite":
        db_path = PROJECT_ROOT / dsn.lstrip("/") if dsn.startswith("services/") else PROJECT_ROOT / dsn
        if db_path.exists():
            click.echo(f"🗑️  Removing: {db_path}")
            db_path.unlink(missing_ok=True)
            for ext in ["-wal", "-shm"]:
                (db_path.with_suffix(db_path.suffix + ext)).unlink(missing_ok=True)
            click.echo("✅ SQLite database dropped")
        else:
            click.echo("ℹ️  No database file found")
    else:
        click.echo("⚠️  PostgreSQL: drop manually (safety)")
        sys.exit(1)


# ---- Register Tenants ----


@cli.command()
@click.argument("tenant_id")
@click.argument("scenario")
def register(tenant_id: str, scenario: str):
    """Register a tenant in data-service with scenario config."""
    config = get_scenario_config(scenario)

    # Ensure absolute DSN path for SQLite
    if config.get("data_source", {}).get("driver") == "sqlite":
        dsn = config["data_source"]["dsn"]
        if not Path(dsn).is_absolute():
            config["data_source"]["dsn"] = str(PROJECT_ROOT / dsn)

    payload = {
        "id": tenant_id,
        "config": config,
        "config_path": str(SCENARIOS_DIR / scenario / "config.json"),
    }

    click.echo(f"🔑 Registering tenant '{tenant_id}' from scenario '{scenario}'...")
    resp = requests.post(
        f"{DATA_SERVICE_URL}/admin/tenants", json=payload, headers=admin_headers()
    )

    if resp.status_code == 409:
        click.echo("⚠️  Tenant exists, recreating...")
        requests.delete(
            f"{DATA_SERVICE_URL}/admin/tenants/{tenant_id}", headers=admin_headers()
        )
        resp = requests.post(
            f"{DATA_SERVICE_URL}/admin/tenants", json=payload, headers=admin_headers()
        )

    if resp.status_code not in (200, 201):
        click.echo(f"❌ Failed ({resp.status_code}): {resp.text}", err=True)
        sys.exit(1)

    click.echo(f"✅ Tenant '{tenant_id}' registered")


@cli.command()
@click.option(
    "--tenant", multiple=True, help="Tenant IDs to register (default: all scenarios)"
)
def register_all(tenant: tuple[str]):
    """Register all scenarios as tenants (tenant_id = scenario name)."""
    scenarios = list(tenant) if tenant else get_scenario_names()
    for s in scenarios:
        click.echo(f"\n--- {s} ---")
        ctx = click.get_current_context()
        ctx.invoke(register, tenant_id=s, scenario=s)


# ---- E2E Test ----


@cli.command()
@click.option("--tenants", default="default,shop", help="Comma-separated tenant IDs")
@click.option("--skip-materialize", is_flag=True, help="Skip DB materialization")
@click.option("--skip-register", is_flag=True, help="Skip tenant registration")
def e2e(tenants: str, skip_materialize: bool, skip_register: bool):
    """Run full E2E pipeline: materialize → register → test web proxy + SSE chat."""
    tenant_list = [t.strip() for t in tenants.split(",")]

    if not skip_materialize:
        click.echo("\n=== MATERIALIZE ===")
        # Map tenant IDs to actual scenario folder names
        scenario_map = {
            "default": "sqlite-testseed",
            "shop": "shop",
        }
        for tid in tenant_list:
            scenario = scenario_map.get(tid, tid)
            ctx = click.get_current_context()
            ctx.invoke(materialize, scenario=scenario, force=True)

    if not skip_register:
        click.echo("\n=== REGISTER TENANTS ===")
        # Map tenant IDs to actual scenario folder names for registration
        scenario_map = {
            "default": "sqlite-testseed",
            "shop": "shop",
        }
        for tid in tenant_list:
            scenario = scenario_map.get(tid, tid)
            ctx = click.get_current_context()
            ctx.invoke(register, tenant_id=tid, scenario=scenario)

    click.echo("\n=== WEB PROXY TESTS ===")
    _run_web_proxy_tests(tenant_list)

    click.echo("\n=== SSE CHAT TESTS ===")
    _run_sse_chat_tests(tenant_list)

    click.echo("\n🎉 ALL E2E TESTS PASSED")


def _run_web_proxy_tests(tenants: list[str]):
    """Test web proxy endpoints for each tenant. Fail immediately if critical data is missing."""
    base = "http://127.0.0.1:8080"

    for tid in tenants:
        headers = {"X-Tenant-ID": tid}

        # 1. Manifest check
        try:
            r = requests.get(f"{base}/api/manifest", headers=headers, timeout=5)
            r.raise_for_status()
            manifest = r.json()
            entities_count = len(manifest.get("entities", []))
            click.echo(f"  ✅ {tid}: manifest ({entities_count} entities)")
            if entities_count == 0:
                click.echo(f"  ❌ {tid}: manifest is empty!", err=True)
                sys.exit(1)
        except Exception as e:
            click.echo(f"  ❌ {tid}: manifest request failed: {e}", err=True)
            sys.exit(1)

        # 2. Data check - use correct entity for tenant
        entity = "products" if tid == "shop" else "students"
        try:
            r = requests.get(f"{base}/api/data/{entity}", headers=headers, timeout=5)
            if r.status_code != 200:
                click.echo(
                    f"  ❌ {tid}: /api/data/{entity} returned {r.status_code} - {r.text[:200]}",
                    err=True,
                )
                sys.exit(1)

            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                click.echo(
                    f"  ❌ {tid}: /api/data/{entity} returned empty or invalid data: {data}",
                    err=True,
                )
                sys.exit(1)
            click.echo(f"  ✅ {tid}: /api/data/{entity} ({len(data)} items)")
        except Exception as e:
            click.echo(f"  ❌ {tid}: data request failed: {e}", err=True)
            sys.exit(1)


def _run_sse_chat_tests(tenants: list[str]):
    """Test SSE chat endpoint for each tenant.

    SSE format from server.py is:
        data: {\"type\": \"<event_type>\", ...}\\n\

    Event types (inside JSON): token, tool_call, tool_result, final, error, done.
    No `event:` lines — type is inside the JSON payload.

    IMPORTANT: LLM is inherently non-deterministic. This test is DIAGNOSTIC —
    it shows what happened, and flags problems (no tool call, error, empty
    response) as warnings, not hard failures. The goal is to help you judge
    whether the pipeline works, not to give a binary pass/fail.
    """
    base = "http://127.0.0.1:8080"

    prompts = {
        "default": "Используй доступные инструменты, чтобы вывести список всех студентов.",
        "shop": "Используй доступные инструменты, чтобы показать все товары в магазине.",
    }

    all_ok = True

    for tid in tenants:
        prompt = prompts.get(tid, "Что есть в базе?")
        click.echo("")
        click.secho(
            f"  ┌─ {tid} ─────────────────────────────────────────────",
            fg="cyan",
            bold=True,
        )
        click.echo(f"  │ 📝 Prompt: {prompt}")

        session_id = f"e2e-{tid}-{uuid.uuid4().hex[:8]}"
        try:
            r = requests.post(
                f"{base}/api/chat",
                json={"message": prompt, "session_id": session_id, "tenant_id": tid},
                headers={
                    "X-Tenant-ID": tid,
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (compatible; E2ETest)",
                },
                timeout=60,
                stream=True,
            )
            if r.status_code != 200:
                click.secho(f"  │ ❌ HTTP {r.status_code}: {r.text[:200]}", fg="red")
                all_ok = False
                continue

            # ── Parse SSE stream ──────────────────────────────
            # Format: data: {"type": "<event>", ...}\n\n
            # (no `event:` lines — type is inside the JSON)
            tool_called = False
            tool_calls_list: list[dict] = []
            tool_results: list[str] = []
            full_response = ""
            errors: list[str] = []
            status_messages: list[str] = []

            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8", errors="replace")

                if line.startswith("data: "):
                    payload_str = line[6:]
                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue

                    ev_type = payload.get("type", "")

                    if ev_type == "status":
                        status_messages.append(
                            payload.get("message") or payload.get("phase", "")
                        )
                    elif ev_type == "tool_call":
                        tool_called = True
                        tool_calls_list.append(payload)
                        name = payload.get("name", "?")
                        args = payload.get("arguments", {})
                        click.echo(
                            f"  │ 🛠️  {name}({json.dumps(args, ensure_ascii=False)})"
                        )
                    elif ev_type == "tool_result":
                        name = payload.get("name", "?")
                        tool_results.append(name)
                        result = payload.get("result")
                        if result:
                            result_preview = str(result)[:200]
                            click.echo(f"  │    → {name}: {result_preview}")
                        else:
                            click.echo(f"  │    → {name}: (no result data)")
                    elif ev_type == "token":
                        full_response += payload.get("text", "")
                    elif ev_type == "error":
                        err_text = payload.get("text", payload_str[:300])
                        errors.append(str(err_text))
                        click.secho(f"  │ ⚡ ERROR: {err_text}", fg="red")
                    elif ev_type == "final":
                        full_response += payload.get("text", "")
                    elif ev_type == "done":
                        break

            # ── Diagnostic summary ───────────────────────────
            has_errors = len(errors) > 0
            has_response = bool(full_response.strip())
            click.echo("  ├─ 📊 Summary ────────────────────────────────")
            click.echo(f"  │  Tool calls:  {len(tool_calls_list)}")
            click.echo(f"  │  Tool results: {len(tool_results)}")
            click.echo(f"  │  Response chars: {len(full_response)}")
            click.echo(f"  │  Errors: {len(errors)}")

            # Show what went wrong (if anything)
            if has_errors:
                click.secho("  │  ⚠️  Errors during SSE stream:", fg="yellow")
                for e in errors[:3]:
                    click.secho(f"  │     {e[:200]}", fg="yellow")

            if not tool_called and not has_response:
                # Both missing — likely fatal (LLM didn't respond at all)
                click.secho(
                    "  │  ⛔ LLM did not call any tool AND produced no response.",
                    fg="red",
                    bold=True,
                )
                click.secho(
                    "  │     This is likely a model or configuration issue — check API logs.",
                    fg="red",
                )
                if status_messages:
                    click.echo(f"  │  Status messages: {status_messages}")
                all_ok = False
            elif tool_called and has_response:
                click.secho(
                    "  │  ✅ Tool called + response received — pipeline OK.", fg="green"
                )
            elif tool_called and not has_response:
                click.secho(
                    "  │  ⚠️  Tool called, but NO final text response.", fg="yellow"
                )
                click.secho(
                    "  │     LLM may have exited before streaming the answer.",
                    fg="yellow",
                )
                click.secho(
                    "  │     Check the tool_result content above — the data probably arrived.",
                    fg="yellow",
                )
            else:  # not tool_called but has_response
                click.secho(
                    "  │  ⚠️  LLM answered WITHOUT calling any tool.", fg="yellow"
                )
                click.secho(f"  │     Prompt was: {prompt}", fg="yellow")
                click.secho(
                    "  │     LLM may be ignoring tool-use instructions — check system prompt and model capabilities.",
                    fg="yellow",
                )
                snippet = (
                    (full_response[:250] + "...")
                    if len(full_response) > 250
                    else full_response
                )
                click.echo(f"  │  LLM raw answer: {snippet}")

            # Show response preview
            if has_response:
                snippet = (
                    (full_response[:400] + "...")
                    if len(full_response) > 400
                    else full_response
                )
                click.echo(f"  │  💬 Response: {snippet}")

            click.secho(
                "  └──────────────────────────────────────────────────", fg="cyan"
            )

        except requests.exceptions.Timeout:
            click.secho(
                "  │ ⛔ Request timed out after 60s — data-service or LLM unresponsive.",
                fg="red",
            )
            all_ok = False
        except requests.exceptions.ConnectionError as e:
            click.secho(f"  │ ⛔ Connection refused: {e}", fg="red")
            all_ok = False
        except Exception as e:
            click.secho(f"  │ ⛔ Unexpected error: {e}", fg="red")
            all_ok = False

    if all_ok:
        click.secho(
            "\n✅ All tenants completed SSE chat (pipeline functional)",
            fg="green",
            bold=True,
        )
    else:
        click.secho(
            "\n⚠️  Some tenants had issues — check the diagnostics above.",
            fg="yellow",
            bold=True,
        )


# ---- List / Status ----


@cli.command()
def scenarios():
    """List all available scenarios."""
    for s in get_scenario_names():
        config = get_scenario_config(s)
        driver = config.get("data_source", {}).get("driver", "?")
        entities = len(config.get("entities", []))
        endpoints = len(config.get("endpoints", []))
        click.echo(
            f"  {s:20} driver={driver:6} entities={entities:2} endpoints={endpoints:2}"
        )


@cli.command()
def tenants():
    """List registered tenants in data-service."""
    r = requests.get(f"{DATA_SERVICE_URL}/admin/tenants", headers=admin_headers())
    if r.status_code != 200:
        click.echo(f"❌ Failed: {r.text}", err=True)
        return

    for t in r.json().get("tenants", []):
        click.echo(
            f"  {t['id']:20} driver={t['driver']:6} entities={t['entities']:2} healthy={t['healthy']}"
        )


def _cleanup_dbs(*db_paths: Path):
    """Remove temporary DB files."""
    for db_path in db_paths:
        if db_path.exists():
            db_path.unlink(missing_ok=True)
        for ext in ["-wal", "-shm"]:
            p = db_path.with_name(db_path.name + ext)
            if p.exists():
                p.unlink(missing_ok=True)


@cli.group()
def bench():
    """Benchmark commands: report and run."""


@bench.command()
@click.option("--backlog-dir", default=None, help="Backlog directory (default: auto-detect)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--limit", default=0, type=int, help="Only analyze last N turns (0 = all)")
def report(backlog_dir: str | None, as_json: bool, limit: int):
    """Show benchmark report from backlog data (token usage, costs, errors, tool calls)."""
    from agent_db.bench.reader import find_backlog_dir
    from agent_db.bench.parser import parse_turns
    from agent_db.bench.reporter import format_report
    from agent_db.bench.models import BenchReport

    bdir = find_backlog_dir(backlog_dir)

    if not bdir.exists():
        click.echo(
            click.style(f"❌ Backlog directory not found: {bdir}", fg="red"),
            err=True,
        )
        sys.exit(1)

    files = sorted(bdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        click.secho("  ℹ️  No backlog files found.", fg="yellow")
        sys.exit(0)

    turns = parse_turns(files)
    if not turns:
        click.secho("  ℹ️  No turn_end records found.", fg="yellow")
        sys.exit(0)

    if limit > 0 and len(turns) > limit:
        turns = turns[-limit:]

    passed = sum(1 for t in turns if t.outcome == "final" and not t.errors)
    failed = sum(1 for t in turns if t.outcome == "final" and t.errors)
    errored = sum(1 for t in turns if t.outcome != "final")
    report = BenchReport(
        turns=turns, total_questions=len(turns),
        passed=passed, failed=failed, errored=errored,
        total_cost=sum(t.total_cost for t in turns),
        total_duration_ms=sum(t.duration_ms for t in turns),
        total_tokens=sum(t.total_tokens for t in turns),
    )

    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))
    else:
        click.echo("")
        click.echo(format_report(report))


# ---- Benchmark: run ----


@bench.command()
@click.option("--questions", default=None, help="Path to JSON file with questions array")
@click.option("--agent", default=None, help="Agent name")
@click.option("--api-url", default="http://127.0.0.1:8081", help="API service URL")
@click.option("--tenant", default=None, help="Tenant ID")
@click.option("--scenario", default="auto-shop", help="Scenario (scripted mode only)")
@click.option("--scripted", is_flag=True, help="Use ScriptedLLMProvider (no real LLM)")
@click.option("--backlog-dir", default=None, help="Backlog dir (default: auto-detect)")
@click.pass_context
def bench_run(
    ctx: click.Context,
    questions: str | None,
    agent: str | None,
    api_url: str,
    tenant: str | None,
    scenario: str,
    scripted: bool,
    backlog_dir: str | None,
):
    """Run benchmark questions through the agent and show report."""
    from agent_db.bench.runner import run_bench
    from agent_db.bench.models import DEFAULT_BENCH_QUESTIONS
    from agent_db.bench.reader import find_backlog_dir
    from agent_db.bench.parser import parse_turns
    from agent_db.bench.reporter import format_report
    from agent_db.bench.models import BenchReport

    if scripted:
        click.echo(click.style("\n\u2550\u2550\u2550 Setting up scripted benchmark \u2550\u2550\u2550\n", fg="cyan", bold=True))
        click.secho("  Scripted mode not yet migrated. Run against a running api-service.", fg="yellow")
        sys.exit(1)

    if not agent:
        click.secho("\u274c --agent is required", fg="red", err=True)
        sys.exit(1)
    if not tenant:
        click.secho("\u274c --tenant is required", fg="red", err=True)
        sys.exit(1)

    # Load questions
    if questions:
        try:
            qdata = json.loads(Path(questions).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            click.secho(f"\u274c Failed to load questions: {e}", fg="red", err=True)
            sys.exit(1)
        bench_questions = qdata.get("questions", [])
        if not bench_questions:
            click.secho("\u274c Empty questions array", fg="red", err=True)
            sys.exit(1)
    else:
        bench_questions = DEFAULT_BENCH_QUESTIONS

    click.echo(click.style(f"\n\u2550\u2550\u2550 Running {len(bench_questions)} questions \u2550\u2550\u2550\n", fg="cyan", bold=True))
    click.echo(f"  API: {api_url}")
    click.echo(f"  Agent: {agent}")
    click.echo(f"  Tenant: {tenant}")
    click.echo()

    # Run via bench.runner
    admin_token = ctx.parent.params.get("admin_token", "") or os.environ.get("ADMIN_TOKEN", "")
    results = run_bench(
        api_url=api_url,
        agent_name=agent,
        questions=bench_questions,
        tenant_id=tenant,
        admin_token=admin_token,
    )

    # Print per-question
    errors = 0
    for i, result in enumerate(results, 1):
        q = bench_questions[i - 1]
        click.echo(f"  [{i}/{len(results)}] {click.style(q[:80], bold=True)}")
        if "error" in result:
            click.secho(f"    \u26d4 {result['error']}", fg="red")
            errors += 1
            continue
        n_tools = len(result.get("tool_calls", []))
        n_errors = len(result.get("errors", []))
        final_len = len(result.get("final_text", ""))
        icon = "\u2705" if not n_errors else "\u26a0\ufe0f"
        click.echo(f"    {icon} tools={n_tools} errors={n_errors} response={final_len}chars")
        if result.get("errors"):
            for err in result["errors"]:
                click.secho(f"      \u274c {err[:200]}", fg="red")
        if final_len > 0:
            click.echo(f"      \U0001f4ac {result['final_text'][:300]}")

    click.echo()
    click.echo(click.style("\u2550\u2550\u2550 Done \u2550\u2550\u2550", fg="cyan", bold=True))
    click.echo(f"  Completed: {len(results)}/{len(bench_questions)}  |  Errors: {errors}")
    click.echo()

    # Report from backlog
    report_dir = find_backlog_dir(backlog_dir)
    if report_dir.exists():
        files = sorted(report_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        turns = parse_turns(files)
        if turns:
            passed = sum(1 for t in turns if t.outcome == "final" and not t.errors)
            failed = sum(1 for t in turns if t.outcome == "final" and t.errors)
            errored = sum(1 for t in turns if t.outcome != "final")
            report = BenchReport(
                turns=turns, total_questions=len(turns),
                passed=passed, failed=failed, errored=errored,
                total_cost=sum(t.total_cost for t in turns),
                total_duration_ms=sum(t.duration_ms for t in turns),
                total_tokens=sum(t.total_tokens for t in turns),
            )
            click.echo(format_report(report))
            click.echo()
            loops = [(i, t) for i, t in enumerate(turns, 1) if t.loop_warnings]
            if loops:
                click.echo(click.style("  \u26a0\ufe0f  Loop warnings:", fg="yellow"))
                for idx, t in loops:
                    for w in t.loop_warnings:
                        click.echo(f"    Turn {idx}: {w}")
        else:
            click.secho(f"  \u2139\ufe0f  No turns in {report_dir}", fg="yellow")
        click.echo(f"  \U0001f517 Backlog: {report_dir}")
    else:
        click.secho(f"  \u2139\ufe0f  Backlog dir not found: {report_dir}", fg="yellow")


if __name__ == "__main__":
    cli()
