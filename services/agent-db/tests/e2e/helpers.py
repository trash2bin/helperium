"""Shared helpers for all e2e tests.

Provides:
- admin_headers(): auth headers for admin API
- register_tenant(): register a tenant via admin API
- delete_tenant(): remove a tenant
- seed_database(): generate SQLite DB from a scenario (agent-db seedgen)
- mcp_call(): invoke an MCP tool over standard Streamable HTTP v2
- save_and_check_persistence(): verify config written to .data/tenants/
- run(): subprocess helper
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
    return project_root() / "services/data-service" / "testdata" / "scenarios"


def tenants_data_dir() -> Path:
    return project_root() / ".data" / "tenants"


# ── URLs ───────────────────────────────────────────────────────────────────


def _env_url(key: str, default: str) -> str:
    return os.environ.get(key, default)


def data_service_url() -> str:
    return _env_url("DATA_SERVICE_URL", "http://127.0.0.1:8084")


def mcp_gateway_url() -> str:
    return _env_url("MCP_GATEWAY_URL", "http://127.0.0.1:8083")


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
    scenario: str,
    project_root_dir: Path | None = None,
) -> dict:
    """Generate a SQLite database from a scenario using Python seedgen.

    Args:
        db_path: Absolute path to the target .db file
        scenario: Scenario name (e.g. 'sqlite-testseed', 'shop').
        project_root_dir: Project root (default: auto-detect)

    Returns:
        The parsed ScenarioConfig (for inspection in tests).

    Raises:
        FileNotFoundError: If scenario/seed file not found.
        RuntimeError: If materialization fails.
    """
    root = project_root_dir or project_root()

    sc_dir = root / "services/agent-db" / "scenarios" / scenario
    if not (sc_dir / "config.json").exists():
        # Scenarios live in data-service/testdata (no agent-db/scenarios dir yet)
        sc_dir = root / "services/data-service" / "testdata" / "scenarios" / scenario
    if not (sc_dir / "config.json").exists():
        raise FileNotFoundError(
            f"Scenario not found: {scenario} (tried {sc_dir / 'config.json'})"
        )

    from agent_db.seedgen import materialize

    cfg = materialize(str(sc_dir), force=True, output_dsn=str(db_path))
    return cfg


def cleanup_db(*db_paths: Path) -> None:
    """Remove temporary database files."""
    for p in db_paths:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def temp_db_path(prefix: str, project_root_dir: Path | None = None) -> Path:
    """Return a unique temp DB path under ``.data/`` (not yet created).

    Parent dir is created; caller should seed/register and cleanup with
    :func:`cleanup_db`.
    """
    root = project_root_dir or project_root()
    suffix = uuid.uuid4().hex[:8]
    p = root / ".data" / f"e2e_{prefix}_{suffix}.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── SSE parsing (shared by e2e + e2e-llm + bench) ────────────────────────


def parse_sse_stream(response, idle_timeout: int = 20) -> dict:
    """Parse SSE stream from api-service into structured result.

    Stream format: ``data: {"type": "<event>", ...}`` (no ``event:`` lines —
    the event type lives inside the JSON payload).

    Returns a dict with:
      - events: all raw payloads (in order)
      - tool_calls / tool_results / status_messages: filtered event lists
      - final_text: concatenated ``token`` + ``final`` text
      - errors: error messages
    """
    result = {
        "events": [],
        "tool_calls": [],
        "tool_results": [],
        "final_text": "",
        "errors": [],
        "status_messages": [],
    }
    try:
        sock = getattr(
            getattr(getattr(response.raw, "_fp", None), "fp", None), "_sock", None
        )
        if sock is not None:
            sock.settimeout(idle_timeout)
    except (AttributeError, OSError):
        pass
    try:
        for line_bytes in response.iter_lines():
            if not line_bytes:
                continue
            line = line_bytes.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            result["events"].append(payload)
            ev_type = payload.get("type", "")
            if ev_type == "status":
                result["status_messages"].append(
                    payload.get("message") or payload.get("phase", "")
                )
            elif ev_type == "tool_call":
                result["tool_calls"].append(payload)
            elif ev_type == "tool_result":
                result["tool_results"].append(payload)
            elif ev_type == "token":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "error":
                result["errors"].append(payload.get("text", str(payload)))
            elif ev_type == "final":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "done":
                break
    except (requests.ConnectionError, TimeoutError, OSError):
        if not result["events"]:
            result["errors"].append("SSE stream ended unexpectedly")
    return result


# ── Subprocess helpers (scripted api-service, bench) ───────────────────────


def find_free_port() -> int:
    """Return a free localhost TCP port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(url: str, timeout: int = 30) -> bool:
    """Poll ``{url}/health`` until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/health", timeout=3)
            if r.status_code == 200:
                return True
        except (requests.ConnectionError, OSError):
            pass
        time.sleep(0.5)
    return False


# ── Scenario config loading ────────────────────────────────────────────────


def load_scenario_config(scenario: str) -> dict:
    """Load config.json for a scenario."""
    config_path = scenarios_dir() / scenario / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return json.loads(config_path.read_text())


# ── TestTenant — единый примитив тестового тенанта ─────────────────────────


class TestTenant:
    """A registered test tenant with its lifecycle.

    Centralises the pattern every e2e test repeats by hand:
    seed DB → build config → register → cleanup. Tests receive a fully
    usable tenant (``tenant.id``, ``tenant.db_path``, ``tenant.config``,
    ``tenant.tools``) and just call ``tenant.cleanup()`` — or use the
    ``tenant`` pytest fixture which does it automatically.
    """

    # pytest: не собирать как тестовый класс (префикс Test)
    __test__ = False

    def __init__(
        self,
        tenant_id: str,
        db_path: Path,
        config: dict | None = None,
        filterable_rules: list[dict] | None = None,
    ):
        self.id = tenant_id
        self.db_path = db_path
        self.config = config or {}
        self.filterable_rules = filterable_rules or []
        self._registered = False
        self._rewrite = False
        self._tools: list[dict] = []

    # ── registration ──────────────────────────────────────────────────────

    def register(self, rewrite: bool | None = None) -> "TestTenant":
        """Register the tenant (rewrite via introspection when needed).

        ``rewrite`` overrides; if None, uses the mode chosen by
        :func:`make_tenant` (auto-rewrite for scenarios without config.json).
        """
        if self._registered:
            return self
        delete_tenant(self.id)  # cleanup stale from previous runs
        if rewrite is None:
            rewrite = self._rewrite
        if rewrite:
            register_tenant_and_rewrite(
                self.id, self.db_path, self.filterable_rules
            )
        else:
            cfg = self.config or {
                "data_source": {
                    "driver": "sqlite",
                    "dsn": str(self.db_path),
                    "read_only": True,
                }
            }
            result = register_tenant(self.id, cfg)
            if result["status"] not in (200, 201):
                raise RuntimeError(
                    f"Register {self.id}: status={result['status']} "
                    f"body={result['text'][:200]}"
                )
        self._registered = True
        return self

    def cleanup(self) -> None:
        """Delete tenant and remove its temp DB (idempotent)."""
        if self._registered:
            try:
                delete_tenant(self.id)
            except Exception:
                pass
            self._registered = False
        cleanup_db(self.db_path)

    # ── helpers for tests ─────────────────────────────────────────────────

    def mcp_call(
        self,
        tool_name: str,
        arguments: dict | None = None,
        timeout: float = 30,
    ) -> MCPCallResult:
        """Call an MCP tool as this tenant."""
        return mcp_call(tool_name, arguments, tenant_ids=self.id, timeout=timeout)

    def tools(self, refresh: bool = False) -> list[dict]:
        """List MCP tools for this tenant (cached)."""
        if refresh or not self._tools:
            headers = {"X-Tenant-ID": self.id}
            if api_key := os.environ.get("MCP_API_KEY"):
                headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.get(
                f"{mcp_gateway_url()}/mcp/manifest",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                # manifest — это конфиг: тулы в mcp_tools (или tools для legacy)
                self._tools = data.get("mcp_tools", data.get("tools", []))
        return self._tools

    def __repr__(self) -> str:
        return f"<TestTenant {self.id} db={self.db_path.name}>"


def make_tenant(
    scenario: str | None = None,
    *,
    tenant_id: str | None = None,
    prefix: str = "e2e",
    config: dict | None = None,
    rewrite: bool = False,
    filterable_rules: list[dict] | None = None,
    db_path: Path | None = None,
) -> TestTenant:
    """Create a ready-to-register TestTenant.

    If ``scenario`` is given, seeds a fresh DB from it:
      - scenarios with config.json (including sqlite-testseed) → seed_database
      - scenarios with only create_db.py (auto-shop, clinic) → create_scenario_db
    Scenarios without config.json register via rewrite (introspection
    generates the config from the DB — entities/endpoints/tools appear).

    ``rewrite=True`` registers via ``POST /admin/config/rewrite``.
    """
    tid = tenant_id or e2e_tenant_id(prefix)
    sc_dir = scenarios_dir() / scenario if scenario else None
    has_config = bool(sc_dir and (sc_dir / "config.json").exists())
    if db_path is None:
        if scenario:
            # Config-backed scenarios must get a private materialized DB for
            # every tenant.  A create_db.py may exist as a checked-in fixture
            # generator, but its data.db is shared and must not be registered
            # directly: two tenants would then read and mutate the same DB.
            if has_config:
                db_path = temp_db_path(prefix)
                seed_database(db_path, scenario=scenario)
            else:
                # create_db.py scenario (auto-shop, clinic, shop) — авто-регенерация
                db_path = ensure_scenario_db(scenario)
        else:
            db_path = Path(config["data_source"]["dsn"]) if config else temp_db_path(prefix)

    if config is None and scenario and has_config:
        config = load_scenario_config(scenario)
        config = {
            **config,
            "data_source": {**config.get("data_source", {}), "dsn": str(db_path)},
        }
    # Сценарии без config.json (auto-shop, clinic) не могут зарегистрироваться
    # с фиксированным конфигом — entities/endpoints пустые. Для них обязателен
    # rewrite (introspection).
    if scenario and not has_config and not rewrite:
        rewrite = True
    t = TestTenant(tid, db_path, config=config, filterable_rules=filterable_rules)
    t._rewrite = rewrite
    return t


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
    return {
        "status": resp.status_code,
        "body": resp.json() if resp.text else {},
        "text": resp.text,
    }


def delete_tenant(tenant_id: str, service_url: str | None = None) -> int:
    """Delete a tenant via admin API. Returns status code."""
    base = service_url or data_service_url()
    resp = requests.delete(
        f"{base}/admin/tenants/{tenant_id}",
        headers=admin_headers(),
        timeout=10,
    )
    return resp.status_code


# ── MCP tool call (Streamable HTTP v2) ──────────────────────────────────────


class MCPCallResult:
    """Compatibility result for a tenant-scoped Streamable HTTP MCP tool call."""

    def __init__(
        self,
        success: bool,
        result: Any = None,
        error: str = "",
        session_ok: bool = True,
    ):
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


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an official SDK content block into the historical test shape."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if isinstance(block, dict):
        return block
    data: dict[str, Any] = {"type": getattr(block, "type", "text")}
    if text := getattr(block, "text", None):
        data["text"] = text
    return data


async def _streamable_mcp_call(
    tool_name: str,
    arguments: dict[str, Any],
    tenant_ids: str,
    base_url: str,
    timeout: float,
) -> dict[str, Any]:
    """Invoke one tool through the official Python MCP SDK v2."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"X-Tenant-ID": tenant_ids}
    if api_key := os.environ.get("MCP_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(timeout, read=timeout),
        follow_redirects=True,
    ) as http_client:
        transport = streamable_http_client(
            f"{base_url.rstrip('/')}/mcp", http_client=http_client
        )
        async with Client(transport) as client:
            response = await client.call_tool(tool_name, arguments)

    return {
        "content": [_content_block_to_dict(block) for block in response.content],
        "isError": response.is_error,
    }


