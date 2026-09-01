"""E2E: read-only probe — попытки записи не должны менять данные tenant'а.

Закрывает дыру саботажа №1 из аудита 2026-08-28: полный E2E никогда не слал
попытку записи, поэтому отключение SQL-гвардов не меняло результат прогона.
Три зонда:

- probe-guard:  конфиг с SQL-инъекцией в stats.counters[].filter /
  auth.row_filters[].where должен быть отвергнут на POST /admin/tenants (400
  от Config.Validate → isValidFilterExpression). Маркер: students rows
  до/после не изменились.
- probe-http:   POST/PUT/DELETE на REST-эндпоинты data-service → 4xx
  (read-only mode не регистрирует write-методы), данные нетронуты.
- probe-tool:   MCP db_filter с SQL-инъекцией в значении поля — значения
  фильтров это данные (параметризованный WHERE), пишущего тул-поверхности нет;
  проверяем, что данные не мутируют независимо от ответа тула.

Задача todo T-2 упоминала тул db_counter — на текущем HEAD его не существует;
SQL-инъекция из конфига достижима через counter.filter (stats.go) и
row_filter.where (row_filter.go), оба гвардятся Config.Validate. Зонд
фиксирует актуальную поверхность.

Маркер целостности: прямой sqlite3-коннект к своему же seeded DB (вне
data-service) — сравниваем count и дайджест строк students до/после зондов.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
import requests

from tests.e2e.helpers import (
    TestTenant,
    cleanup_db,
    data_service_url,
    make_tenant,
    mcp_call,
)


def _students_digest(t: TestTenant) -> tuple[int, str]:
    """(row_count, md5 of all rows) of the students table, read directly.

    Opened mode=ro: this is an integrity marker, never a writer — a parallel
    data-service connection on the same WAL database must not see a RW
    side-channel from the test process.
    """
    conn = sqlite3.connect(f"file:{t.db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, name, group_id, course FROM students ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    digest = hashlib.md5(repr(rows).encode()).hexdigest()
    return len(rows), digest


def _register_expect(cfg: dict, tid: str) -> requests.Response:
    """POST /admin/tenants with an explicit config; returns raw response."""
    base = data_service_url()
    from tests.e2e.helpers import admin_headers

    resp = requests.post(
        f"{base}/admin/tenants",
        json={"id": tid, "config": cfg},
        headers=admin_headers(),
        timeout=10,
    )
    # Never leave a half-registered tenant behind.
    if resp.status_code in (200, 201):
        from tests.e2e.helpers import delete_tenant

        delete_tenant(tid)
    return resp


@pytest.fixture(scope="module")
def probe_tenant():
    """Registered sqlite-testseed tenant used for HTTP/MCP probes."""
    t = make_tenant("sqlite-testseed", prefix="ro")
    t.register()
    yield t
    t.cleanup()


# ── probe-guard: config SQL injection must fail closed at registration ─────


@pytest.mark.parametrize(
    "injection",
    [
        "1=1; DROP TABLE students",
        "1=1; DELETE FROM students",
        "1=1; INSERT INTO students VALUES (1)",
        "1=1; UPDATE students SET name='pwned'",
        "name='x' -- comment",
        "1=1 UNION SELECT 1 FROM students",
    ],
)
def test_probe_guard_counter_filter_rejected(injection):
    """SQL injection in stats.counters[].filter → 400, data untouched."""
    t = make_tenant("sqlite-testseed", prefix="rog")
    try:
        cfg = dict(t.config or {})
        cfg["data_source"] = {
            "driver": "sqlite",
            "dsn": str(t.db_path),
            "read_only": True,
        }
        cfg["stats"] = {
            "counters": [
                {"name": "students", "entity": "student", "filter": injection},
            ]
        }

        before = _students_digest(t)
        resp = _register_expect(cfg, t.id)
        assert resp.status_code == 400, (
            f"probe-guard: injection {injection!r} was NOT rejected at "
            f"registration: status={resp.status_code} body={resp.text[:300]}"
        )
        body = resp.text
        assert "forbidden SQL construct" in body or "invalid" in body.lower(), (
            f"probe-guard: 400 for counter.filter injection came from an "
            f"unrelated validation error, not the SQL guard: {body[:300]}"
        )
        after = _students_digest(t)
        assert before == after, (
            f"probe-guard: DATA CHANGED via counter.filter injection {injection!r}: "
            f"{before} -> {after}"
        )
    finally:
        # Probe-guard tests never register the tenant; drop the seeded temp DB
        # (and WAL/SHM sidecars) so failed probes do not leak files into
        # E2E_DB_DIR / .data.
        cleanup_db(t.db_path)


def test_probe_guard_row_filter_where_rejected():
    """SQL injection in auth.row_filters[].where → 400, data untouched.

    All entities get benign row_filters so the ONLY possible rejection reason
    is the SQL guard on the injected where (header-auth otherwise requires a
    row_filter per entity and would 400 for a different, unrelated reason).
    """
    t = make_tenant("sqlite-testseed", prefix="ror")
    try:
        _probe_guard_row_filter_body(t)
    finally:
        cleanup_db(t.db_path)


def _probe_guard_row_filter_body(t: TestTenant) -> None:
    cfg = dict(t.config or {})
    cfg["data_source"] = {
        "driver": "sqlite",
        "dsn": str(t.db_path),
        "read_only": True,
    }
    benign = [
        {"entity": name, "where": "1=1"}
        for name in ("group", "teacher", "discipline", "grade", "schedule")
    ]
    cfg["auth"] = {
        "strategy": "header",
        "row_filters": benign
        + [
            {
                "entity": "student",
                "where": "group_id='g1'; DROP TABLE students",
            }
        ],
    }

    before = _students_digest(t)
    resp = _register_expect(cfg, t.id)
    assert resp.status_code == 400, (
        f"probe-guard: row_filter injection NOT rejected: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )
    body = resp.text
    assert "forbidden SQL construct" in body or "invalid" in body.lower(), (
        f"probe-guard: 400 for row_filter injection came from an unrelated "
        f"validation error, not the SQL guard: {body[:300]}"
    )
    after = _students_digest(t)
    assert before == after, (
        f"probe-guard: DATA CHANGED via row_filter injection: {before} -> {after}"
    )


# ── probe-http: REST write verbs must fail closed ──────────────────────────


def test_probe_http_write_verbs_rejected(probe_tenant):
    """POST/PUT/PATCH/DELETE on data-service entity endpoints → 4xx."""
    t = probe_tenant
    before = _students_digest(t)
    base = data_service_url()

    write_attempts = [
        ("POST", f"{base}/students", {"name": "pwn", "group_id": "g1"}),
        ("PUT", f"{base}/students/s1", {"name": "pwn"}),
        ("PATCH", f"{base}/students/s1", {"name": "pwn"}),
        ("DELETE", f"{base}/students/s1", None),
        ("POST", f"{base}/q/filter", {"entity": "student"}),
        ("PUT", f"{base}/stats", {}),
    ]
    headers = {"X-Tenant-ID": t.id, "Content-Type": "application/json"}
    for method, url, payload in write_attempts:
        r = requests.request(
            method, url, json=payload, headers=headers, timeout=10
        )
        assert 400 <= r.status_code < 500, (
            f"probe-http: {method} {url} returned {r.status_code} "
            f"(expected 4xx): {r.text[:200]}"
        )

    after = _students_digest(t)
    assert before == after, (
        f"probe-http: DATA CHANGED after write attempts: {before} -> {after}"
    )


# ── probe-tool: MCP tool surface carries no write path ─────────────────────


def test_probe_tool_no_write_tools(probe_tenant):
    """MCP tool manifest exposes no mutating tools."""
    names = {tool["name"] for tool in probe_tenant.tools()}
    write_markers = (
        "insert", "update", "delete", "drop", "create", "alter",
        "truncate", "exec", "execute", "write",
    )
    for name in names:
        lowered = name.lower()
        for marker in write_markers:
            assert not lowered.startswith(marker) and f"_{marker}" not in lowered, (
                f"probe-tool: suspicious tool name {name!r} in manifest: {sorted(names)}"
            )


def test_probe_tool_db_filter_injection_no_mutation(probe_tenant):
    """MCP db_filter with SQL payload in a field value — data untouched.

    Field values are data (parameterized), unknown fields fail schema
    validation — either way students must be unchanged.
    """
    t = probe_tenant
    before = _students_digest(t)

    result = mcp_call(
        "db_filter",
        {
            "entity": "student",
            "name": "x'; DROP TABLE students; --",
        },
        tenant_ids=t.id,
        timeout=30,
    )
    # No assert on result: a guarded isError OR an empty/partial success are
    # both acceptable; the invariant is the data, checked below.
    _ = result

    after = _students_digest(t)
    assert before == after, (
        f"probe-tool: DATA CHANGED via db_filter injection: {before} -> {after}"
    )
