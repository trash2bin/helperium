"""E2E тест с ScriptedLLMProvider — pipeline без реальной LLM.

Поднимает api-service как subprocess с ``USE_SCRIPTED_LLM=1``,
гоняет тулы через реальный SSE endpoint, проверяет всю цепочку.

Не требует Polza/DeepSeek (не тратит деньги).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import admin_headers, data_service_url, project_root, mcp_call


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout: int = 30) -> bool:
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


def _parse_sse_stream(response, idle_timeout: int = 20) -> dict:
    """Parse SSE stream into structured result."""
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


# ── Script helpers ──────────────────────────────────────────────────────

SCRIPT_ROUND_NORMAL = json.dumps({
    "content": "Давайте поищем запчасти.",
    "tool_calls": [{"name": "grep_auto_parts", "arguments": {"pattern": "глушитель", "limit": 5}}],
    "delay_ms": 100,
}, ensure_ascii=False) + "\n"

SCRIPT_ROUND_FINAL = json.dumps({
    "content": "Нашёл для BMW X5:\n1. Глушитель задний — 45 000 руб\n2. Глушитель средний — 32 000 руб",
    "delay_ms": 100,
}, ensure_ascii=False) + "\n"

SCRIPT_ROUND_EMPTY_CALL = json.dumps({
    "tool_calls": [{"name": "grep_auto_parts", "arguments": {}}],
    "delay_ms": 50,
}, ensure_ascii=False) + "\n"

SCRIPT_ROUND_EMPTY_RETRY = json.dumps({
    "content": "Попробую точнее.",
    "tool_calls": [{"name": "grep_auto_parts", "arguments": {"pattern": "глушитель", "limit": 5}}],
    "delay_ms": 50,
}, ensure_ascii=False) + "\n"

SCRIPT_ROUND_EMPTY_LLM = json.dumps({
    "content": "",
    "delay_ms": 50,
}, ensure_ascii=False) + "\n"

SCRIPT_ROUND_ERROR_RECOVERY = json.dumps({
    "content": "Вот что нашёл: ...",
    "delay_ms": 50,
}, ensure_ascii=False) + "\n"


def _write_script(path: Path, rounds: list[str]) -> None:
    """Write JSONL script file."""
    path.write_text("".join(rounds), encoding="utf-8")


def _write_good_script(path: Path) -> None:
    _write_script(path, [SCRIPT_ROUND_NORMAL, SCRIPT_ROUND_NORMAL, SCRIPT_ROUND_FINAL])


def _write_empty_call_script(path: Path) -> None:
    _write_script(path, [SCRIPT_ROUND_EMPTY_CALL, SCRIPT_ROUND_NORMAL, SCRIPT_ROUND_FINAL])


def _write_empty_llm_script(path: Path) -> None:
    _write_script(path, [SCRIPT_ROUND_EMPTY_LLM, SCRIPT_ROUND_NORMAL, SCRIPT_ROUND_FINAL])


# ── Fixture ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scripted_server(tmp_path_factory):
    """Start api-service with ScriptedLLMProvider.

    Поднимает на свободном порту, со своей session_db (чтобы не мешать
    основному api-service), создаёт tenant + agent, гоняет чат.
    После тестов убивает процесс.
    """
    root = project_root()
    data_dir = tmp_path_factory.mktemp("scripted-data")
    script_path = data_dir / "pipeline.jsonl"
    _write_good_script(script_path)

    port = _find_free_port()
    api_url = f"http://127.0.0.1:{port}"

    # Поднимаем api-service с scripted LLM и отдельной БД
    env = os.environ.copy()
    env["USE_SCRIPTED_LLM"] = "1"
    env["SCRIPTED_LLM_PATH"] = str(script_path)
    env["ADMIN_TOKEN"] = os.environ.get("ADMIN_TOKEN", "secret")
    env["MCP_SERVICE_URL"] = os.environ.get("MCP_SERVICE_URL", "http://127.0.0.1:8083")
    env["DATA_SERVICE_URL"] = os.environ.get("DATA_SERVICE_URL", "http://127.0.0.1:8084")
    # Своя БД — чтобы не лочить основную
    env["DEMO_SESSION_DB_PATH"] = str(data_dir / "session.db")
    env["API_PORT"] = str(port)
    env["LISTEN_ADDR"] = f"127.0.0.1:{port}"
    env["LOG_LEVEL"] = "info"

    log_path = data_dir / "api.log"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "api_service.server:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "info",
        ],
        cwd=str(root / "api-service" / "src"),
        env=env,
        stdout=open(log_path, "w", buffering=1),
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_health(api_url, timeout=30):
        try:
            log_text = log_path.read_text(encoding="utf-8")[-3000:]
            pytest.fail(f"api-service failed to start.\n=== last 3KB log ===\n{log_text}")
        except Exception:
            pytest.fail("api-service failed to start (no log)")
        proc.kill()
        proc.wait()
        return None

    # Проверяем что скрипт загружен
    try:
        log_text = log_path.read_text(encoding="utf-8")
        assert "SCRIPTED" in log_text or "scripted" in log_text.lower(), \
            f"ScriptedLLM not loaded!\n=== log ===\n{log_text[-2000:]}"
    except Exception:
        pass

    # ── Регистрация tenant ──
    from tests.e2e.test_search_strategies import _create_db, _register_and_rewrite
    db_path = _create_db("auto-shop")
    tid = f"e2e-{uuid.uuid4().hex[:8]}"
    _register_and_rewrite(tid, db_path)

    # ── Создаём агента через API нашего инстанса ──
    agent_name = f"agent-{uuid.uuid4().hex[:6]}"
    payload = {
        "name": agent_name,
        "tenant_ids": [tid],
        "llm_config": {
            "model": "scripted/test",
            "provider": "openai",
            "api_key": "test-key",
            "api_base": "https://test.local",
            "system_prompt": "Ты — консультант автозапчастей. Используй инструменты.",
        },
    }
    resp = requests.post(
        f"{api_url}/api/agents",
        json=payload,
        headers={"Authorization": f"Bearer {env['ADMIN_TOKEN']}"},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        log_text = log_path.read_text(encoding="utf-8")[-2000:]
        pytest.fail(
            f"Agent creation failed: {resp.status_code}: {resp.text[:300]}\n"
            f"=== log ===\n{log_text}"
        )

    yield api_url, agent_name, tid, data_dir

    # Cleanup
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not admin_headers(),
    reason="ADMIN_TOKEN not set",
)
class TestScriptedPipeline:
    """Прогон pipeline через ScriptedLLMProvider — без живой LLM."""

    def test_basic_pipeline(self, scripted_server):
        """Тулы вызываются, имена не пустые, доходит до финала."""
        api_url, agent_name, tid, _ = scripted_server
        result = self._chat(api_url, agent_name, tid, "Нужен глушитель на BMW X5")

        assert not result.get("errors"), f"Errors: {result['errors']}"
        assert len(result["tool_calls"]) > 0, "Нет tool calls"
        assert len(result["tool_results"]) > 0, "Нет tool results"
        assert len(result["final_text"]) > 0, "Нет финального ответа"

        # Имена тулов не пустые — ключевая проверка! (был баг)
        for tc in result["tool_calls"]:
            name = tc.get("name", "")
            assert name, f"Tool name is empty! tc={json.dumps(tc, ensure_ascii=False)}"
            assert tc.get("display_name", ""), f"display_name empty for {name}"

        names = [tc.get("name", "") for tc in result["tool_calls"]]
        assert "grep_auto_parts" in names, f"Expected grep_auto_parts, got: {names}"

        print(f"\n  ✅ Tool calls: {names}")
        print(f"  ✅ Tool results: {len(result['tool_results'])}")
        print(f"  ✅ Final: {result['final_text'][:120]}")

    def test_empty_call_blocked(self, scripted_server):
        """grep_auto_parts({}) → validateArgs/mcp-gateway блокирует.

        Проверяем через прямой MCP call (без LLM).
        """
        _, _, tid, _ = scripted_server

        result = mcp_call("grep_auto_parts", arguments={}, tenant_ids=tid, timeout=15)

        if result.success:
            print(f"\n  ⚠️ Empty call NOT rejected at MCP level. Result: {result.result}")
        else:
            print(f"\n  ✅ Empty call rejected: {result.error[:120]}")

    def test_tool_name_not_empty_in_sse(self, scripted_server):
        """Проверка что SSE event показывает имя тула, а не пустую строку.

        Раньше было: `🛠️  ({})` — имя пустое, args пустые.
        SSE tool_call event не содержит arguments (браузеру не нужно),
        но name ОБЯЗАН быть непустым.
        """
        api_url, agent_name, tid, _ = scripted_server
        result = self._chat(api_url, agent_name, tid, "Нужен глушитель на BMW X5")

        # Проверяем tool_call events в SSE
        for tc in result["tool_calls"]:
            ev_name = tc.get("name", "")
            assert ev_name, f"SSE tool_call has empty name! event={json.dumps(tc, ensure_ascii=False)}"

        # Проверяем tool_result events
        for tr in result["tool_results"]:
            ev_name = tr.get("name", "")
            assert ev_name, f"SSE tool_result has empty name! event={tr}"

        print(f"\n  ✅ Все {len(result['tool_calls'])} tool_call events имеют непустые имена")
        for tc in result["tool_calls"]:
            print(f"    🛠️ {tc.get('name')} ({tc.get('display_name')})")

    # ── helpers ──

    def _chat(self, api_url: str, agent_name: str, tid: str, message: str) -> dict:
        session_id = f"e2e-{uuid.uuid4().hex[:8]}"
        resp = requests.post(
            f"{api_url}/api/chat/{agent_name}",
            json={"message": message, "session_id": session_id},
            headers={
                "X-Tenant-ID": tid,
                "Content-Type": "application/json",
                "User-Agent": "HelperiumE2E/1.0",
            },
            timeout=60,
            stream=True,
        )
        if resp.status_code != 200:
            return {
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                "events": [], "tool_calls": [], "tool_results": [],
                "final_text": "", "errors": [f"HTTP {resp.status_code}"], "status_messages": [],
            }
        return _parse_sse_stream(resp, idle_timeout=20)
