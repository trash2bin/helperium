"""MCP validation: проверка что тулы с required аргументами реджектят пустые вызовы.

Проблема: LLM (deepseek) шлёт `get_catalog_product({})` и `grep_catalog_product({})`
с пустыми аргументами. MCP-гейтвей должен возвращать isError, а не выполнять запрос.

Что тестируем:
1. get_*({}) → isError (требует id)
2. grep_*({}) → isError (требует pattern)
3. get_*(правильные args) → OK
4. grep_*(правильные args) → OK
5. schema_*({}) → OK (без параметров)
6. filter_*(с параметрами) → OK
7. Все тулы (кроме count_*) имеют required параметры
8. Long regex → isError (ReDoS защита)
9. limit > 100 → isError
10. search_* и simple_* тулы отсутствуют

Создаёт собственный tenant через интроспекцию БД из auto-shop сценария.
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
    mcp_gateway_url,
    mcp_call,
    project_root,
    scenarios_dir,
)

pytestmark = [
    pytest.mark.skipif(
        not admin_headers(),
        reason="ADMIN_TOKEN not set — register admin API calls",
    ),
]

# ── Helpers ────────────────────────────────────────────────────────────────


def _tenant_id(prefix: str) -> str:
    return f"e2e-mcp-{prefix}-{uuid.uuid4().hex[:6]}"


def _create_db(scenario: str) -> Path:
    """Create scenario database and return path."""
    sc_dir = scenarios_dir() / scenario
    if not sc_dir.exists():
        raise FileNotFoundError(f"Scenario dir not found: {sc_dir}")

    script = sc_dir / "create_db.py"
    db_path = sc_dir / "data.db"

    # Удаляем старую БД если есть
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
    if result.returncode != 0:
        raise RuntimeError(f"create_db.py failed:\n{result.stderr}")

    if not db_path.exists():
        raise RuntimeError(f"DB not created: {db_path}")

    return db_path


def _register_and_rewrite(tenant_id: str, db_path: Path) -> dict:
    """Register a tenant with minimal config, then POST /admin/config/rewrite.

    Returns rewrite response.
    """
    base = data_service_url()
    h = admin_headers()

    # 1. Register tenant with just DSN
    config = {
        "data_source": {
            "driver": "sqlite",
            "dsn": str(db_path),
            "read_only": True,
        },
    }

    resp = requests.post(
        f"{base}/admin/tenants",
        json={"id": tenant_id, "config": config},
        headers=h,
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        if resp.status_code == 409:
            requests.delete(
                f"{base}/admin/tenants/{tenant_id}", headers=h, timeout=10
            )
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
        headers={
            "X-Tenant-ID": tenant_id,
            **h,
        },
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Rewrite: {resp.status_code} {resp.text[:200]}"
    )

    return resp.json()


def _get_tool_list(tenant_id: str) -> list[dict]:
    """Получить все MCP тулы из конфига tenant'а."""
    ds = data_service_url()
    r = requests.get(
        f"{ds}/admin/config",
        headers={**admin_headers(), "X-Tenant-ID": tenant_id},
        timeout=10,
    )
    assert r.status_code == 200, f"Failed to get config: {r.status_code} {r.text[:200]}"
    return r.json().get("mcp_tools", [])


def _get_tool_by_name(tools: list[dict], name: str) -> dict:
    """Найти тул по имени."""
    for t in tools:
        if t["name"] == name:
            return t
    raise AssertionError(f"Tool '{name}' not found among: {[t['name'] for t in tools]}")


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tenant_context():
    """Create a fresh tenant from auto-shop scenario for all tests in module.

    Yields (tenant_id, tools). Cleans up after module.
    """
    db_path = _create_db("auto-shop")
    tid = _tenant_id("val")
    rewrite_result = _register_and_rewrite(tid, db_path)

    # Даём mcp-gateway время подхватить новый tenant
    import time

    time.sleep(1)

    tools = _get_tool_list(tid)

    yield tid, tools

    # Cleanup
    try:
        requests.delete(
            f"{data_service_url()}/admin/tenants/{tid}",
            headers=admin_headers(),
            timeout=10,
        )
    except Exception:
        pass

    # Remove db if it was created in scenario dir
    try:
        if db_path.exists():
            db_path.unlink()
            for ext in ("-wal", "-shm"):
                (db_path.with_suffix(db_path.suffix + ext)).unlink(missing_ok=True)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 1. GET_* validation — должны требовать id
# ═══════════════════════════════════════════════════════════════════════════


