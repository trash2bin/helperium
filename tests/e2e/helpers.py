"""Shared helpers for all e2e tests.

Provides:
- admin_headers(): auth headers for admin API
- register_tenant(): register a tenant via admin API
- delete_tenant(): remove a tenant
- seed_database(): generate SQLite DB from seed.json
- mcp_call(): make MCP JSON-RPC tool call over SSE
- save_and_check_persistence(): verify config written to .data/tenants/
- run(): subprocess helper
"""

from __future__ import annotations

import json
import os
import queue
import subprocess

import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests


# ── Paths (lazy, for import safety) ───────────────────────────────────────

def project_root() -> Path:
    """Find project root by AGENTS.md marker."""
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env)
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "AGENTS.md").exists():
            return parent
    return current.parents[2]


def scenarios_dir() -> Path:
    return project_root() / "data-service" / "testdata" / "scenarios"


def tenants_data_dir() -> Path:
    return project_root() / ".data" / "tenants"


# ── URLs ───────────────────────────────────────────────────────────────────

def _env_url(key: str, default: str) -> str:
    return os.environ.get(key, default)


def data_service_url() -> str:
    return _env_url("DATA_SERVICE_URL", "http://127.0.0.1:8084")


def mcp_gateway_url() -> str:
    return _env_url("MCP_SERVICE_URL", "http://127.0.0.1:8083")


def api_service_url() -> str:
    host = os.environ.get("DEMO_API_HOST", "127.0.0.1")
    port = os.environ.get("DEMO_API_PORT", "8081")
    return _env_url("API_SERVICE_URL", f"http://{host}:{port}")


def demo_web_url() -> str:
    host = os.environ.get("DEMO_WEB_HOST", "127.0.0.1")
    port = os.environ.get("DEMO_WEB_PORT", "8080")
    return _env_url("DEMO_WEB_URL", f"http://{host}:{port}")


# ── Auth ───────────────────────────────────────────────────────────────────

def admin_token() -> str | None:
    return os.environ.get("ADMIN_TOKEN") or os.environ.get("ADMIN_API_TOKEN")


def admin_headers() -> dict[str, str]:
    """Build auth headers for admin API. Raises if missing."""
    token = admin_token()
    if not token:
        raise ValueError(
            "ADMIN_TOKEN not set — admin API calls require it.\n"
            "     Set:  export ADMIN_TOKEN=secret\n"
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Database seed ──────────────────────────────────────────────────────────

def seed_database(
    db_path: Path,
    scenario: str | None = None,
    seed_path: Path | None = None,
    project_root_dir: Path | None = None,
) -> dict:
    """Generate a SQLite database from a scenario using Python seedgen.

    Args:
        db_path: Absolute path to the target .db file
        scenario: Scenario name (e.g. 'sqlite-testseed', 'shop').
                  Overrides seed_path if given.
        seed_path: Path to seed.json (deprecated — use scenario instead)
        project_root_dir: Project root (default: auto-detect)

    Returns:
        The parsed ScenarioConfig (for inspection in tests).

    Raises:
        FileNotFoundError: If scenario/seed file not found.
        RuntimeError: If materialization fails.
    """
    root = project_root_dir or project_root()

    if scenario:
        sc_dir = root / "agent-db" / "scenarios" / scenario
        if not (sc_dir / "config.json").exists():
            # Fallback: still in data-service/testdata (before full migration)
            sc_dir = root / "data-service" / "testdata" / "scenarios" / scenario
        if not (sc_dir / "config.json").exists():
            raise FileNotFoundError(f"Scenario not found: {scenario} "
                                    f"(tried {sc_dir / 'config.json'})")

        from agent_db.seedgen import materialize
        cfg = materialize(str(sc_dir), force=True, output_dsn=str(db_path))
        return cfg

    # Legacy path: explicit seed_path
    if seed_path is None:
        seed_path = root / "specs" / "fixtures" / "seed.json"

    if not seed_path.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_path}")

    # Use the old seed-cli path but with Python seedgen
    import json as _json
    from agent_db.seedgen import apply_with_ddl, generate_ddl
    from agent_db.seedgen.models import (Entity, EntityField, FieldType,
                                          DataSourceConfig, ScenarioConfig,
                                          Relation)
    import sqlite3

    with open(seed_path) as f:
        raw_seed = _json.load(f)

    from helperium_sdk.seed_models import StorageSeed
    seed = StorageSeed.model_validate(raw_seed)

    # Build minimal entities from seed data structure
    entities = [
        Entity(name="group", table="groups", id_column="id",
               fields=[EntityField(name=n, column=n, type=FieldType.STRING)
                       for n in ["id", "name", "speciality"]]),
        Entity(name="student", table="students", id_column="id",
               fields=[EntityField(name=n, column=n,
                                   type=FieldType.INT if n == "course" else FieldType.STRING)
                       for n in ["id", "name", "group_id", "course"]]),
        Entity(name="teacher", table="teachers", id_column="id",
               fields=[EntityField(name=n, column=n, type=FieldType.STRING)
                       for n in ["id", "name", "disciplines_json"]]),
        Entity(name="discipline", table="disciplines", id_column="id",
               fields=[EntityField(name=n, column=n, type=FieldType.STRING)
                       for n in ["id", "name", "description"]]),
        Entity(name="schedule", table="schedule", id_column="id",
               fields=[EntityField(name=n, column=n, type=FieldType.STRING)
                       for n in ["id", "day", "group_id", "lessons_json"]]),
        Entity(name="grade", table="grades", id_column="id",
               fields=[EntityField(name=n, column=n, type=FieldType.STRING)
                       for n in ["id", "student_id", "discipline_id", "grade", "date"]]),
    ]

    ddl = generate_ddl(entities, "sqlite")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        apply_with_ddl(conn, ddl=ddl, seed=seed, driver="sqlite")
        conn.commit()
    finally:
        conn.close()

    return ScenarioConfig(entities=entities, data_source=DataSourceConfig(
        driver="sqlite", dsn=str(db_path)))


