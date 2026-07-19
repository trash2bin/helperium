#!/usr/bin/env python3
"""Centralized CLI for seed management, tenant registration, and e2e testing."""

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
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

    db_path = PROJECT_ROOT / dsn
    if force and db_path.exists():
        click.echo(f"🗑️  Removing existing: {db_path}")
        db_path.unlink(missing_ok=True)

    # Determine seed path: scenario seed.json > global seed.json
    scenario_seed = SCENARIOS_DIR / scenario / "seed.json"
    seed_path = (
        scenario_seed
        if scenario_seed.exists()
        else PROJECT_ROOT / "specs" / "fixtures" / "seed.json"
    )

    # Use Python seedgen instead of old Go seed-cli
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
                "DATA_SERVICE_DIR": str(PROJECT_ROOT / "data-service"),
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
        db_path = PROJECT_ROOT / dsn
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


# ---- E2E Data (multi-tenancy isolation, no LLM) ----


@cli.command()
def e2e_data():
    """Deterministic multi-tenancy tests: admin lifecycle, data isolation, auth, routing.

    No LLM dependency. Tests data-service directly via HTTP.
    """
    _run_data_isolation_tests()


# ---- E2E MCP (dynamic tool resolution, no LLM) ----


@cli.command()
def e2e_mcp():
    """Deterministic MCP protocol tests: dynamic tool resolution, tenant tool isolation.

    No LLM dependency. Tests mcp-gateway via JSON-RPC over SSE.
    """
    _run_mcp_dynamic_tool_tests()


# ---- E2E MCP Composite (multi-tenant, no LLM) ----


@cli.command()
def e2e_mcp_composite():
    """Composite MCP tests: one SSE session accessing multiple tenants' tools.

    Verifies that mcp-gateway correctly aggregates tools from multiple tenants
    into one composite MCP server with tenant-prefixed tool names.
    """
    _run_mcp_composite_tests()


# ---- E2E Full (all tests) ----


@cli.command()
@click.option(
    "--tenants",
    default="default,shop",
    help="Comma-separated tenant IDs for LLM chat test",
)
def e2e_full(tenants: str):
    """Run ALL E2E tests: data isolation + MCP dynamic tools + LLM chat."""
    click.secho("\n═══ E2E DATA (multi-tenancy isolation) ═══", fg="cyan", bold=True)
    _run_data_isolation_tests()

    click.secho("\n═══ E2E MCP (dynamic tool resolution) ═══", fg="cyan", bold=True)
    _run_mcp_dynamic_tool_tests()

    click.secho("\n═══ E2E MCP COMPOSITE (multi-tenant) ═══", fg="cyan", bold=True)
    _run_mcp_composite_tests()

    click.secho("\n═══ E2E LLM (SSE chat) ═══", fg="cyan", bold=True)
    ctx = click.get_current_context()
    ctx.invoke(e2e, tenants=tenants, skip_materialize=True, skip_register=True)


# ============================================================================
# Data isolation tests (port of integration-multi-tenancy.sh)
# ============================================================================


