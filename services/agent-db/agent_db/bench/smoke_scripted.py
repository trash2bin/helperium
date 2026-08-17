"""End-to-end smoke of the Core Benchmark WITHOUT a real LLM.

Brings up a dedicated api-service with ScriptedLLMProvider (USE_SCRIPTED_LLM=1)
on a free port, registers an agent bound to the autoparts tenant (PG),
runs a few benchmark cases through the real CLI pipeline
(SSE -> final_text + backlog -> evaluator -> report), then tears down.

This proves the bench works end-to-end on the live stack with zero LLM cost.
"""

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # repo root (helperium/)
sys.path.insert(0, str(ROOT / "services/agent-db"))  # for tests.e2e package
sys.path.insert(0, str(ROOT))  # for helperium root imports

from tests.e2e.helpers import (  # noqa: E402
    find_free_port,
    wait_for_health,
)

# ── Scripted LLM: chain that mimics a real agent on autoparts ─────────────
# Round 1: find the product by article (filter) -> round 2: db_get(id) -> final
# значения синхронизированы с cases/autoparts.json (seed=42):
# EXT-01392 → price 2751 (не 3064, обновилось в ревизии кейсов).
SCRIPT = [
    {"content": "Найду товар по артикулу.",
     "tool_calls": [{"name": "filter_catalog_product", "arguments": {"article": "EXT-01392"}}],
     "delay_ms": 50},
    {"content": "Возьму детали.",
     "tool_calls": [{"name": "db_get", "arguments": {"entity": "catalog_product", "id": 392}}],
     "delay_ms": 50},
    {"content": "Артикул EXT-01392 стоит 2751 рубль. В наличии.",
     "delay_ms": 50},
]


def main() -> int:
    data_dir = Path("/tmp/bench-scripted-smoke")
    data_dir.mkdir(exist_ok=True)
    script_path = data_dir / "script.jsonl"
    with open(script_path, "w", encoding="utf-8") as f:
        for r in SCRIPT:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Отдельный скрипт для absence-кейса: filter вернёт [] -> отказ
    script_absent = data_dir / "script_absent.jsonl"
    with open(script_absent, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "content": "Поищу артикул.",
            "tool_calls": [{"name": "filter_catalog_product", "arguments": {"article": "FAKE-ARTICLE-999"}}],
            "delay_ms": 50,
        }, ensure_ascii=False) + "\n")
        f.write(json.dumps({
            "content": "Такого артикула не найдено в каталоге.",
            "delay_ms": 50,
        }, ensure_ascii=False) + "\n")

    port = find_free_port()
    api_url = f"http://127.0.0.1:{port}"
    log_path = data_dir / "api.log"

    env = os.environ.copy()
    env["USE_SCRIPTED_LLM"] = "1"
    env["SCRIPTED_LLM_PATH"] = str(script_path)
    env["ADMIN_TOKEN"] = os.environ.get("ADMIN_TOKEN", "secret")
    env["MCP_GATEWAY_URL"] = "http://127.0.0.1:8083"
    env["MCP_STREAMABLE_HTTP_URL"] = "http://127.0.0.1:8083/mcp"
    env["DATA_SERVICE_URL"] = "http://127.0.0.1:8084"
    env["DEMO_SESSION_DB_PATH"] = str(data_dir / "session.db")
    env["BACKLOG_DIR"] = str(data_dir / "backlog")
    env["API_PORT"] = str(port)
    env["LISTEN_ADDR"] = f"127.0.0.1:{port}"
    env["LOG_LEVEL"] = "info"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api_service.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
        cwd=str(ROOT / "services" / "api-service" / "src"),
        env=env,
        stdout=open(log_path, "w", buffering=1),
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_for_health(api_url, timeout=30):
            print(f"FAIL: api-service not healthy. Log:\n{log_path.read_text()[-2000:]}")
            return 1

        # Create agent bound to autoparts tenant
        import requests
        agent_name = f"bench-agent-{uuid.uuid4().hex[:6]}"
        payload = {
            "name": agent_name,
            "tenant_ids": ["autoparts"],
            "llm_config": {
                "model": "scripted/test",
                "provider": "openai",
                "api_key": "test-key",
                "api_base": "https://test.local",
                "system_prompt": "Ты — консультант автозапчастей. Используй инструменты.",
            },
        }
        r = requests.post(f"{api_url}/api/agents", json=payload,
                          headers={"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}, timeout=10)
        print(f"agent create: {r.status_code}")
        if r.status_code not in (200, 201):
            print(r.text[:300])
            return 1

        # Run the bench CLI on a subset of cases
        from agent_db.bench.cli import _load_cases
        from agent_db.bench.runner import BenchmarkRunner
        from agent_db.bench.evaluator import DeterministicEvaluator
        from agent_db.bench.report import aggregate_report, print_report

        cases = [c for c in _load_cases(ROOT / "services" / "agent-db" / "agent_db" / "bench" / "cases" / "autoparts.json")
                 if c.id in ("product-lookup-article-001", "product-count-bosch-available-001", "product-absence-001")]
        print(f"Running {len(cases)} cases against {api_url} agent={agent_name}")

        runner = BenchmarkRunner(api_url, agent_name, "autoparts",
                                 admin_token=env["ADMIN_TOKEN"], backlog_dir=str(data_dir / "backlog"),
                                 bench_log_dir=str(data_dir / "bench-backlog"))
        evaluator = DeterministicEvaluator()

        runs, evals = [], []
        for i, c in enumerate(cases):
            # Абсент-кейс гоняем на скрипте-отказе (перезапуск сервера)
            if c.id == "product-absence-001" and i > 0:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                env["SCRIPTED_LLM_PATH"] = str(script_absent)
                proc = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "api_service.server:app",
                     "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
                    cwd=str(ROOT / "services" / "api-service" / "src"),
                    env=env,
                    stdout=open(log_path, "w", buffering=1),
                    stderr=subprocess.STDOUT,
                )
                if not wait_for_health(api_url, timeout=30):
                    print(f"FAIL: restart not healthy. Log:\n{log_path.read_text()[-2000:]}")
                    return 1
            run = runner.run_case(c.question)
            ev = evaluator.evaluate(c, run)
            runs.append(run)
            evals.append(ev)
            print(f"  [{c.id}] tool={ev.tool_ok} retrieval={ev.retrieval_ok} "
                  f"answer={ev.answer_ok} halluc={ev.hallucination} refusal={ev.refusal_ok} "
                  f"final={run.final_text[:60]!r}")
            print(f"    backlog: outcome={run.backlog.outcome if run.backlog else None} "
                  f"tool_calls={run.backlog.tool_calls_count if run.backlog else None} "
                  f"tokens={run.backlog.total_tokens if run.backlog else None} "
                  f"duration={run.backlog.duration_ms if run.backlog else None}")

        report = aggregate_report(cases, runs, evals)
        print()
        print(print_report(report))
        print()
        # Assertions: first case (article lookup) must pass with scripted chain
        ok = all(ev.success for ev in evals)
        print(f"SMOKE {'PASS' if ok else 'FAIL'} — all cases success={ok}")
        return 0 if ok else 1
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
