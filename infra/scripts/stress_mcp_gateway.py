#!/usr/bin/env python3
"""
MCP Gateway Stress Test
=========================
Запуск:  uv run python3 scripts/stress_mcp_gateway.py [--rps 50] [--duration 30] [--spike]
Результат: /tmp/mcp_stress_results.json

Фазы:
  1. Warm-up: 5 сек, 10 RPS
  2. Steady:  N RPS, длительность
  3. Spike:   пачка 200 concurrent
  4. Cooldown: замер метрик после нагрузки
"""

import json
import time
import statistics
import urllib.request
import urllib.error
import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

MCP = os.environ.get("MCP_URL", "http://localhost:8083")
DATA = os.environ.get("DATA_URL", "http://localhost:8084")
TENANT = os.environ.get("TENANT", "autoparts")
TOKEN = os.environ.get("ADMIN_TOKEN", "secret")

RPS = int(os.environ.get("STRESS_RPS", "50"))
DURATION = int(os.environ.get("STRESS_DURATION", "30"))

results = {
    "config": {
        "rps": RPS,
        "duration": DURATION,
        "mcp": MCP,
        "data": DATA,
        "tenant": TENANT,
    },
    "tool_calls": [],
    "errors": [],
    "phases": {},
    "bottlenecks": [],
    "recommendations": [],
}

tool_pool = [
    ("find_catalog_brand", {"name": "Bosch"}),
    ("find_catalog_brand", {"name": "Febi"}),
    ("find_catalog_brand", {"name": "TRW"}),
    ("find_catalog_product", {"name": "Bosch"}),
    ("get_catalog_product", {"id": 1}),
    ("get_catalog_product", {"id": 100}),
    ("find_catalog_category", {"name": "Тормозная"}),
    ("get_catalog_brand", {"id": 1}),
    ("find_catalog_order", {"name": "Иван"}),
]

# Shared counters
rate_limit_count = 0
error_count = 0
lock = threading.Lock()

headers = {"Content-Type": "application/json", "X-Tenant-ID": TENANT}
latencies = []
stop_flag = threading.Event()


def do_tool_call(tool, args, session_id):
    global rate_limit_count, error_count

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": hash(session_id) % 100000,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    ).encode()

    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            f"{MCP}/mcp/message?sessionId={session_id}",
            data=body,
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        elapsed_ms = (time.perf_counter() - start) * 1000
        status = resp.status

        with lock:
            if status == 429:
                rate_limit_count += 1
            latencies.append(elapsed_ms)

        return {"tool": tool, "ms": elapsed_ms, "status": status, "size": len(data)}

    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - start) * 1000
        with lock:
            if e.code == 429:
                rate_limit_count += 1
            error_count += 1
        return {"tool": tool, "ms": elapsed, "status": e.code, "error": str(e)[:100]}

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        with lock:
            error_count += 1
        return {"tool": tool, "ms": elapsed, "status": 0, "error": str(e)[:100]}


def worker(worker_id, result_list):
    """Continuously makes tool calls until stop_flag is set."""
    i = 0
    while not stop_flag.is_set():
        tool, args = tool_pool[i % len(tool_pool)]
        sid = f"stress-{worker_id}-{i}"
        r = do_tool_call(tool, args, sid)
        result_list.append(r)
        i += 1

        if stop_flag.is_set():
            break


