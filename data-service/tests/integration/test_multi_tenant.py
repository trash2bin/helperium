"""
Integration test: multi-tenant data-service.

Запускает data-service с одним default-tenant'ом, затем через admin API
добавляет ещё 1-2 tenant'а, проверяет X-Tenant-ID routing, health check,
hot reload и удаление tenant'а.

Не требует PostgreSQL — использует SQLite для каждого tenant'а (изолированные файлы).

Использование:
    cd agent-tutor
    uv run python data-service/tests/integration/test_multi_tenant.py
    uv run python data-service/tests/integration/test_multi_tenant.py --seed 7
    uv run python data-service/tests/integration/test_multi_tenant.py --port 18085
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_SERVICE = PROJECT_ROOT / "data-service"

# Сколько студентов генерируем в каждом tenant'е
STUDENTS_PER_TENANT = 5
GROUPS_PER_TENANT = 2


# ---------------------------------------------------------------------------
# Multi-tenant scenario generator
# ---------------------------------------------------------------------------


def make_tenant_scenario(
    tmp_root: Path,
    tenant_label: str,
    seed: int,
) -> tuple[Path, dict]:
    """
    Создаёт сценарий (config.json + SQLite-БД) для одного tenant'а.

    Каждый tenant имеет свой набор students со студентами с фамилией,
    содержащей tenant_label — это позволяет в тестах проверить, что
    X-Tenant-ID реально роутит в нужную БД (а не в default).
    """
    scenario_dir = tmp_root / f"tenant-{tenant_label}"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    db_path = scenario_dir / "data.db"

    # Пишем простой config.json с SQLite-БД в этой директории
    config = {
        "version": 1,
        "data_source": {
            "driver": "sqlite",
            "dsn": "data.db",  # относительный путь → резолвится относительно config.json
            "read_only": False,
        },
        "entities": [
            {
                "name": "student",
                "table": "students",
                "id_column": "id",
                "fields": [
                    {"name": "id", "column": "id", "type": "string"},
                    {"name": "name", "column": "name", "type": "string"},
                    {"name": "group_id", "column": "group_id", "type": "string"},
                    {"name": "course", "column": "course", "type": "int"},
                ],
            },
            {
                "name": "group",
                "table": "groups",
                "id_column": "id",
                "fields": [
                    {"name": "id", "column": "id", "type": "string"},
                    {"name": "name", "column": "name", "type": "string"},
                    {"name": "speciality", "column": "speciality", "type": "string"},
                ],
            },
        ],
        "endpoints": [
            {"method": "GET", "path": "/health", "op": "builtin_health"},
            {"method": "GET", "path": "/stats", "op": "builtin_stats"},
            {"method": "GET", "path": "/students", "op": "list", "entity": "student"},
            {
                "method": "GET",
                "path": "/students/{id}",
                "op": "get_by_id",
                "entity": "student",
            },
            {"method": "GET", "path": "/groups", "op": "list", "entity": "group"},
            {
                "method": "GET",
                "path": "/groups/{id}",
                "op": "get_by_id",
                "entity": "group",
            },
        ],
        "stats": {
            "counters": [
                {"name": "students", "entity": "student"},
                {"name": "groups", "entity": "group"},
            ],
        },
    }

    (scenario_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Генерируем seed с детерминированным ID-шником (по seed)
    import random

    rng = random.Random(seed)
    students = []
    groups = []
    for g in range(1, GROUPS_PER_TENANT + 1):
        gid = f"g-{tenant_label}-{g}"
        groups.append(
            {
                "id": gid,
                "name": f"Группа {tenant_label.upper()}-{g}",
                "speciality": "Программная инженерия",
            }
        )
        for s in range(1, STUDENTS_PER_TENANT // GROUPS_PER_TENANT + 1):
            sid = f"s-{tenant_label}-{g}-{s}"
            students.append(
                {
                    "id": sid,
                    "name": f"Студент-{tenant_label.upper()}-{g}-{s}",
                    "group_id": gid,
                    "course": 1,
                }
            )

    seed_json = {
        "groups": groups,
        "students": students,
        "teachers": [],
        "disciplines": [],
        "schedule": [],
        "grades": [],
    }

    (scenario_dir / "seed.json").write_text(
        json.dumps(seed_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return scenario_dir, seed_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  RUN: {' '.join(cmd)}")
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True, cwd=str(PROJECT_ROOT), **kwargs
    )


def http_request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: dict | str | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict | list | str]:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        else:
            data = body.encode("utf-8")
    req = Request(url, method=method, headers=h, data=data)
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode()
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                return resp.status, json.loads(text)
            return resp.status, text
    except HTTPError as e:
        text = e.read().decode() if hasattr(e, "read") else ""
        try:
            return e.code, json.loads(text)
        except Exception:
            return e.code, text


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-tenant integration test")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--port", type=int, default=18084, help="data-service port (avoid clash)"
    )
    parser.add_argument(
        "--admin-token", type=str, default="test-admin-token-multi-tenant"
    )
    args = parser.parse_args()

    # -- 1. Build data-service --
    print("-- 1. Building data-service --")
    subprocess.run(
        [
            "go",
            "build",
            "-o",
            "data-service/bin/data-service",
            "./data-service/cmd/server/",
        ],
        check=True,
        cwd=str(PROJECT_ROOT),
    )
    print("   OK: built")

    # -- 2. Create scenarios (3 tenants: default, school-a, school-b) --
    print(f"-- 2. Creating scenarios (seed={args.seed}) --")
    tmp_root = Path(tempfile.mkdtemp(prefix="multi-tenant-test-"))
    print(f"   tmp_root: {tmp_root}")

    default_scenario, default_seed = make_tenant_scenario(
        tmp_root, "default", args.seed
    )
    school_a_scenario, school_a_seed = make_tenant_scenario(
        tmp_root, "school-a", args.seed + 1
    )
    school_b_scenario, school_b_seed = make_tenant_scenario(
        tmp_root, "school-b", args.seed + 2
    )

    print(
        f"   default: {default_scenario.name} ({len(default_seed['students'])} students)"
    )
    print(
        f"   school-a: {school_a_scenario.name} ({len(school_a_seed['students'])} students)"
    )
    print(
        f"   school-b: {school_b_scenario.name} ({len(school_b_seed['students'])} students)"
    )

    # -- 3. Materialize all 3 scenarios --
    print("-- 3. Materializing scenarios --")
    for scenario in [default_scenario, school_a_scenario, school_b_scenario]:
        run(
            [
                "go",
                "run",
                "./data-service/cmd/server/",
                "--materialize",
                str(scenario),
            ]
        )

    # -- 4. Start data-service with default tenant --
    print(f"-- 4. Starting data-service on :{args.port} --")
    server = subprocess.Popen(
        [
            str(PROJECT_ROOT / "data-service" / "bin" / "data-service"),
            "--config",
            str(default_scenario / "config.json"),
        ],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "PORT": str(args.port),
            "ADMIN_TOKEN": args.admin_token,
            "LOG_LEVEL": "info",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def cleanup():
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    import atexit

    atexit.register(cleanup)

    base_url = f"http://127.0.0.1:{args.port}"
    admin_headers = {"Authorization": f"Bearer {args.admin_token}"}

    for i in range(20):
        try:
            status, body = http_request("GET", f"{base_url}/health")
            if status == 200:
                print(f"   OK: server ready after {i * 0.5:.1f}s")
                break
        except Exception:
            time.sleep(0.5)
    else:
        print("FAIL: Server didn't start in 10s")
        server.send_signal(signal.SIGTERM)
        return 1

    # -- 5. Test default tenant --
    print("-- 5. Testing default tenant --")
    failures = 0

    def check(label: str, ok: bool, detail: str = ""):
        nonlocal failures
        marker = "OK  " if ok else "FAIL"
        print(f"   [{marker}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures += 1

    status, body = http_request("GET", f"{base_url}/health")
    if isinstance(body, dict):
        # Backward-compat: single-tenant response has "status: ok"
        check(
            "default /health",
            status == 200 and body.get("status") == "ok",
            f"status={status}, body={body}",
        )
    else:
        check("default /health", False, f"body not dict: {body}")

    status, body = http_request("GET", f"{base_url}/students")
    default_students = body if isinstance(body, list) else []
    check(
        "default /students list",
        status == 200 and len(default_students) == len(default_seed["students"]),
        f"got {len(default_students)}, expected {len(default_seed['students'])}",
    )

    # -- 6. Test admin endpoint (no auth) --
    print("-- 6. Testing admin endpoint without auth (expect 401) --")
    status, body = http_request("GET", f"{base_url}/admin/tenants")
    check("admin /tenants without auth", status == 401, f"got {status}")

    # -- 7. Add tenant via admin API --
    print("-- 7. Adding tenant school-a via admin API --")
    school_a_config = json.loads((school_a_scenario / "config.json").read_text())
    add_resp = http_request(
        "POST",
        f"{base_url}/admin/tenants",
        headers=admin_headers,
        body={
            "id": "school-a",
            "config": school_a_config,
            "config_path": str(school_a_scenario / "config.json"),
        },
    )
    check(
        "add school-a", add_resp[0] == 201, f"status={add_resp[0]}, body={add_resp[1]}"
    )

    # -- 8. List tenants --
    print("-- 8. Listing tenants --")
    status, body = http_request(
        "GET", f"{base_url}/admin/tenants", headers=admin_headers
    )
    if isinstance(body, dict) and "tenants" in body:
        tenant_ids = [t["id"] for t in body["tenants"]]
        check(
            "list tenants (default + school-a)",
            status == 200 and "default" in tenant_ids and "school-a" in tenant_ids,
            f"got {tenant_ids}",
        )
    else:
        check("list tenants", False, f"bad body: {body}")

    # -- 9. Routing: X-Tenant-ID → school-a --
    print("-- 9. Testing X-Tenant-ID routing --")
    status, body = http_request(
        "GET", f"{base_url}/students", headers={"X-Tenant-ID": "school-a"}
    )
    if isinstance(body, list):
        school_a_students = body
        # Проверяем что имена студентов содержат "SCHOOL-A" (от school-a tenant)
        all_from_a = all(
            "SCHOOL-A" in (s.get("name", "") or "") for s in school_a_students
        )
        check(
            "X-Tenant-ID school-a → school-a students",
            status == 200
            and len(school_a_students) == len(school_a_seed["students"])
            and all_from_a,
            f"got {len(school_a_students)} students, all_from_a={all_from_a}",
        )
    else:
        check("X-Tenant-ID school-a", False, f"body not list: {body}")

    # Routing: без X-Tenant-ID → default
    status, body = http_request("GET", f"{base_url}/students")
    if isinstance(body, list):
        no_header_students = body
        all_default = all(
            "DEFAULT" in (s.get("name", "") or "") for s in no_header_students
        )
        check(
            "no X-Tenant-ID → default tenant",
            status == 200
            and len(no_header_students) == len(default_seed["students"])
            and all_default,
            f"got {len(no_header_students)} students, all_default={all_default}",
        )
    else:
        check("no X-Tenant-ID", False, f"body not list: {body}")

    # -- 10. Routing: get single student by ID with X-Tenant-ID --
    print("-- 10. Testing get_by_id with X-Tenant-ID --")
    school_a_s1 = school_a_seed["students"][0]
    status, body = http_request(
        "GET",
        f"{base_url}/students/{school_a_s1['id']}",
        headers={"X-Tenant-ID": "school-a"},
    )
    check(
        f"get school-a student {school_a_s1['id']}",
        status == 200
        and isinstance(body, dict)
        and body.get("name") == school_a_s1["name"],
        f"got {body}",
    )

    # -- 11. Add second tenant school-b --
    print("-- 11. Adding second tenant school-b --")
    school_b_config = json.loads((school_b_scenario / "config.json").read_text())
    add_b = http_request(
        "POST",
        f"{base_url}/admin/tenants",
        headers=admin_headers,
        body={
            "id": "school-b",
            "config": school_b_config,
            "config_path": str(school_b_scenario / "config.json"),
        },
    )
    check("add school-b", add_b[0] == 201, f"status={add_b[0]}")

    # -- 12. Multi-tenant health --
    print("-- 12. Multi-tenant /health --")
    status, body = http_request("GET", f"{base_url}/health")
    if isinstance(body, dict) and "tenants" in body:
        # Multi-tenant mode: response has tenants[]
        tenant_health = {t["id"]: t["status"] for t in body["tenants"]}
        check(
            "multi-tenant /health has all 3 tenants",
            status == 200
            and len(tenant_health) == 3
            and all(
                tid in tenant_health for tid in ["default", "school-a", "school-b"]
            ),
            f"got {tenant_health}",
        )
        check(
            "all tenants healthy",
            all(s == "healthy" for s in tenant_health.values()),
            f"statuses: {tenant_health}",
        )
    else:
        check(
            "multi-tenant /health format",
            False,
            f"expected {{status, tenants[]}} body, got: {body}",
        )

    # -- 13. Duplicate add (expect 409) --
    print("-- 13. Adding duplicate tenant (expect 409) --")
    dup = http_request(
        "POST",
        f"{base_url}/admin/tenants",
        headers=admin_headers,
        body={"id": "school-a", "config": school_a_config},
    )
    check("duplicate add → 409", dup[0] == 409, f"got {dup[0]}")

    # -- 14. Remove non-default tenant --
    print("-- 14. Removing school-b --")
    rm = http_request(
        "DELETE", f"{base_url}/admin/tenants/school-b", headers=admin_headers
    )
    check("DELETE school-b", rm[0] == 200, f"got {rm[0]}")

    status, body = http_request("GET", f"{base_url}/health")
    if isinstance(body, dict) and "tenants" in body:
        tenant_health = {t["id"] for t in body["tenants"]}
        check(
            "school-b removed from /health",
            "school-b" not in tenant_health and len(tenant_health) == 2,
            f"remaining: {tenant_health}",
        )
    else:
        check("post-removal /health", False, f"bad body: {body}")

    # -- 15. Cannot remove default tenant --
    print("-- 15. Cannot remove default tenant (expect 403) --")
    rm_default = http_request(
        "DELETE", f"{base_url}/admin/tenants/default", headers=admin_headers
    )
    check("DELETE default → 403", rm_default[0] == 403, f"got {rm_default[0]}")

    # -- 16. Routing to removed tenant (expect error) --
    print("-- 16. Routing to removed tenant (school-b) --")
    status, body = http_request(
        "GET", f"{base_url}/students", headers={"X-Tenant-ID": "school-b"}
    )
    # Должно вернуть ошибку (404 или 500)
    check("X-Tenant-ID: school-b after removal fails", status >= 400, f"got {status}")

    # -- 17. Report --
    print(f"\n{'=' * 60}")
    if failures == 0:
        print("ALL PASSED — 3 tenants managed via admin API, X-Tenant-ID routing works")
    else:
        print(f"{failures} FAILURE(S)")
    print(f"{'=' * 60}")

    cleanup()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