def cleanup_db(*db_paths: Path) -> None:
    """Remove temporary database files."""
    for p in db_paths:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ── Scenario config loading ────────────────────────────────────────────────

def load_scenario_config(scenario: str) -> dict:
    """Load config.json for a scenario."""
    config_path = scenarios_dir() / scenario / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return json.loads(config_path.read_text())


# ── Tenant registration ────────────────────────────────────────────────────

def register_tenant(
    tenant_id: str,
    config: dict,
    service_url: str | None = None,
) -> dict[str, Any]:
    """Register a tenant via admin API. Returns response JSON."""
    base = service_url or data_service_url()
    h = admin_headers()
    resp = requests.post(
        f"{base}/admin/tenants",
        json={"id": tenant_id, "config": config},
        headers=h,
        timeout=10,
    )
    return {"status": resp.status_code, "body": resp.json() if resp.text else {},
            "text": resp.text}


def delete_tenant(tenant_id: str, service_url: str | None = None) -> int:
    """Delete a tenant via admin API. Returns status code."""
    base = service_url or data_service_url()
    resp = requests.delete(
        f"{base}/admin/tenants/{tenant_id}",
        headers=admin_headers(),
        timeout=10,
    )
    return resp.status_code


# ── MCP tool call (SSE protocol) ───────────────────────────────────────────

class MCPCallResult:
    """Result of an MCP tool call over SSE."""

    def __init__(self, success: bool, result: Any = None, error: str = "",
                 session_ok: bool = True):
        self.success = success
        self.result = result
        self.error = error
        self.session_ok = session_ok

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        if self.success:
            return f"<MCPCallResult OK: {str(self.result)[:100]}>"
        return f"<MCPCallResult FAIL: {self.error[:100]}>"