def run_phase(name, rps, duration_sec):
    """Run a phase: N workers making requests at target RPS."""
    global latencies, error_count, rate_limit_count
    latencies = []
    error_count = 0
    rate_limit_count = 0

    results_list = []
    n_workers = min(rps, 200)  # cap at 200 workers
    # Each worker makes ~1 req/sec for target RPS

    print(f"\n{'=' * 60}")
    print(f"📊 Phase: {name}")
    print(f"    Target: {rps} RPS, Duration: {duration_sec}s, Workers: {n_workers}")
    print(f"{'=' * 60}")

    stop_flag.clear()

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(worker, i, results_list): i for i in range(n_workers)}

        # Run for duration
        time.sleep(duration_sec)
        stop_flag.set()

        # Wait for all to finish
        for f in as_completed(futures):
            try:
                f.result(timeout=30)
            except Exception as e:
                with lock:
                    error_count += 1

    total = len(results_list)
    duration = duration_sec

    if len(latencies) > 0:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        p999 = latencies[int(len(latencies) * 0.999)]
        actual_rps = total / duration

        print(f"\n    Results ({total} calls in {duration}s):")
        print(f"    Actual RPS:  {actual_rps:.1f}")
        print(
            f"    Errors:      {error_count} ({error_count / max(total, 1) * 100:.1f}%)"
        )
        print(f"    Rate limited: {rate_limit_count}")
        print(f"    Latency:")
        print(f"      min:   {min(latencies):.1f}ms")
        print(f"      p50:   {p50:.1f}ms")
        print(f"      avg:   {statistics.mean(latencies):.1f}ms")
        print(f"      p95:   {p95:.1f}ms")
        print(f"      p99:   {p99:.1f}ms")
        print(f"      p999:  {p999:.1f}ms")
        print(f"      max:   {max(latencies):.1f}ms")

        results["phases"][name] = {
            "total_calls": total,
            "actual_rps": round(actual_rps, 1),
            "target_rps": rps,
            "errors": error_count,
            "error_pct": round(error_count / max(total, 1) * 100, 1),
            "rate_limited": rate_limit_count,
            "latency_ms": {
                "min": round(min(latencies), 1),
                "p50": round(p50, 1),
                "avg": round(statistics.mean(latencies), 1),
                "p95": round(p95, 1),
                "p99": round(p99, 1),
                "p999": round(p999, 1),
                "max": round(max(latencies), 1),
            },
        }

        # Detect bottlenecks from this phase
        if p95 > 100:
            results["bottlenecks"].append(
                {
                    "phase": name,
                    "metric": "p95 > 100ms",
                    "value_ms": p95,
                    "reason": "Latency spikes under concurrent load — suspect connection pool or serialized manifest fetch",
                }
            )
        if total < rps * duration * 0.5:
            results["bottlenecks"].append(
                {
                    "phase": name,
                    "metric": "throughput < 50% of target",
                    "value": f"{actual_rps:.0f}/{rps} RPS",
                    "reason": "Workers не поспевают за target RPS — thread pool упирается в блокировку",
                }
            )
        if rate_limit_count > 0:
            results["bottlenecks"].append(
                {
                    "phase": name,
                    "metric": "rate limited",
                    "value": rate_limit_count,
                    "reason": "Rate limiter сработал — MCP_RATE_LIMIT_RPS надо поднять",
                }
            )
    else:
        print("\n    ❌ No successful calls!")

    return results_list


def phase_warmup():
    run_phase("warmup", 10, 5)


def phase_steady():
    run_phase(f"steady-{RPS}rps", RPS, DURATION)


def phase_spike():
    """Spike: all workers fire at once."""
    global latencies, error_count, rate_limit_count
    latencies = []
    error_count = 0
    rate_limit_count = 0

    n_workers = 200
    results_list = []
    print(f"\n{'=' * 60}")
    print(f"📊 Phase: SPIKE ({n_workers} concurrent calls)")
    print(f"{'=' * 60}")

    stop_flag.clear()
    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {}
        for i in range(n_workers):
            tool, args = tool_pool[i % len(tool_pool)]
            sid = f"spike-{i}"
            futures[pool.submit(do_tool_call, tool, args, sid)] = i

        for f in as_completed(futures):
            try:
                r = f.result(timeout=60)
                results_list.append(r)
            except Exception:
                with lock:
                    error_count += 1

    elapsed = time.perf_counter() - start
    total = len(results_list)
    actual_rps = total / elapsed if elapsed > 0 else 0

    print(f"\n    {total} calls in {elapsed:.1f}s ({actual_rps:.0f} RPS)")

    if len(latencies) > 0:
        latencies.sort()
        print(f"    Errors: {error_count}, Rate limited: {rate_limit_count}")
        print(f"    Latency:")
        print(f"      min:   {min(latencies):.1f}ms")
        print(f"      p50:   {statistics.median(latencies):.1f}ms")
        print(f"      p95:   {latencies[int(len(latencies) * 0.95)]:.1f}ms")
        print(f"      p99:   {latencies[int(len(latencies) * 0.99)]:.1f}ms")
        print(f"      max:   {max(latencies):.1f}ms")

    results["phases"]["spike"] = {
        "total_calls": total,
        "actual_rps": round(actual_rps, 1),
        "errors": error_count,
        "error_pct": round(error_count / max(total, 1) * 100, 1),
        "rate_limited": rate_limit_count,
        "elapsed_sec": round(elapsed, 1),
    }