def mcp_call(
    tool_name: str,
    arguments: dict | None = None,
    tenant_ids: str = "default",
    mcp_url: str | None = None,
    timeout: float = 30,
) -> MCPCallResult:
    """Invoke a tenant-scoped MCP tool over standard Streamable HTTP.

    The helper preserves the historical `content`/`isError` result dictionary so
    business-level E2E assertions remain transport agnostic. The official MCP
    v2 SDK owns transport initialization, requests and shutdown.
    """
    try:
        result = asyncio.run(
            _streamable_mcp_call(
                tool_name,
                arguments or {},
                tenant_ids,
                mcp_url or mcp_gateway_url(),
                timeout,
            )
        )
    except Exception as exc:
        return MCPCallResult(False, error=f"Streamable HTTP call failed: {exc}")
    return MCPCallResult(True, result=result)


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


# ── Scenario database creation & tenant rewrite ───────────────────────────


def create_scenario_db(scenario: str, project_root_dir: Path | None = None) -> Path:
    """Create a scenario database using the scenario's create_db.py script.

    Args:
        scenario: Scenario name (e.g. 'sqlite-testseed', 'auto-shop', 'clinic').
        project_root_dir: Optional project root (auto-detected if not provided).

    Returns:
        Path to the created database file.

    Raises:
        FileNotFoundError: If scenario directory not found.
        RuntimeError: If database creation fails.
    """
    root = project_root_dir or project_root()
    sc_dir = root / "services/data-service" / "testdata" / "scenarios" / scenario
    if not sc_dir.exists():
        # Fallback: some scenarios in agent-db
        sc_dir = root / "services/agent-db" / "scenarios" / scenario
    if not sc_dir.exists():
        raise FileNotFoundError(f"Scenario dir not found: {sc_dir}")

    script = sc_dir / "create_db.py"
    db_path = sc_dir / "data.db"

    # Remove old DB if exists
    if db_path.exists():
        db_path.unlink()
        for ext in ("-wal", "-shm"):
            (db_path.with_suffix(db_path.suffix + ext)).unlink(missing_ok=True)

    # Run database creation script
    result = subprocess.run(
        ["python3", str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"create_db.py failed:\n{result.stderr}")

    if not db_path.exists():
        raise RuntimeError(f"DB not created: {db_path}")

    return db_path


def ensure_scenario_db(
    scenario: str, project_root_dir: Path | None = None
) -> Path:
    """Return a valid scenario DB, regenerating it if stale/corrupt.

    Scenario DBs (auto-shop, clinic, shop) are gitignored and can be left
    empty/corrupt by docker runs or crashed generators. This checks the DB
    has real tables and regenerates it (via create_db.py) otherwise.

    Returns the path to the valid DB.
    """
    root = project_root_dir or project_root()
    sc_dir = root / "services/data-service" / "testdata" / "scenarios" / scenario
    db_path = sc_dir / "data.db"

    def _valid(p: Path) -> bool:
        if not p.exists() or p.stat().st_size < 4096:
            return False
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
            try:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            finally:
                conn.close()
            return len(tables) > 0
        except Exception:
            return False

    if _valid(db_path):
        return db_path

    if (sc_dir / "create_db.py").exists():
        # regenerate from the scenario's own generator
        return create_scenario_db(scenario, project_root_dir=root)

    # Fallback: shop — генератор в testdata/scripts/create_shop_db.py (как CI-workflow)
    generator = root / "services/data-service" / "testdata" / "scripts" / "create_shop_db.py"
    if generator.exists():
        env = {**os.environ, "SHOP_DB": str(db_path)}
        result = subprocess.run(
            ["python3", str(generator)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"create_shop_db.py failed:\n{result.stderr}")
        if not _valid(db_path):
            raise RuntimeError(f"create_shop_db.py produced invalid DB: {db_path}")
        return db_path

    raise RuntimeError(
        f"Scenario {scenario} DB is invalid and no create_db.py to regenerate"
    )


def register_tenant_and_rewrite(
    tenant_id: str,
    db_path: Path,
    filterable_rules: list[dict] | None = None,
    service_url: str | None = None,
) -> dict:
    """Register a tenant with minimal config, then POST /admin/config/rewrite.

    Args:
        tenant_id: Unique tenant identifier.
        db_path: Path to the tenant's SQLite database.
        filterable_rules: Optional custom filterable field rules to add before rewrite.
        service_url: Optional data-service URL (default: from env).

    Returns:
        Rewrite response JSON (contains entities/endpoints counts).

    Raises:
        AssertionError: If registration or rewrite fails.
    """
    base = service_url or data_service_url()
    h = admin_headers()

    config = {
        "data_source": {
            "driver": "sqlite",
            "dsn": str(db_path),
            "read_only": True,
        },
    }
    if filterable_rules:
        config["filterable_rules"] = filterable_rules

    # 1. Register tenant
    resp = requests.post(
        f"{base}/admin/tenants",
        json={"id": tenant_id, "config": config},
        headers=h,
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        if resp.status_code == 409:
            requests.delete(f"{base}/admin/tenants/{tenant_id}", headers=h, timeout=10)
            resp = requests.post(
                f"{base}/admin/tenants",
                json={"id": tenant_id, "config": config},
                headers=h,
                timeout=10,
            )
    assert resp.status_code in (200, 201), (
        f"Register tenant: {resp.status_code} {resp.text[:200]}"
    )

    # 2. Rewrite config (introspect + generate)
    resp = requests.post(
        f"{base}/admin/config/rewrite",
        headers={"X-Tenant-ID": tenant_id, **h},
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Rewrite: {resp.status_code} {resp.text[:200]}"
    )

    return resp.json()


def e2e_tenant_id(prefix: str) -> str:
    """Generate a unique e2e tenant ID with prefix."""
    return f"e2e-{prefix}-{uuid.uuid4().hex[:6]}"
