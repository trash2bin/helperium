#!/usr/bin/env python3
"""load_gen.py — генератор фиктивной нагрузки для локальной проверки Grafana.

Создаёт постоянный трафик, чтобы панели дашборда Helperium (request rate,
tool calls, error rate, latency) ожили без реальных посетителей:

  - MCP-воркеры: держат Streamable HTTP /mcp-сессию и крутят tool calls
    (db_map / db_describe / db_search / db_filter / db_get / stats) через
    mcp-gateway;
  - HTTP-воркеры: бьют в grep-эндпоинт tenant'а data-service и /health
    api-service.

Это НЕ бенчмарк: числа не говорят ничего ни о пропускной способности,
ни о качестве ответов. Инструмент для скринов дашборда и быстрой
проверки, что метрики вообще текут.

Требует запущенный стек и зарегистрированного tenant'а (стандартный путь —
`agent-db materialize` + `agent-db register`, см. services/agent-db/README.md).
Набор тулов и аргументов по умолчанию соответствует сценарию `shop`; для
другого сценария передайте --tools или поправьте TOOLS.

Usage:
  python3 infra/scripts/load_gen.py --duration 900
  python3 infra/scripts/load_gen.py --duration 300 --workers 2 --tenant my-tenant

Переменные окружения: MCP_URL, DATA_URL, API_URL, TENANT, MCP_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Тулов surface сценария `shop` (проверено по живому манифесту data-service).
TOOLS = [
    ("db_map", {}),
    ("db_describe", {"entity": "products"}),
    ("db_search", {"entity": "products", "pattern": "{word}"}),
    ("db_filter", {"entity": "products", "price__lt": "{price}", "limit": "5"}),
    ("db_get", {"entity": "products", "id": "{pid}"}),
    ("db_get", {"entity": "orders", "id": "{oid}"}),
    ("stats", {}),
    ("db_filter", {"entity": "customers", "city__like": "%", "limit": "3"}),
]
WORDS = ["iPhone", "Футболка", "Часы", "Кофемашина", "MacBook", "Кроссовки"]
PRICES = ["500", "1000", "2000"]

_stop = threading.Event()


def render_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if v == "{word}":
            v = random.choice(WORDS)
        elif v == "{price}":
            v = random.choice(PRICES)
        elif v == "{pid}":
            v = str(random.randint(1, 4))
        elif v == "{oid}":
            v = str(random.randint(1, 2))
        out[k] = v
    return out


class McpSession:
    """Одна Streamable HTTP MCP-сессия с переинициализацией после сбоя."""

    def __init__(self, mcp_url: str, tenant: str, key: str):
        self.mcp_url = mcp_url
        self.tenant = tenant
        self.key = key
        self.sid: str | None = None
        self.rid = 0

    def _post(self, body: dict, extra_headers: dict | None = None) -> urllib.request.Request:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.key}",
            "X-Tenant-ID": self.tenant,
        }
        if extra_headers:
            headers.update(extra_headers)
        return urllib.request.Request(
            self.mcp_url, data=json.dumps(body).encode(), method="POST", headers=headers
        )

    def init(self) -> None:
        body = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "loadgen", "version": "1.0"}}}
        with urllib.request.urlopen(self._post(body), timeout=10) as r:
            self.sid = r.headers.get("Mcp-Session-Id")
            r.read()
        with urllib.request.urlopen(
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"Mcp-Session-Id": self.sid}), timeout=10) as r:
            r.read()

    def call(self, name: str, args: dict) -> None:
        self.rid += 1
        if not self.sid:
            self.init()
        body = {"jsonrpc": "2.0", "id": self.rid, "method": "tools/call",
                "params": {"name": name, "arguments": args}}
        try:
            with urllib.request.urlopen(
                self._post(body, {"Mcp-Session-Id": self.sid}), timeout=10) as r:
                r.read()
        except Exception:
            # Сессия могла протухнуть — одна повторная попытка с новой сессией.
            self.sid = None
            self.init()
            with urllib.request.urlopen(
                self._post(body, {"Mcp-Session-Id": self.sid}), timeout=10) as r:
                r.read()


def mcp_worker(sess: McpSession) -> None:
    i = 0
    while not _stop.is_set():
        name, args = TOOLS[i % len(TOOLS)]
        i += 1
        try:
            sess.call(name, render_args(args))
        except Exception:
            pass  # ошибки инструмента — тоже метрика; не роняем воркер
        time.sleep(random.uniform(0.15, 0.6))


def http_worker(data_url: str, api_url: str, tenant: str) -> None:
    while not _stop.is_set():
        try:
            q = urllib.parse.quote(random.choice(WORDS))
            req = urllib.request.Request(
                f"{data_url}/products?pattern={q}", headers={"X-Tenant-ID": tenant})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        try:
            req = urllib.request.Request(
                f"{data_url}/products/{random.randint(1, 4)}",
                headers={"X-Tenant-ID": tenant})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        try:
            urllib.request.urlopen(f"{api_url}/health", timeout=5).read()
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 1.2))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--duration", type=int, default=600, help="seconds to run (default 600)")
    p.add_argument("--workers", type=int, default=4, help="MCP workers (default 4)")
    p.add_argument("--tenant", default=os.environ.get("TENANT", "shop-load"))
    p.add_argument("--mcp-url", default=os.environ.get("MCP_URL", "http://127.0.0.1:8083/mcp"))
    p.add_argument("--data-url", default=os.environ.get("DATA_URL", "http://127.0.0.1:8084"))
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8081"))
    p.add_argument("--mcp-key", default=os.environ.get("MCP_API_KEY", "ci-mcp-token"),
                   help="must match MCP_API_KEY of the stack")
    args = p.parse_args()

    print(f"load: {args.duration}s, {args.workers} MCP workers + 2 HTTP workers, "
          f"tenant={args.tenant}")
    threads = [
        threading.Thread(target=mcp_worker,
                         args=(McpSession(args.mcp_url, args.tenant, args.mcp_key),),
                         daemon=True)
        for _ in range(args.workers)
    ]
    threads += [
        threading.Thread(target=http_worker,
                         args=(args.data_url, args.api_url, args.tenant), daemon=True)
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    t0 = time.time()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    _stop.set()
    for t in threads:
        t.join(timeout=5)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