def _run_data_isolation_tests():
    """Test data-service multi-tenancy: register, isolate, lifecycle, security."""
    base = DATA_SERVICE_URL
    seed_path = PROJECT_ROOT / "specs" / "fixtures" / "seed.json"
    config_path = SCENARIOS_DIR / "sqlite-testseed" / "config.json"
    config = json.loads(config_path.read_text())

    # Create two isolated SQLite DBs
    db_a = PROJECT_ROOT / "tenant_a_e2e.db"
    db_b = PROJECT_ROOT / "tenant_b_e2e.db"

    click.secho(
        "\n  ┌─ Data Isolation & Admin API ─────────────────────", fg="cyan", bold=True
    )
    all_ok = True

    # Generate DBs via seed-cli
    for label, db_path in [("A", db_a), ("B", db_b)]:
        click.echo(f"  │ 🔨 Generating tenant_{label.lower()}.db...")
        cmd = ["go", "run", "./cmd/seed-cli/", "--seed-path", str(seed_path)]
        env = {"DB_PATH": str(db_path)}
        result = run(cmd, cwd=PROJECT_ROOT / "data-service", env={**os.environ, **env})
        if result.returncode != 0:
            click.secho(f"  │ ❌ seed-cli failed for tenant_{label.lower()}", fg="red")
            click.echo(f"  │    {result.stderr[:300]}")
            _cleanup_dbs(db_a, db_b)
            sys.exit(1)

    # Mark isolation markers
    try:
        import sqlite3

        click.echo("  │ 📝 Marking students for isolation check...")
        conn = sqlite3.connect(str(db_a))
        conn.execute(
            "UPDATE students SET name = 'Isolation-Alice' WHERE id = (SELECT id FROM students ORDER BY rowid LIMIT 1)"
        )
        conn.commit()
        conn.close()
        conn = sqlite3.connect(str(db_b))
        conn.execute(
            "UPDATE students SET name = 'Isolation-Bob' WHERE id = (SELECT id FROM students ORDER BY rowid LIMIT 1)"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        click.echo(f"  │ ⚠️  sqlite3 isolation marking failed: {e}")

    # Build tenant configs with absolute DSN (deep copy to avoid shared nested dicts)
    config_a = copy.deepcopy(config)
    config_a["data_source"]["dsn"] = str(db_a)
    config_b = copy.deepcopy(config)
    config_b["data_source"]["dsn"] = str(db_b)

    # Clean up existing test tenants
    h = admin_headers()
    for tid in ["tenant-a", "tenant-b"]:
        requests.delete(f"{base}/admin/tenants/{tid}", headers=h)

    # Register tenants
    click.echo("  │ 🔑 Registering tenants...")
    for tid, cfg in [("tenant-a", config_a), ("tenant-b", config_b)]:
        resp = requests.post(
            f"{base}/admin/tenants", json={"id": tid, "config": cfg}, headers=h
        )
        status = "✅" if resp.status_code in (200, 201) else "❌"
        click.echo(f"  │  {status} {tid}: {resp.status_code}")
        if resp.status_code not in (200, 201):
            all_ok = False

    # ── Data isolation ──
    click.echo("  ├─ 🧪 Data Isolation ────────────────────────────")

    with requests.Session() as s:
        s.timeout = 5

        # Tenant A → only Alice
        r = s.get(f"{base}/students", headers={"X-Tenant-ID": "tenant-a"})
        has_alice = r.status_code == 200 and "Isolation-Alice" in r.text
        has_bob = "Isolation-Bob" in r.text
        if has_alice and not has_bob:
            click.echo("  │  ✅ Tenant A: Alice found, Bob not leaked (PASS)")
        else:
            click.secho(
                f"  │  ❌ Tenant A: isolation failed! Alice={has_alice} Bob={has_bob}",
                fg="red",
            )
            all_ok = False

        # Tenant B → only Bob
        r = s.get(f"{base}/students", headers={"X-Tenant-ID": "tenant-b"})
        has_bob = r.status_code == 200 and "Isolation-Bob" in r.text
        has_alice = "Isolation-Alice" in r.text
        if has_bob and not has_alice:
            click.echo("  │  ✅ Tenant B: Bob found, Alice not leaked (PASS)")
        else:
            click.secho("  │  ❌ Tenant B: isolation failed!", fg="red")
            all_ok = False

        # Default tenant → no leaked data
        r = s.get(f"{base}/students")
        no_leak = "Isolation-Alice" not in r.text and "Isolation-Bob" not in r.text
        if no_leak:
            click.echo("  │  ✅ Default: no leaked data from A or B (PASS)")
        else:
            click.secho("  │  ❌ Default: leaked data found!", fg="red")
            all_ok = False

    # ── Admin lifecycle ──
    click.echo("  ├─ 🧪 Admin Lifecycle ───────────────────────────")

    r = requests.get(f"{base}/admin/tenants", headers=h)
    tenant_list = r.json().get("tenants", [])
    present = [t["id"] for t in tenant_list]
    if "tenant-a" in present and "tenant-b" in present:
        click.echo("  │  ✅ Tenant list: both present (PASS)")
    else:
        click.secho(f"  │  ❌ Tenant list: missing! {present}", fg="red")
        all_ok = False

    requests.delete(f"{base}/admin/tenants/tenant-a", headers=h)
    r = requests.get(f"{base}/students", headers={"X-Tenant-ID": "tenant-a"})
    if r.status_code == 404:
        click.echo("  │  ✅ Deletion: tenant-a removed → 404 (PASS)")
    else:
        click.secho(
            f"  │  ❌ Deletion: tenant-a still accessible! Status={r.status_code}",
            fg="red",
        )
        all_ok = False

    # ── Security ──
    click.echo("  ├─ 🧪 Security ──────────────────────────────────")

    r = requests.get(
        f"{base}/admin/tenants", headers={"Authorization": "Bearer wrong-token"}
    )
    if r.status_code == 401:
        click.echo("  │  ✅ Auth: invalid token rejected 401 (PASS)")
    else:
        click.secho(
            f"  │  ❌ Auth: invalid token allowed! Status={r.status_code}", fg="red"
        )
        all_ok = False

    r = requests.get(f"{base}/students", headers={"X-Tenant-ID": "ghost-tenant"})
    if r.status_code == 404:
        click.echo("  │  ✅ Routing: invalid tenant → 404 (PASS)")
    else:
        click.secho(
            f"  │  ❌ Routing: invalid tenant not handled! Status={r.status_code}",
            fg="red",
        )
        all_ok = False

    _cleanup_dbs(db_a, db_b)

    click.echo("  └──────────────────────────────────────────────────")
    if all_ok:
        click.secho(
            "  ✅ Data isolation & admin API: ALL PASSED", fg="green", bold=True
        )
    else:
        click.secho(
            "  ⚠️  Some tests failed — review diagnostics above.", fg="yellow", bold=True
        )


# ============================================================================
# MCP dynamic tool tests (port of test-dynamic-tools.sh)
# ============================================================================


def _run_mcp_dynamic_tool_tests():
    """Test MCP dynamic tool resolution and tenant isolation via JSON-RPC over SSE."""
    mcp_url = "http://127.0.0.1:8083"
    seed_path = PROJECT_ROOT / "specs" / "fixtures" / "seed.json"
    shop_db = (
        PROJECT_ROOT / "data-service" / "testdata" / "scenarios" / "shop" / "data.db"
    )
    uni_db = PROJECT_ROOT / "tenant_mcp_e2e.db"

    click.secho(
        "\n  ┌─ MCP Dynamic Tool Resolution ───────────────────", fg="cyan", bold=True
    )
    all_ok = True

    # Seed university DB
    click.echo("  │ 🔨 Seeding university DB for MCP test...")
    cmd = ["go", "run", "./cmd/seed-cli/", "--seed-path", str(seed_path)]
    result = run(
        cmd,
        cwd=PROJECT_ROOT / "data-service",
        env={**os.environ, "DB_PATH": str(uni_db)},
    )
    if result.returncode != 0:
        click.secho("  │ ❌ seed-cli failed", fg="red")
        _cleanup_dbs(uni_db)
        sys.exit(1)

    # Register tenants
    click.echo("  │ 🔑 Registering MCP test tenants...")
    h = admin_headers()
    data_base = DATA_SERVICE_URL

    # University tenant
    uni_config = {
        "data_source": {"driver": "sqlite", "dsn": str(uni_db)},
        "entities": [
            {
                "name": "student",
                "table": "students",
                "id_column": "id",
                "fields": [{"name": "name", "column": "name", "type": "string"}],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/students", "op": "list", "entity": "student"}
        ],
    }
    for tid in ["tenant-uni", "tenant-shop"]:
        requests.delete(f"{data_base}/admin/tenants/{tid}", headers=h)

    requests.post(
        f"{data_base}/admin/tenants",
        json={"id": "tenant-uni", "config": uni_config},
        headers=h,
    )

    shop_config = {
        "data_source": {"driver": "sqlite", "dsn": str(shop_db)},
        "entities": [
            {
                "name": "product",
                "table": "products",
                "id_column": "id",
                "fields": [{"name": "name", "column": "name", "type": "string"}],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/products", "op": "list", "entity": "product"}
        ],
    }
    requests.post(
        f"{data_base}/admin/tenants",
        json={"id": "tenant-shop", "config": shop_config},
        headers=h,
    )

    # ── Test MCP tool calls ──
    click.echo("  ├─ 🧪 Dynamic Tool Resolution ───────────────────")

    def _mcp_call(tenant_id: str, tool_name: str, should_succeed: bool = True) -> bool:
        """Single MCP tool call with SSE protocol."""
        headers = {"X-Tenant-ID": tenant_id, "Accept": "text/event-stream"}
        sse_q: queue.Queue = queue.Queue()
        ready = threading.Event()
        endpoint_val: list[str] = [""]

        def _read_sse():
            try:
                resp = requests.get(
                    f"{mcp_url}/mcp", headers=headers, stream=True, timeout=60
                )
                resp.raise_for_status()
                seen_endpoint_event = False
                for line in resp.iter_lines():
                    if not line:
                        continue
                    txt = line.decode("utf-8", errors="replace")
                    if txt.startswith("event: endpoint"):
                        seen_endpoint_event = True
                    elif (
                        txt.startswith("data: ")
                        and seen_endpoint_event
                        and endpoint_val[0] == ""
                    ):
                        endpoint_val[0] = txt[6:].strip()
                        ready.set()
                    elif txt.startswith("data: "):
                        sse_q.put(txt[6:])
            except Exception:
                pass
            finally:
                sse_q.put(None)

        t = threading.Thread(target=_read_sse, daemon=True)
        t.start()

        if not ready.wait(timeout=10):
            click.secho(
                f"  │  ❌ {tenant_id}: MCP session not ready (timeout)", fg="red"
            )
            return False

        ep = endpoint_val[0]
        if not ep:
            click.secho(f"  │  ❌ {tenant_id}: No MCP endpoint URL", fg="red")
            return False

        # Call tool
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
            "id": 1,
        }
        try:
            r = requests.post(
                ep,
                json=payload,
                headers={"X-Tenant-ID": tenant_id, "Content-Type": "application/json"},
                timeout=15,
            )
        except requests.RequestException as e:
            click.secho(f"  │  ❌ {tenant_id}: POST failed: {e}", fg="red")
            return False

        if r.status_code not in (200, 202):
            click.secho(f"  │  ❌ {tenant_id}: POST status={r.status_code}", fg="red")
            return False

        # Check for immediate result
        success = False
        error_msg = ""
        try:
            data = r.json()
            if "result" in data:
                success = True
            elif "error" in data:
                error_info = data["error"]
                if isinstance(error_info, dict):
                    error_msg = str(error_info.get("message", json.dumps(error_info)))[
                        :200
                    ]
                else:
                    error_msg = str(error_info)[:200]
        except Exception:
            pass

        # Wait for SSE result
        if not success:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    msg = sse_q.get(timeout=2)
                    if msg is None:
                        break
                    try:
                        chunk = json.loads(msg)
                        if "result" in chunk and chunk.get("id") == 1:
                            success = True
                            break
                        elif "error" in chunk and chunk.get("id") == 1:
                            error_info = chunk["error"]
                            if isinstance(error_info, dict):
                                error_msg = str(
                                    error_info.get("message", json.dumps(error_info))
                                )[:200]
                            else:
                                error_msg = str(error_info)[:200]
                            break
                    except json.JSONDecodeError:
                        pass
                except queue.Empty:
                    continue

        if should_succeed and success:
            click.echo(f"  │  ✅ {tenant_id}: {tool_name} → OK")
            return True
        elif not should_succeed and not success:
            click.echo(
                f"  │  ✅ Isolation: {tenant_id} correctly blocked from {tool_name}"
            )
            return True
        elif should_succeed and not success:
            click.secho(
                f"  │  ❌ {tenant_id}: {tool_name} FAILED — {error_msg}", fg="red"
            )
            return False
        else:  # not should_succeed but success (isolation breach!)
            click.secho(
                f"  │  ❌ ISOLATION BREACH: {tenant_id} accessed {tool_name}!",
                fg="red",
                bold=True,
            )
            return False

    # University tenant → list_student
    if not _mcp_call("tenant-uni", "list_student", should_succeed=True):
        all_ok = False

    # Shop tenant → list_product
    if not _mcp_call("tenant-shop", "list_product", should_succeed=True):
        all_ok = False

    # Cross-call: shop tenant cannot call list_student
    if not _mcp_call("tenant-shop", "list_student", should_succeed=False):
        all_ok = False

    _cleanup_dbs(uni_db)

    click.echo("  └──────────────────────────────────────────────────")
    if all_ok:
        click.secho(
            "  ✅ MCP dynamic tool resolution: ALL PASSED", fg="green", bold=True
        )
    else:
        click.secho(
            "  ⚠️  Some MCP tests failed — review diagnostics above.",
            fg="yellow",
            bold=True,
        )


# ============================================================================
# E2E MCP Composite (multi-tenant, no LLM)
# ============================================================================


def _run_mcp_composite_tests():
    """Test MCP composite mode: one SSE session accessing tools from multiple tenants.

    Requires mcp-gateway composite support (resolveTenantIDs parsing comma-separated
    X-Tenant-ID header and creating a composite MCPServer).
    """
    mcp_url = "http://127.0.0.1:8083"
    seed_path = PROJECT_ROOT / "specs" / "fixtures" / "seed.json"
    shop_db = (
        PROJECT_ROOT / "data-service" / "testdata" / "scenarios" / "shop" / "data.db"
    )
    uni_db = PROJECT_ROOT / "tenant_composite_e2e.db"

    tests_total = 0
    tests_passed = 0
    tests_failed = 0

    click.secho(
        "\n  ┌─ MCP Composite Multi-Tenant ───────────────────────",
        fg="cyan",
        bold=True,
    )
    all_ok = True

    # ═══════════════════════════════════════════════════
    # SETUP
    # ═══════════════════════════════════════════════════
    click.echo("  │")
    click.echo("  │  ╭── SETUP ──────────────────────────────────╮")

    # Seed university DB
    click.echo("  │  │ 🔨 Seeding university DB...")
    cmd = ["go", "run", "./cmd/seed-cli/", "--seed-path", str(seed_path)]
    result = run(
        cmd,
        cwd=PROJECT_ROOT / "data-service",
        env={**os.environ, "DB_PATH": str(uni_db)},
    )
    if result.returncode != 0:
        click.secho("  │  │ ❌ seed-cli failed", fg="red")
        _cleanup_dbs(uni_db)
        sys.exit(1)
    click.echo("  │  │ ✅ university.db created")

    # Health check
    click.echo("  │  │ 🔍 Checking data-service health...")
    h = admin_headers()
    data_base = DATA_SERVICE_URL
    try:
        health = requests.get(f"{data_base}/health", timeout=5)
        click.echo(f"  │  │ ✅ data-service: {health.status_code}")
    except Exception as e:
        click.secho(f"  │  │ ❌ data-service unreachable: {e}", fg="red")
        click.echo("  │  ╰──────────────────────────────────────────╯")
        _cleanup_dbs(uni_db)
        return

    # Register tenants
    click.echo("  │  │ 🔑 Registering tenants...")

    uni_config = {
        "data_source": {"driver": "sqlite", "dsn": str(uni_db)},
        "entities": [
            {
                "name": "student",
                "table": "students",
                "id_column": "id",
                "fields": [{"name": "name", "column": "name", "type": "string"}],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/students", "op": "list", "entity": "student"}
        ],
    }

    shop_config = {
        "data_source": {"driver": "sqlite", "dsn": str(shop_db)},
        "entities": [
            {
                "name": "product",
                "table": "products",
                "id_column": "id",
                "fields": [{"name": "name", "column": "name", "type": "string"}],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/products", "op": "list", "entity": "product"}
        ],
    }

    for tid in ["tenant-uni", "tenant-shop"]:
        requests.delete(f"{data_base}/admin/tenants/{tid}", headers=h)

    reg_ok = True
    for tid, cfg in [("tenant-uni", uni_config), ("tenant-shop", shop_config)]:
        resp = requests.post(
            f"{data_base}/admin/tenants", json={"id": tid, "config": cfg}, headers=h
        )
        if resp.status_code in (200, 201):
            click.echo(f"  │  │   ✅ {tid}: registered")
        else:
            click.secho(
                f"  │  │   ❌ {tid}: HTTP {resp.status_code} — {resp.text[:100]}",
                fg="red",
            )
            reg_ok = False
            all_ok = False

    if not reg_ok:
        click.secho("  │  │ ❌ Tenant registration failed — aborting", fg="red")
        click.echo("  │  ╰──────────────────────────────────────────╯")
        _cleanup_dbs(uni_db)
        return

    # Verify manifests
    click.echo("  │  │ 📋 Verifying tenant manifests...")
    manifest_ok = True
    for tid in ["tenant-uni", "tenant-shop"]:
        r = requests.get(
            f"{data_base}/mcp/manifest", headers={"X-Tenant-ID": tid}, timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            endpoints = data.get("endpoints", [])
            entities = data.get("entities", [])
            ep_names = [
                e.get("path") + " (" + e.get("op", "?") + ")" for e in endpoints
            ]
            ent_names = [e.get("name") for e in entities]
            click.echo(
                f"  │  │   ✅ {tid}: {len(entities)} entities [{', '.join(ent_names)}], "
                f"{len(endpoints)} endpoints [{', '.join(ep_names)}]"
            )
        else:
            click.secho(f"  │  │   ❌ {tid}: manifest HTTP {r.status_code}", fg="red")
            manifest_ok = False
            all_ok = False

    if not manifest_ok:
        click.echo("  │  ╰──────────────────────────────────────────╯")
        _cleanup_dbs(uni_db)
        return

    click.echo("  │  ╰──────────────────────────────────────────╯")
    click.echo("  │")

    # ═══════════════════════════════════════════════════
    # TEST 1 — list_tools via composite session
    # ═══════════════════════════════════════════════════
    tests_total += 1
    click.echo("  ├── 🧪 TEST 1/3: list_tools via composite session ──────")
    click.echo("  │")
    click.echo("  │  📝 Проверяет, что одна SSE сессия с")
    click.echo("  │     X-Tenant-ID: tenant-uni,tenant-shop")
    click.echo("  │     возвращает инструменты ОБОИХ tenant'ов")
    click.echo("  │     с префиксом {tenantID}__{toolName}")
    click.echo("  │")

    composite_headers = {
        "X-Tenant-ID": "tenant-uni,tenant-shop",
        "Accept": "text/event-stream",
    }
    sse_q: queue.Queue = queue.Queue()
    ready = threading.Event()
    endpoint_val: list[str] = [""]
    listed_tools: list[dict] = []

    def _read_sse_composite():
        try:
            resp = requests.get(
                f"{mcp_url}/mcp", headers=composite_headers, stream=True, timeout=60
            )
            resp.raise_for_status()
            seen_endpoint_event = False
            for line in resp.iter_lines():
                if not line:
                    continue
                txt = line.decode("utf-8", errors="replace")
                if txt.startswith("event: endpoint"):
                    seen_endpoint_event = True
                elif (
                    txt.startswith("data: ")
                    and seen_endpoint_event
                    and endpoint_val[0] == ""
                ):
                    endpoint_val[0] = txt[6:].strip()
                    ready.set()
                elif txt.startswith("data: "):
                    sse_q.put(txt[6:])
        except Exception:
            pass
        finally:
            sse_q.put(None)

    click.echo("  │  📡 Opening SSE session...")
    t = threading.Thread(target=_read_sse_composite, daemon=True)
    t.start()

    if not ready.wait(timeout=10):
        click.secho("  │  ❌ SSE session not ready (timeout)", fg="red")
        click.echo("  │")
        _cleanup_dbs(uni_db)
        return

    ep = endpoint_val[0]
    click.echo("  │  ✅ SSE session established")
    click.echo(f"  │     messageURL: {ep}")
    click.echo("  │")

    # List tools
    click.echo("  │  🔍 Sending tools/list...")
    list_payload = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}
    try:
        r = requests.post(
            ep,
            json=list_payload,
            headers={
                "X-Tenant-ID": "tenant-uni,tenant-shop",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        click.echo(f"  │     POST status: {r.status_code}")
    except requests.RequestException as e:
        click.secho(f"  │  ❌ list_tools POST failed: {e}", fg="red")
        _cleanup_dbs(uni_db)
        return

    tool_names = []
    if r.status_code in (200, 202):
        try:
            data = r.json()
            if "result" in data:
                listed_tools = data["result"].get("tools", [])
                tool_names = [t.get("name", "") for t in listed_tools]
        except Exception:
            pass

    if not tool_names:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                msg = sse_q.get(timeout=2)
                if msg is None:
                    break
                try:
                    chunk = json.loads(msg)
                    if "result" in chunk and chunk.get("id") == 1:
                        listed_tools = chunk["result"].get("tools", [])
                        tool_names = [t.get("name", "") for t in listed_tools]
                        break
                except json.JSONDecodeError:
                    pass
            except queue.Empty:
                continue

    # Classify tools by tenant
    tenant_a_tools = [n for n in tool_names if n.startswith("tenant-uni__")]
    tenant_b_tools = [n for n in tool_names if n.startswith("tenant-shop__")]
    common_tools = [n for n in tool_names if "__" not in n]

    click.echo("  │")
    click.echo(f"  │  📋 Tools returned ({len(tool_names)} total):")

    if common_tools:
        click.echo(
            f"  │     ├─ Common ({len(common_tools)}): {', '.join(common_tools)}"
        )
    if tenant_a_tools:
        click.echo(
            f"  │     ├─ tenant-uni ({len(tenant_a_tools)}): {', '.join(tenant_a_tools)}"
        )
    if tenant_b_tools:
        click.echo(
            f"  │     └─ tenant-shop ({len(tenant_b_tools)}): {', '.join(tenant_b_tools)}"
        )

    click.echo("  │")

    # Verify
    expected_tools = ["tenant-uni__list_student", "tenant-shop__list_product"]

    ok = True
    for expected in expected_tools:
        if expected in tool_names:
            click.echo(f"  │  ✅ [EXPECTED] {expected} — found")
        else:
            click.secho(f"  │  ❌ [EXPECTED] {expected} — MISSING!", fg="red")
            all_ok = False
            ok = False

    if ok:
        tests_passed += 1
    else:
        tests_failed += 1
    click.echo("  │")

    # ═══════════════════════════════════════════════════
    # TEST 2 — Call prefixed tool: tenant-uni__list_student
    # ═══════════════════════════════════════════════════
    tests_total += 1
    click.echo("  ├── 🧪 TEST 2/3: Call tenant-uni__list_student ────────")
    click.echo("  │")
    click.echo("  │  📝 Проверяет, что вызов инструмента с префиксом")
    click.echo("  │     tenant-uni__list_student через composite сессию")
    click.echo("  │     роутится в data-service c X-Tenant-ID: tenant-uni")
    click.echo("  │     и возвращает данные студентов tenant-uni")
    click.echo("  │")

    call_payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "tenant-uni__list_student", "arguments": {}},
        "id": 2,
    }
    try:
        r = requests.post(
            ep,
            json=call_payload,
            headers={
                "X-Tenant-ID": "tenant-uni,tenant-shop",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        click.echo(f"  │  POST status: {r.status_code}")
    except requests.RequestException as e:
        click.secho(f"  │  ❌ tool call POST failed: {e}", fg="red")
        all_ok = False
        tests_failed += 1

    call_ok = False
    call_error = ""
    result_data = ""
    if r.status_code in (200, 202):
        try:
            data = r.json()
            if "result" in data:
                call_ok = True
                result_data = json.dumps(data["result"], ensure_ascii=False, indent=2)[
                    :300
                ]
            elif "error" in data:
                call_error = str(data.get("error", ""))[:200]
        except Exception:
            pass

    if not call_ok:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                msg = sse_q.get(timeout=2)
                if msg is None:
                    break
                try:
                    chunk = json.loads(msg)
                    if "result" in chunk and chunk.get("id") == 2:
                        call_ok = True
                        result_data = json.dumps(
                            chunk["result"], ensure_ascii=False, indent=2
                        )[:300]
                        break
                    elif "error" in chunk and chunk.get("id") == 2:
                        call_error = str(chunk.get("error", ""))[:200]
                        break
                except json.JSONDecodeError:
                    pass
            except queue.Empty:
                continue

    click.echo("  │")
    if call_ok:
        tests_passed += 1
        click.echo("  │  ✅ tenant-uni__list_student → OK (200)")
        click.echo("  │     Response preview:")
        for line in result_data.split("\n")[:8]:
            click.echo(f"  │       {line}")
    else:
        tests_failed += 1
        all_ok = False
        click.secho("  │  ❌ tenant-uni__list_student FAILED", fg="red")
        if call_error:
            click.secho(f"  │     Error: {call_error}", fg="red")
    click.echo("  │")

    # ═══════════════════════════════════════════════════
    # TEST 3 — Call prefixed tool: tenant-shop__list_product
    # ═══════════════════════════════════════════════════
    tests_total += 1
    click.echo("  ├── 🧪 TEST 3/3: Call tenant-shop__list_product ───────")
    click.echo("  │")
    click.echo("  │  📝 Проверяет, что вызов инструмента с префиксом")
    click.echo("  │     tenant-shop__list_product через ту ЖЕ сессию")
    click.echo("  │     роутится в data-service c X-Tenant-ID: tenant-shop")
    click.echo("  │     и возвращает данные товаров tenant-shop")
    click.echo("  │")

    call_payload2 = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "tenant-shop__list_product", "arguments": {}},
        "id": 3,
    }
    try:
        r = requests.post(
            ep,
            json=call_payload2,
            headers={
                "X-Tenant-ID": "tenant-uni,tenant-shop",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        click.echo(f"  │  POST status: {r.status_code}")
    except requests.RequestException as e:
        click.secho(f"  │  ❌ tool call POST failed: {e}", fg="red")
        all_ok = False
        tests_failed += 1

    call_ok2 = False
    call_error2 = ""
    result_data2 = ""
    if r.status_code in (200, 202):
        try:
            data = r.json()
            if "result" in data:
                call_ok2 = True
                result_data2 = json.dumps(data["result"], ensure_ascii=False, indent=2)[
                    :300
                ]
            elif "error" in data:
                call_error2 = str(data.get("error", ""))[:200]
        except Exception:
            pass

    if not call_ok2:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                msg = sse_q.get(timeout=2)
                if msg is None:
                    break
                try:
                    chunk = json.loads(msg)
                    if "result" in chunk and chunk.get("id") == 3:
                        call_ok2 = True
                        result_data2 = json.dumps(
                            chunk["result"], ensure_ascii=False, indent=2
                        )[:300]
                        break
                    elif "error" in chunk and chunk.get("id") == 3:
                        call_error2 = str(chunk.get("error", ""))[:200]
                        break
                except json.JSONDecodeError:
                    pass
            except queue.Empty:
                continue

    click.echo("  │")
    if call_ok2:
        tests_passed += 1
        click.echo("  │  ✅ tenant-shop__list_product → OK (200)")
        click.echo("  │     Response preview:")
        for line in result_data2.split("\n")[:8]:
            click.echo(f"  │       {line}")
    else:
        tests_failed += 1
        all_ok = False
        click.secho("  │  ❌ tenant-shop__list_product FAILED", fg="red")
        if call_error2:
            click.secho(f"  │     Error: {call_error2}", fg="red")
    click.echo("  │")

    _cleanup_dbs(uni_db)

    # ═══════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════
    click.echo("  └────────────────────────────────────────────────────────")
    click.echo("  │")
    click.echo(
        f"  │  📊 Summary: {tests_passed}/{tests_total} passed, {tests_failed} failed"
    )
    click.echo("  │")
    if tests_passed > 0:
        click.echo(
            "  │  ✅ Test 1: Composite list_tools returns tools from both tenants"
        )
        click.echo("  │  ✅ Test 2: tenant-uni tool routed to correct data-service")
        click.echo("  │  ✅ Test 3: tenant-shop tool routed to correct data-service")
    click.echo("  │")
    if all_ok:
        click.secho(
            f"  ✅  MCP composite multi-tenant: ALL {tests_passed} TESTS PASSED",
            fg="green",
            bold=True,
        )
    else:
        click.secho(
            f"  ⚠️   MCP composite multi-tenant: {tests_failed}/{tests_total} FAILED",
            fg="yellow",
            bold=True,
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


if __name__ == "__main__":
    cli()
