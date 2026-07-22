"""E2E тест LLM с новыми тулами (grep/filter/schema).

Полный цикл:
1. Создать SQLite БД из seed-сценария
2. Зарегистрировать tenant на data-service + rewrite
3. Создать/пересоздать агента с этим tenant'ом
4. Проверить что MCP тулы доступны
5. Отправить серию вопросов с разными session_id
6. Проверить что LLM вызывает корректные тулы (grep_*, filter_*, schema_*)
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    data_service_url,
    api_service_url,
    project_root,
    scenarios_dir,
)

pytestmark = [
    pytest.mark.skipif(
        not admin_headers(),
        reason="ADMIN_TOKEN not set",
    ),
    pytest.mark.skipif(
        not (os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")),
        reason="LLM API key not set — set OPENAI_API_KEY or LLM_API_KEY",
    ),
]

# ── Scenario & LLM config ─────────────────────────────────────────────────

_SCENARIO = "auto-shop"
_LLM_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
_LLM_MODEL = os.environ.get("OPENAI_MODEL", "deepseek-v4-flash")
_LLM_API_BASE = os.environ.get("OPENAI_API_BASE", "https://polza.ai/api/v1")


# ── Backlog helpers ───────────────────────────────────────────────────────


def _backlog_path(session_id: str) -> Path | None:
    if not session_id:
        return None
    short = session_id.split(":")[-1] if ":" in session_id else session_id
    candidates = [
        Path("backlog") / f"{session_id}.jsonl",
        Path("backlog") / f"agent_{_agent_name}_{short}.jsonl",
        Path("backlog") / f"agent_{session_id}.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _print_backlog(path: Path) -> None:
    import json
    content = path.read_text(encoding="utf-8")
    records = content.split("---===---")
    print(f"\n  📜 Backlog ({path.name}, {len(records)} records):")
    for i, rec in enumerate(records):
        rec = rec.strip()
        if not rec:
            continue
        try:
            o = json.loads(rec)
        except json.JSONDecodeError:
            continue
        event = o.get("event") or o.get("type", "")
        ts = (o.get("ts") or o.get("timestamp", ""))[-8:] if (o.get("ts") or o.get("timestamp")) else ""
        if event == "turn_start":
            print(f"     [{i}] 🟢 START: {o.get('data',{}).get('user_message','')[:80]}")
        elif event == "llm_call":
            d = o.get("data", {})
            it = o.get("iteration", "?")
            ms = d.get("duration_ms", "?")
            print(f"     [{i}] 🤖 LLM [{ts}] iter={it} {o.get('model','?')} tokens={d.get('prompt_tokens','?')}+{d.get('completion_tokens','?')} dur={ms if isinstance(ms,str) else f'{ms:.0f}ms'}")
        elif event == "tool_call":
            print(f"     [{i}] 🛠️  CALL [{ts}] iter={o.get('iteration','?')} {o.get('data',{}).get('name','?')}({json.dumps(o.get('data',{}).get('arguments',{}),ensure_ascii=False)[:120]})")
        elif event == "tool_result":
            res = (o.get("data",{}).get("result","") or "")[:120]
            print(f"     [{i}] 📦 RESULT [{ts}] {o.get('data',{}).get('name','?')}: {res}")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _create_tenant_db(scenario: str) -> Path:
    """Create a fresh SQLite DB from a scenario."""
    sc_dir = scenarios_dir() / scenario
    if not sc_dir.exists():
        raise FileNotFoundError(f"Scenario not found: {sc_dir}")

    script = sc_dir / "create_db.py"
    db_path = sc_dir / "data.db"

    # Clean old files
    if db_path.exists():
        db_path.unlink()
        for ext in ("-wal", "-shm"):
            (db_path.with_suffix(db_path.suffix + ext)).unlink(missing_ok=True)

    result = subprocess.run(
        ["python3", str(script)],
        cwd=project_root(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"create_db.py failed:\n{result.stderr}"
    assert db_path.exists(), f"DB not created: {db_path}"
    return db_path


def _register_tenant(db_path: Path) -> str:
    """Register tenant on data-service with rewrite. Returns tenant_id."""
    tid = f"e2e-llm-{uuid.uuid4().hex[:8]}"
    base = data_service_url()
    h = admin_headers()

    # Register with just DSN
    config = {"data_source": {"driver": "sqlite", "dsn": str(db_path), "read_only": True}}
    resp = requests.post(
        f"{base}/admin/tenants",
        json={"id": tid, "config": config},
        headers=h,
        timeout=10,
    )
    if resp.status_code == 409:
        requests.delete(f"{base}/admin/tenants/{tid}", headers=h, timeout=10)
        resp = requests.post(
            f"{base}/admin/tenants",
            json={"id": tid, "config": config},
            headers=h,
            timeout=10,
        )
    assert resp.status_code in (200, 201), f"Register: {resp.status_code} {resp.text[:200]}"

    # Rewrite (introspect → generate config)
    resp = requests.post(
        f"{base}/admin/config/rewrite",
        headers={"X-Tenant-ID": tid, **h},
        timeout=30,
    )
    assert resp.status_code == 200, f"Rewrite: {resp.status_code} {resp.text[:200]}"

    return tid


def _ensure_agent(agent_name: str, tid: str) -> None:
    """Create or update agent to use the given tenant."""
    h = admin_headers()
    api = api_service_url()

    # Delete if exists
    requests.delete(f"{api}/api/agents/{agent_name}", headers=h, timeout=10)

    payload = {
        "name": agent_name,
        "tenant_ids": [tid],
        "provider_priority": ["polza"],
        "llm_config": {
            "model": _LLM_MODEL,
            "provider": "polza",
            "api_key": _LLM_KEY,
            "api_base": _LLM_API_BASE,
            "system_prompt": (
                "You are a car parts shop assistant. "
                "Use the available data tools to answer questions about auto parts. "
                "Call schema_{entity}() first to discover the data structure."
            ),
        },
    }
    resp = requests.post(f"{api}/api/agents", json=payload, headers=h, timeout=10)
    assert resp.status_code in (200, 201), f"Create agent: {resp.status_code} {resp.text[:200]}"


def _check_mcp_accessible(tid: str) -> list[str]:
    """Verify MCP gateway has tools for this tenant. Returns tool names."""
    resp = requests.get(
        f"{data_service_url()}/mcp/manifest",
        headers={"X-Tenant-ID": tid},
        timeout=10,
    )
    assert resp.status_code == 200, f"MCP manifest: {resp.status_code}"

    tools = resp.json()
    tools_list = tools.get("mcp_tools", tools.get("tools", []))
    names = [t.get("name") for t in tools_list]

    # Must have grep/filter/schema
    grep_tools = [n for n in names if n.startswith("grep_")]
    filter_tools = [n for n in names if n.startswith("filter_")]
    schema_tools = [n for n in names if n.startswith("schema_")]

    assert len(grep_tools) > 0, f"No grep_* tools! All: {names}"
    assert len(filter_tools) > 0, f"No filter_* tools! All: {names}"
    assert len(schema_tools) > 0, f"No schema_* tools! All: {names}"

    return names


def _chat(
    agent_name: str, tid: str, message: str, idle_timeout: int = 20
) -> dict:
    """Send a chat message via SSE, parse all events. Returns structured result."""
    import socket as _socket

    session_id = f"e2e-llm-{uuid.uuid4().hex[:8]}"

    resp = requests.post(
        f"{api_service_url()}/api/chat/{agent_name}",
        json={"message": message, "session_id": session_id},
        headers={
            "X-Tenant-ID": tid,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; HelperiumE2E/1.0)",
        },
        timeout=120,
        stream=True,
    )

    result: dict = {
        "events": [],
        "tool_calls": [],
        "tool_results": [],
        "final_text": "",
        "reasoning": "",
        "errors": [],
        "status_messages": [],
        "session_id": session_id,
        "iterations": 0,
    }

    if resp.status_code != 200:
        result["errors"].append(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return result

    # Set idle timeout on socket
    try:
        sock = getattr(
            getattr(getattr(resp.raw, "_fp", None), "fp", None), "_sock", None
        )
        if sock is not None:
            sock.settimeout(idle_timeout)
    except (AttributeError, OSError):
        pass

    try:
        for line_bytes in resp.iter_lines():
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

            if ev_type == "tool_call":
                result["tool_calls"].append(payload)
                result["iterations"] = max(result["iterations"], payload.get("iteration", 0) + 1)
            elif ev_type == "tool_result":
                result["tool_results"].append(payload)
            elif ev_type == "reasoning":
                result["reasoning"] += payload.get("text", "")
            elif ev_type == "status":
                msg = payload.get("message") or payload.get("phase", "")
                it = payload.get("iteration", "")
                entry = f"{msg}"
                if it:
                    entry = f"iteration={it} {msg}"
                result["status_messages"].append(entry)
            elif ev_type in ("token", "final"):
                result["final_text"] += payload.get("text", "")
            elif ev_type == "error":
                result["errors"].append(payload.get("text", str(payload)))
            elif ev_type == "done":
                break
    except (requests.ConnectionError, TimeoutError, _socket.timeout, _socket.error, OSError):
        if not result["events"]:
            result["errors"].append("SSE stream ended unexpectedly")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures (module-scoped — один раз на весь модуль)
# ═══════════════════════════════════════════════════════════════════════════


_agent_name = "e2e-llm-test"
_tid: str | None = None


def setup_module() -> None:
    """Setup: create tenant, configure agent, verify MCP is alive."""
    global _tid

    print("\n🔄 Setting up LLM E2E test environment...")

    # Step 1: Create DB
    print(f"  📦 Creating {_SCENARIO} database...")
    db_path = _create_tenant_db(_SCENARIO)
    print(f"     DB: {db_path} ({db_path.stat().st_size / 1024:.0f} KB)")

    # Step 2: Register tenant + rewrite
    print(f"  🏗️  Registering tenant + config rewrite...")
    _tid = _register_tenant(db_path)
    print(f"     Tenant ID: {_tid}")

    # Step 3: Create agent
    print(f"  🤖 Creating agent '{_agent_name}'...")
    _ensure_agent(_agent_name, _tid)
    print(f"     Agent ready")

    # Step 4: Verify MCP
    print(f"  🔌 Verifying MCP tools...")
    names = _check_mcp_accessible(_tid)
    grep_count = len([n for n in names if n.startswith("grep_")])
    filter_count = len([n for n in names if n.startswith("filter_")])
    schema_count = len([n for n in names if n.startswith("schema_")])
    search_count = len([n for n in names if n.startswith("search_")])
    print(f"     Total tools: {len(names)}")
    print(f"     grep_*: {grep_count}, filter_*: {filter_count}, schema_*: {schema_count}")
    print(f"     search_*: {search_count} (should be 0)")
    assert search_count == 0, f"search_* tools should not exist! Found: {search_count}"

    # Check API is healthy
    resp = requests.get(f"{api_service_url()}/health", timeout=5)
    assert resp.status_code == 200, f"api-service health: {resp.status_code}"
    print(f"     api-service healthy ✅")


def teardown_module() -> None:
    """Cleanup: remove tenant, delete agent."""
    global _tid
    h = admin_headers()

    if _tid:
        try:
            requests.delete(
                f"{data_service_url()}/admin/tenants/{_tid}",
                headers=h,
                timeout=10,
            )
            print(f"  🧹 Tenant {_tid} deleted")
        except Exception:
            pass
        _tid = None

    try:
        requests.delete(
            f"{api_service_url()}/api/agents/{_agent_name}",
            headers=h,
            timeout=10,
        )
        print(f"  🧹 Agent {_agent_name} deleted")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLLME2E:
    """LLM E2E test suite.

    Each test sends a question about auto parts and checks that LLM
    calls appropriate tools (grep_*, filter_*, schema_*).
    Each test uses a unique session_id.
    """

    def test_discovery_first_then_search(self) -> None:
        """LLM should call schema_auto_parts() first, then grep/filter.

        Prompt: 'Какие есть запчасти для BMW?'
        """
        result = _chat(_agent_name, _tid, "Какие есть запчасти для BMW?")

        self._log_result(result)
        self._assert_ok(result)

        # Should call grep_*
        tool_names = [tc.get("name", "") for tc in result["tool_calls"]]
        has_grep = any(n.startswith("grep_") for n in tool_names)
        has_filter = any(n.startswith("filter_") for n in tool_names)
        has_schema = any(n.startswith("schema_") for n in tool_names)
        has_search = any(n.startswith("search_") for n in tool_names)

        print(f"     Tools: grep={has_grep}, filter={has_filter}, schema={has_schema}, search={has_search}")
        assert has_grep or has_filter, f"No grep_*/filter_* called! Called: {tool_names}"
        assert not has_search, f"search_* called despite being removed! Called: {tool_names}"

    def test_search_by_text(self) -> None:
        """'найди глушители' → grep_auto_parts(pattern='глушитель')."""
        result = _chat(_agent_name, _tid, "Найди глушители, покажи что есть")

        self._log_result(result)
        self._assert_ok(result)

        tool_names = [tc.get("name", "") for tc in result["tool_calls"]]
        has_grep = any(n.startswith("grep_") for n in tool_names)
        has_search = any(n.startswith("search_") for n in tool_names)
        assert has_grep, f"No grep_* called! Tools: {tool_names}"
        assert not has_search, f"search_* called! {tool_names}"

    def test_filter_by_category(self) -> None:
        """'категория тормозная система' → filter_auto_parts()."""
        result = _chat(_agent_name, _tid, "Покажи запчасти из категории тормозная система")

        self._log_result(result)
        self._assert_ok(result)

        tool_names = [tc.get("name", "") for tc in result["tool_calls"]]
        has_search = any(n.startswith("search_") for n in tool_names)
        assert not has_search, f"search_* called! {tool_names}"

    def test_multiturn_conversation(self) -> None:
        """Два вопроса в одной сессии — проверка history."""

        # First question
        result1 = _chat(_agent_name, _tid, "Сколько всего запчастей в каталоге?")
        self._log_result(result1)

        # Second question (new session)
        result2 = _chat(_agent_name, _tid, "А сколько товаров дороже 10000 рублей?")
        self._log_result(result2)

        # Both should have tool calls
        assert len(result1["tool_calls"]) > 0 or result1["final_text"], f"First query failed: {result1.get('errors',[])}"
        assert len(result2["tool_calls"]) > 0 or result2["final_text"], f"Second query failed: {result2.get('errors',[])}"

        # No search_* in either
        for name, r in [("first", result1), ("second", result2)]:
            bad = [tc.get("name") for tc in r["tool_calls"] if tc.get("name", "").startswith("search_")]
            assert len(bad) == 0, f"{name} query used search_*: {bad}"

    # ── helpers ──

    @staticmethod
    def _log_result(result: dict) -> None:
        """Full verbose log: all events, reasoning, iterations, backlog reference."""
        import urllib.request

        sid = result.get("session_id", "?")
        tc = result.get("tool_calls", [])
        tr = result.get("tool_results", [])
        err = result.get("errors", [])
        final = result.get("final_text", "")
        reasoning = result.get("reasoning", "")
        status = result.get("status_messages", [])
        iterations = result.get("iterations", 0)

        # ── SSE events log ──
        print(f"\n{'='*70}")
        print(f"  🆔 Session: {sid}")
        print(f"  🔄 Iterations: {iterations}")
        print(f"  📊 Pipeline events: {len(result['events'])}")

        # Status messages (empty rounds, retries)
        if status:
            print(f"  \n  📋 Status flow:")
            for s in status:
                print(f"     {s}")

        # Tool calls with iteration marks
        if tc:
            print(f"  \n  🛠️  Tool calls ({len(tc)}):")
            for i, t in enumerate(tc):
                name = t.get("name", "?")
                args = t.get("arguments", {})
                disp = t.get("display_name", "")
                args_str = json.dumps(args, ensure_ascii=False)
                extra = f" (display: {disp})" if disp and disp != name else ""
                print(f"     [{i}] {name}({args_str[:200]}){extra}")

        # Tool results (truncated content)
        if tr:
            print(f"  \n  📦 Tool results ({len(tr)}):")
            for i, e in enumerate(tr):
                is_err = e.get("isError", False)
                tag = "❌" if is_err else "✅"
                content = (e.get("result", "") or "")[:150]
                print(f"     [{i}] {tag} {e.get('name', '?')}: {content}")

        # Reasoning (model thinking)
        if reasoning:
            print(f"  \n  🧠 Reasoning:")
            for line in reasoning.strip().split("\n"):
                if line.strip():
                    print(f"     {line[:200]}")

        # Errors
        if err:
            print(f"  \n  ❌ Errors ({len(err)}):")
            for e in err[:5]:
                print(f"     {e[:300]}")

        # Final text
        if final:
            print(f"  \n  💬 Final answer ({len(final)} chars):")
            for line in final.strip().split("\n")[:15]:
                if line.strip():
                    print(f"     {line[:200]}")

        # ── Backlog (LLM call trace from disk) ──
        backlog_path = _backlog_path(sid)
        if not backlog_path or not backlog_path.exists():
            # Try alternate naming: agent prefix
            alt = Path("backlog") / f"agent_{_agent_name}_{sid.split(':')[-1] if ':' in sid else sid}.jsonl"
            backlog_path = alt

        if backlog_path and backlog_path.exists():
            _print_backlog(backlog_path)

    @staticmethod
    def _assert_ok(result: dict) -> None:
        """Basic assertions for a successful LLM turn."""
        errors = result.get("errors", [])
        tool_calls = result.get("tool_calls", [])
        final = result.get("final_text", "")

        # Must have either tool calls or final answer
        has_tools = len(tool_calls) > 0
        has_response = bool(final.strip())

        if errors and not has_tools and not has_response:
            pytest.fail(f"LLM pipeline failed: {errors}")

        # No search_* tools
        bad = [tc for tc in tool_calls if tc.get("name", "").startswith("search_")]
        assert len(bad) == 0, f"search_* tools still used: {[b.get('name') for b in bad]}"