def check_post_stress():
    """Check MCP and data-service health after stress. Read metrics."""
    print(f"\n{'=' * 60}")
    print("📊 Post-stress: service health")
    print(f"{'=' * 60}")

    for svc, url in [("MCP", MCP), ("DATA", DATA)]:
        try:
            start = time.perf_counter()
            r = urllib.request.urlopen(f"{url}/health", timeout=10)
            ms = (time.perf_counter() - start) * 1000
            print(f"    {svc}: {r.status} ({ms:.1f}ms)")
            results[f"post_stress_{svc.lower()}_health_ms"] = round(ms, 1)
        except Exception as e:
            print(f"    {svc}: ❌ {e}")
            results[f"post_stress_{svc.lower()}_error"] = str(e)

    for svc, url in [("MCP", MCP), ("DATA", DATA)]:
        try:
            r = urllib.request.urlopen(f"{url}/metrics", timeout=5)
            text = r.read().decode()
            for line in text.split("\n"):
                if (
                    line.startswith("mcp_rate")
                    or line.startswith("data_requests_total")
                    or line.startswith("data_db_query")
                ):
                    parts = line.split()
                    if len(parts) >= 2:
                        print(f"    {parts[0]} = {parts[1]}")
        except:
            pass


if __name__ == "__main__":
    print("=" * 60)
    print("🔬 MCP GATEWAY STRESS TEST")
    print(f"   MCP:  {MCP}  |  DATA: {DATA}")
    print(f"   RPS:  {RPS}  |  Duration: {DURATION}s")
    print(f"   Tenant: {TENANT}")
    print(f"   Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Pre-flight check
    for svc, url in [("MCP", MCP), ("DATA", DATA)]:
        try:
            r = urllib.request.urlopen(f"{url}/health", timeout=5)
            print(f"  ✅ {svc} UP ({r.status})")
        except Exception as e:
            print(f"  ❌ {svc} DOWN: {e}")
            sys.exit(1)

    # Check if rate limit is lifted
    try:
        r = urllib.request.urlopen(f"{MCP}/metrics", timeout=5)
        for line in r.read().decode().split("\n"):
            if "mcp_rate" in line and "rate_limit" not in line:
                print(f"    {line}")
    except:
        pass

    # Phases
    phase_warmup()
    phase_steady()
    phase_spike()
    check_post_stress()

    # Summary
    print("\n" + "=" * 60)
    print("📊 STRESS TEST SUMMARY")
    print("=" * 60)

    for name, phase in results["phases"].items():
        if "latency_ms" in phase:
            l = phase["latency_ms"]
            print(f"\n  {name}:")
            print(f"    Calls: {phase['total_calls']} | RPS: {phase['actual_rps']}")
            print(f"    p50={l['p50']}ms  p95={l['p95']}ms  p99={l['p99']}ms")
            print(
                f"    Errors: {phase['errors']} ({phase['error_pct']}%) | Rate-limited: {phase['rate_limited']}"
            )
        else:
            print(
                f"\n  {name}: {phase['total_calls']} calls, {phase['actual_rps']} RPS"
            )

    if results["bottlenecks"]:
        print("\n  🐌 BOTTLENECKS:")
        for b in results["bottlenecks"]:
            print(
                f"    🔴 [{b['phase']}] {b['metric']}: {b['value_ms'] if 'value_ms' in b else b.get('value', '')} — {b['reason']}"
            )
    else:
        print("\n  ✅ No bottlenecks!")

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    with open("/tmp/mcp_stress_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Full results -> /tmp/mcp_stress_results.json")