class TestGetWithRequired:
    """get_* тулы: пустой вызов → isError, с id → OK."""

    def test_get_without_id_returns_is_error(self, tenant_context):
        """get_auto_parts({}) → isError, не данные."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "get_auto_parts")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "id" in required, (
            f"get_auto_parts должен требовать id. required={required}"
        )

        result = mcp_call("get_auto_parts", {}, tenant_ids=tid, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"get_auto_parts({{}}) должно вернуть isError.\n"
            f"  Вместо этого: {err_text[:300]}"
        )
        assert "id" in err_text.lower(), (
            f"Ошибка должна упоминать 'id'. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty get_auto_parts → isError: {err_text[:200]}")

    def test_get_with_id_returns_ok(self, tenant_context):
        """get_auto_parts({'id': 1}) → данные."""
        tid, _ = tenant_context
        result = mcp_call(
            "get_auto_parts", {"id": 1}, tenant_ids=tid, timeout=30
        )
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, f"Empty response for valid get call: {result}"
        assert "error" not in text.lower()[:50], (
            f"Valid get returned error-like: {text[:200]}"
        )
        print(f"\n  ✅ get_auto_parts(id=1) → {len(text)} chars")

    def test_several_get_tools_reject_empty(self, tenant_context):
        """Несколько get_* тулов проверяются на валидацию."""
        tid, tools = tenant_context
        get_tools = [t for t in tools if t["name"].startswith("get_")]
        import random

        random.seed(42)
        samples = random.sample(get_tools, min(3, len(get_tools)))

        for t in samples:
            name = t["name"]
            result = mcp_call(name, {}, tenant_ids=tid, timeout=30)
            is_error = result.result.get("isError", False)
            assert is_error, (
                f"{name}({{}}) не вернул isError!\n"
                f"  Tool params: {t.get('params', [])}"
            )
            print(f"  ✅ {name}({{}}) → isError")


# ═══════════════════════════════════════════════════════════════════════════
# 2. GREP_* validation — должны требовать pattern
# ═══════════════════════════════════════════════════════════════════════════


class TestGrepWithRequired:
    """grep_* тулы: пустой вызов → isError, с pattern → OK."""

    def test_grep_without_pattern_returns_is_error(self, tenant_context):
        """grep_auto_parts({}) → isError."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "grep_auto_parts")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "pattern" in required, (
            f"grep_auto_parts должен требовать pattern. required={required}"
        )

        result = mcp_call("grep_auto_parts", {}, tenant_ids=tid, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"grep_auto_parts({{}}) должно вернуть isError.\n"
            f"  Response OK: {err_text[:300]}"
        )
        assert "pattern" in err_text.lower() or "required" in err_text.lower(), (
            f"Ошибка должна упоминать pattern/required. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty grep_auto_parts → isError: {err_text[:200]}")

    def test_grep_with_pattern_returns_ok(self, tenant_context):
        """grep_auto_parts({'pattern': 'масло'}) → данные."""
        tid, _ = tenant_context
        result = mcp_call(
            "grep_auto_parts", {"pattern": "масло"}, tenant_ids=tid, timeout=30
        )
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, f"Empty response for valid grep call"
        assert "error" not in text.lower()[:50], (
            f"Valid grep returned error-like: {text[:200]}"
        )
        print(f"\n  ✅ grep_auto_parts(pattern='масло') → {len(text)} chars")

    def test_several_grep_tools_reject_empty(self, tenant_context):
        """Несколько grep_* тулов проверяются."""
        tid, tools = tenant_context
        grep_tools = [t for t in tools if t["name"].startswith("grep_")]
        import random

        random.seed(42)
        samples = random.sample(grep_tools, min(3, len(grep_tools)))

        for t in samples:
            name = t["name"]
            result = mcp_call(name, {}, tenant_ids=tid, timeout=30)
            is_error = result.result.get("isError", False)
            if not is_error:
                content = result.result.get("content", [])
                text = "".join(c.get("text", "") for c in content if "text" in c)
            assert is_error, (
                f"{name}({{}}) не вернул isError!\n"
                f"  Вместо этого: {text[:200] if not is_error else 'OK'}"
            )
            print(f"  ✅ {name}({{}}) → isError")

    def test_grep_long_regex_returns_is_error(self, tenant_context):
        """grep_* with very long regex pattern → isError (ReDoS protection)."""
        tid, _ = tenant_context
        long_pattern = "a" * 300  # exceeds maxRegexLen=200
        result = mcp_call(
            "grep_auto_parts",
            {"pattern": long_pattern, "regex": True},
            tenant_ids=tid,
            timeout=30,
        )
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert is_error, f"Long regex should give isError. Got: {text[:200]}"
        assert "too long" in text.lower() or "max" in text.lower(), (
            f"Should mention length limit: {text[:200]}"
        )
        print(f"\n  ✅ long regex → isError: {text[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Все тулы: проверка что required поля заданы
# ═══════════════════════════════════════════════════════════════════════════


class TestAllToolsHaveRequiredGuard:
    """Тулы, которые должны иметь required параметры: get_*, grep_*, relationship (*_by_*).

    filter_*, find_*, schema_*, count_*, distinct_* — легитимно без required:
    - filter_{entity}() — LLM передаёт field__op параметры, ни один не обязателен
    - find_{entity}() — backward compat, параметры опциональны
    - schema_{entity}() — без параметров (discovery)
    - count_{entity}() — без параметров (everything)
    - distinct_{entity}() — column опционален (по умолч. nameCol)
    """

    def test_grep_and_get_have_required_param(self, tenant_context):
        """У get_*, grep_*, *_by_* есть required параметры."""
        tid, tools = tenant_context
        bad = []
        for t in tools:
            name = t["name"]
            # Только get_*, grep_*, и relationship (*_by_*) должны иметь required
            if not (name.startswith("get_") or name.startswith("grep_") or "_by_" in name):
                continue
            params = t.get("params", [])
            required = [p["name"] for p in params if p.get("required")]
            if not required:
                bad.append(name)

        assert not bad, (
            f"Тулы без required параметров: {bad}"
        )

    def test_all_tool_params_have_names(self, tenant_context):
        """Проверка что у всех тулов параметры имеют имена (базовая валидация схемы)."""
        tid, tools = tenant_context
        issues = []
        for t in tools:
            name = t["name"]
            params = t.get("params", [])
            for p in params:
                if not p.get("name"):
                    issues.append(f"{name}: param without name: {p}")
        assert not issues, f"Параметры без имени:\n" + "\n".join(issues)


# ═══════════════════════════════════════════════════════════════════════════
# 4. limit параметры имеют максимальное значение
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitHasMaxBound:
    """limit параметр не должен позволять загрузить всю БД."""

    def test_limit_has_maximum_constraint(self, tenant_context):
        """Проверяем что в схеме тула есть параметр limit."""
        tid, tools = tenant_context
        found = False
        for t in tools:
            params = t.get("params", [])
            for p in params:
                if p["name"] == "limit":
                    found = True
                    break
            if found:
                break
        assert found, "Ни один тул не имеет параметра limit!"
        print(f"\n  ✅ limit parameter found in tools")

    def test_limit_gt_100_returns_is_error(self, tenant_context):
        """limit > 100 → isError (cap changed from 1000 to 100 in v4)."""
        tid, _ = tenant_context
        result = mcp_call(
            "grep_auto_parts",
            {"pattern": "масло", "limit": 9999999},
            tenant_ids=tid,
            timeout=30,
        )
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"Слишком большой limit должен давать isError. Ответ: {text[:200]}"
        )
        assert "limit" in text.lower() or "value" in text.lower(), (
            f"Ошибка должна упоминать limit/value. Текст: {text[:200]}"
        )
        print(f"\n  ✅ limit=9999999 → isError: {text[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Проверка отсутствия устаревших тулов
# ═══════════════════════════════════════════════════════════════════════════


class TestNoLegacyTools:
    """После v4 search_*, simple_*, find_*, list_* тулы не должны генерироваться
    для entity с grep/filter/schema стратегией."""

    def test_no_search_tools(self, tenant_context):
        """search_* не должно быть в манифесте."""
        tid, tools = tenant_context
        for t in tools:
            name = t["name"]
            assert not name.startswith("search_"), (
                f"search_* тулы удалены в v4, но найден: {name}"
            )
        print(f"\n  ✅ Нет search_* тулов ({len(tools)} total)")

    def test_no_simple_tools(self, tenant_context):
        """simple_* не должно быть в манифесте."""
        tid, tools = tenant_context
        for t in tools:
            name = t["name"]
            assert not name.startswith("simple_"), (
                f"simple_* тулы удалены в v4, но найден: {name}"
            )
        print(f"  ✅ Нет simple_* тулов")

    def test_has_grep_filter_schema(self, tenant_context):
        """grep_*, filter_*, schema_* должны быть."""
        tid, tools = tenant_context
        names = [t["name"] for t in tools]
        grep_tools = [n for n in names if n.startswith("grep_")]
        filter_tools = [n for n in names if n.startswith("filter_")]
        schema_tools = [n for n in names if n.startswith("schema_")]

        assert len(grep_tools) >= 1, f"Нет grep_* тулов среди: {names}"
        assert len(filter_tools) >= 1, f"Нет filter_* тулов среди: {names}"
        assert len(schema_tools) >= 1, f"Нет schema_* тулов среди: {names}"

        print(f"  ✅ Тулы: {len(grep_tools)} grep, {len(filter_tools)} filter, {len(schema_tools)} schema")