def mcp_call(
    tool_name: str,
    arguments: dict | None = None,
    tenant_ids: str = "default",
    mcp_url: str | None = None,
    timeout: float = 30,
) -> MCPCallResult:
    """Make an MCP JSON-RPC tool call over SSE (full protocol).

    Opens SSE session → gets endpoint URL → POSTs JSON-RPC →
    reads result from SSE stream or HTTP response.

    Args:
        tool_name: Name of the MCP tool to call
        arguments: Tool arguments dict (default: {})
        tenant_ids: X-Tenant-ID header value (comma-separated for composite)
        mcp_url: mcp-gateway URL (default: http://127.0.0.1:8083)
        timeout: Max seconds to wait for result

    Returns:
        MCPCallResult with success flag + result/error
    """
    base = mcp_url or mcp_gateway_url()
    args = arguments or {}

    # 1. Open SSE session
    headers = {"X-Tenant-ID": tenant_ids, "Accept": "text/event-stream"}
    sse_q: queue.Queue = queue.Queue()
    ready = threading.Event()
    endpoint_url: list[str] = [""]
    sse_error: list[str] = [""]

    def _read_sse():
        try:
            resp = requests.get(
                f"{base}/mcp", headers=headers, stream=True, timeout=timeout
            )
            resp.raise_for_status()
            seen_endpoint = False
            for line_bytes in resp.iter_lines():
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8", errors="replace")
                if line.startswith("event: endpoint"):
                    seen_endpoint = True
                elif line.startswith("data: ") and seen_endpoint and not endpoint_url[0]:
                    endpoint_url[0] = line[6:].strip()
                    ready.set()
                elif line.startswith("data: ") and endpoint_url[0]:
                    sse_q.put(line[6:])
        except Exception as e:
            sse_error[0] = str(e)
        finally:
            sse_q.put(None)

    t = threading.Thread(target=_read_sse, daemon=True)
    t.start()

    if not ready.wait(timeout=10):
        err = sse_error[0] or "SSE session not ready (timeout)"
        return MCPCallResult(False, error=err, session_ok=False)

    ep = endpoint_url[0]
    if not ep:
        return MCPCallResult(False, error="No MCP endpoint URL received",
                             session_ok=False)

    # 2. Call tool via JSON-RPC
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
        "id": 1,
    }
    try:
        r = requests.post(
            ep,
            json=payload,
            headers={
                "X-Tenant-ID": tenant_ids,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        return MCPCallResult(False, error=f"POST failed: {e}")

    # 3. Parse immediate result
    if r.status_code in (200, 202):
        try:
            data = r.json()
            if "result" in data:
                return MCPCallResult(True, result=data["result"])
            if "error" in data:
                err_info = data["error"]
                err_msg = (err_info.get("message", str(err_info))[:300]
                           if isinstance(err_info, dict) else str(err_info)[:300])
                return MCPCallResult(False, error=err_msg)
        except (json.JSONDecodeError, ValueError):
            pass

    # 4. Wait for SSE result
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = sse_q.get(timeout=2)
            if msg is None:
                break
            try:
                chunk = json.loads(msg)
                if chunk.get("id") == 1:
                    if "result" in chunk:
                        return MCPCallResult(True, result=chunk["result"])
                    if "error" in chunk:
                        err_info = chunk["error"]
                        err_msg = (err_info.get("message", str(err_info))[:300]
                                   if isinstance(err_info, dict)
                                   else str(err_info)[:300])
                        return MCPCallResult(False, error=err_msg)
            except json.JSONDecodeError:
                pass
        except queue.Empty:
            continue

    return MCPCallResult(False, error="No result received via SSE (timeout)")


# ── Config persistence check ────────────────────────────────────────────────

def save_and_check_persistence(
    tenant_id: str,
    expected: dict | None = None,
    data_dir: Path | None = None,
    project_root_dir: Path | None = None,
) -> dict:
    """Check that tenant config was persisted to .data/tenants/{id}.json.

    Returns the loaded config dict.
    """
    root = project_root_dir or project_root()
    ddir = data_dir or tenants_data_dir()
    config_path = ddir / f"{tenant_id}.json"
    if not config_path.exists():
        raise AssertionError(
            f"Tenant config not persisted: {config_path}\n"
            f"(expected at {ddir}/{tenant_id}.json)"
        )
    config = json.loads(config_path.read_text())
    if expected:
        for key, val in expected.items():
            if key in config and config[key] != val:
                raise AssertionError(
                    f"Persistence mismatch for {tenant_id}.{key}: "
                    f"expected={val!r}, got={config[key]!r}"
                )
    return config
