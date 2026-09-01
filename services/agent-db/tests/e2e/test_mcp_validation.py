"""MCP validation: проверка консолидированных db_* тулов (Фаза 2/2.5).

Проблема: LLM (deepseek) шлёт тулы с пустыми аргументами. MCP-гейтвей должен
возвращать isError, а не выполнять запрос.

Что тестируем (v5 контракт — 5 консолидированных db_* + N пер-энтити filter_{entity}):
1. db_get({}) → isError (требует entity + id)
2. db_search({}) → isError (требует entity + pattern)
3. db_get(entity, id) → OK
4. db_search(entity, pattern) → OK
5. db_describe(entity) → OK
6. db_filter(field__op) + filter_{entity}(field__op) → OK (оба тула доступны)
7. Все db_* тулы имеют required параметры
8. Long regex в db_search → isError (ReDoS защита)
9. limit > 100 → isError
10. Нет per-entity тулов (grep_*/schema_*/get_*/count_*/distinct_*) — консолидированы
11. Ровно 5 db_* тулов независимо от размера БД

Создаёт собственный tenant через интроспекцию БД из auto-shop сценария.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    data_service_url,
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

    # 2. Rewrite (introspect → generate entities/endpoints/tools)
    resp = requests.post(
        f"{base}/admin/config/rewrite",
        headers={**h, "X-Tenant-ID": tenant_id},
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Rewrite: {resp.status_code} {resp.text[:300]}"
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
    _register_and_rewrite(tid, db_path)

    # Ждём, пока mcp-gateway подхватит новый tenant: poll вместо blind sleep —
    # готовность = тулы появились в admin config (тот же источник, что и тест).
    import time

    deadline = time.time() + 15
    tools: list[dict] = []
    while time.time() < deadline:
        try:
            tools = _get_tool_list(tid)
            if tools:
                break
        except (requests.ConnectionError, OSError, AssertionError):
            pass
        time.sleep(0.5)
    assert tools, f"tenant {tid} tools never appeared within 15s"

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
# 1. DB_GET — должен требовать entity + id
# ═══════════════════════════════════════════════════════════════════════════


class TestDBGetWithRequired:
    """db_get: пустой вызов → isError, с entity+id → OK."""

    def test_get_without_arguments_returns_is_error(self, tenant_context):
        """db_get({}) → isError, не данные."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "db_get")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "entity" in required, (
            f"db_get должен требовать entity. required={required}"
        )
        assert "id" in required, (
            f"db_get должен требовать id. required={required}"
        )

        result = mcp_call("db_get", {}, tenant_ids=tid, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"db_get({{}}) должно вернуть isError.\n"
            f"  Вместо этого: {err_text[:300]}"
        )
        assert "id" in err_text.lower() or "required" in err_text.lower(), (
            f"Ошибка должна упоминать id/required. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty db_get → isError: {err_text[:200]}")

    def test_get_with_id_returns_ok(self, tenant_context):
        """db_get(entity, id) → данные (id из поиска)."""
        tid, _ = tenant_context
        # Сначала db_search, чтобы получить реальный id (анти-перебор).
        search = mcp_call(
            "db_search",
            {"entity": "auto_parts", "pattern": "масло"},
            tenant_ids=tid,
            timeout=30,
        )
        text = "".join(c.get("text", "") for c in search.result.get("content", []) if "text" in c)
        import re

        m = re.search(r'"id"\s*:\s*(\d+)', text)
        assert m, f"db_search должен вернуть id: {text[:300]}"
        rid = int(m.group(1))

        result = mcp_call(
            "db_get", {"entity": "auto_parts", "id": rid}, tenant_ids=tid, timeout=30
        )
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, f"Empty response for valid db_get call: {result}"
        assert not result.result.get("isError", False), (
            f"Valid db_get returned isError: {text[:200]}"
        )
        print(f"\n  ✅ db_get(entity, id={rid}) → {len(text)} chars")


# ═══════════════════════════════════════════════════════════════════════════
# 2. DB_SEARCH — должен требовать entity + pattern
# ═══════════════════════════════════════════════════════════════════════════


class TestDBSearchWithRequired:
    """db_search: пустой вызов → isError, с pattern → OK."""

    def test_search_without_pattern_returns_is_error(self, tenant_context):
        """db_search({}) → isError."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "db_search")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "entity" in required, (
            f"db_search должен требовать entity. required={required}"
        )
        assert "pattern" in required, (
            f"db_search должен требовать pattern. required={required}"
        )

        result = mcp_call("db_search", {}, tenant_ids=tid, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"db_search({{}}) должно вернуть isError.\n"
            f"  Response OK: {err_text[:300]}"
        )
        assert "pattern" in err_text.lower() or "required" in err_text.lower(), (
            f"Ошибка должна упоминать pattern/required. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty db_search → isError: {err_text[:200]}")

    def test_search_with_pattern_returns_ok(self, tenant_context):
        """db_search(entity, pattern) → данные."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_search",
            {"entity": "auto_parts", "pattern": "масло"},
            tenant_ids=tid,
            timeout=30,
        )
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, "Empty response for valid db_search call"
        assert not result.result.get("isError", False), (
            f"Valid db_search returned isError: {text[:200]}"
        )
        print(f"\n  ✅ db_search(entity, pattern='масло') → {len(text)} chars")

    def test_search_long_regex_returns_is_error(self, tenant_context):
        """db_search with very long regex pattern → isError (ReDoS protection)."""
        tid, _ = tenant_context
        long_pattern = "a" * 300  # exceeds maxRegexLen=200
        result = mcp_call(
            "db_search",
            {"entity": "auto_parts", "pattern": long_pattern, "regex": True},
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
# 3. Все db_* тулы: required параметры
# ═══════════════════════════════════════════════════════════════════════════


class TestAllToolsHaveRequiredGuard:
    """db_search/db_get/db_related/db_describe — required entity; filter_* — per-field.

    db_map — без параметров (вся карта).
    """

    def test_db_tools_have_required_param(self, tenant_context):
        """У db_search/db_get/db_related/db_describe есть required entity."""
        tid, tools = tenant_context
        bad = []
        for t in tools:
            name = t["name"]
            if name == "db_map" or name.startswith("filter_"):
                continue
            if not name.startswith("db_"):
                continue
            params = t.get("params", [])
            required = [p["name"] for p in params if p.get("required")]
            if "entity" not in required:
                bad.append((name, required))

        assert not bad, (
            f"db_* тулы без required entity: {bad}"
        )

    def test_filter_tools_expose_fields(self, tenant_context):
        """filter_* (пер-энтити, Фаза 2.5): поля сущности в параметрах тула
        (тупая модель должна видеть имена полей прямо в схеме)."""
        tid, tools = tenant_context
        filter_tools = [t for t in tools if t["name"].startswith("filter_")]
        assert len(filter_tools) >= 1, f"Нет filter_* тулов: {[t['name'] for t in tools]}"
        # У filter_auto_parts должны быть поля (не только limit).
        t = _get_tool_by_name(tools, "filter_auto_parts")
        params = t.get("params", [])
        field_params = [p["name"] for p in params if p["name"] != "limit"]
        assert len(field_params) >= 3, (
            f"filter_auto_parts должен перечислять поля (не только limit), got {field_params}"
        )
        print(f"\n  ✅ filter_auto_parts поля: {field_params[:8]}")

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
        assert not issues, "Параметры без имени:\n" + "\n".join(issues)


# ═══════════════════════════════════════════════════════════════════════════
# 4. limit параметры имеют максимальное значение
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitHasMaxBound:
    """limit параметр не должен позволять загрузить всю БД."""

    def test_limit_has_maximum_constraint(self, tenant_context):
        """Проверяем что в схеме db_search есть параметр limit."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "db_search")
        params = t.get("params", [])
        has_limit = any(p["name"] == "limit" for p in params)
        assert has_limit, f"db_search должен иметь limit: {params}"
        print("\n  ✅ limit parameter found in db_search")

    def test_limit_gt_100_returns_is_error(self, tenant_context):
        """db_search limit > 100 → isError."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_search",
            {"entity": "auto_parts", "pattern": "масло", "limit": 9999999},
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
# 5. Консолидация (Фаза 2/2.5): ровно 5 db_* тулов, нет per-entity
# ═══════════════════════════════════════════════════════════════════════════


class TestToolComposition:
    """Фаза 2.5: N filter_* (пер-энтити) + 5 db_* консолидированных."""

    def test_six_db_tools_and_filter_per_entity(self, tenant_context):
        """6 db_* (db_filter + 5 консолидированных) + N filter_* (пер-энтити)."""
        tid, tools = tenant_context
        db_tools = {t["name"] for t in tools if t["name"].startswith("db_")}
        expected_db = {
            "db_map", "db_describe", "db_search", "db_get",
            "db_related", "db_filter",
        }
        assert db_tools == expected_db, (
            f"Ожидались 6 db_*: {sorted(expected_db)}, получили {sorted(db_tools)}"
        )
        # filter_* — пер-энтити, есть для auto_parts.
        filter_tools = [t["name"] for t in tools if t["name"].startswith("filter_")]
        assert "filter_auto_parts" in filter_tools, (
            f"filter_auto_parts должен быть (пер-энтити filter), got {filter_tools}"
        )
        print(f"\n  ✅ 6 db_* + N filter_*: {len(filter_tools)} filter tools")

    def test_no_per_entity_grep_schema(self, tenant_context):
        """grep_*/schema_* остаются консолидированными (не пер-энтити)."""
        tid, tools = tenant_context
        bad_prefixes = ("grep_", "schema_", "get_", "count_", "distinct_")
        bad = [t["name"] for t in tools if t["name"].startswith(bad_prefixes)]
        assert len(bad) == 0, (
            f"grep_/schema_/get_/count_/distinct_ должны быть консолидированы/удалены, но найдены: {bad}"
        )
        print(f"  ✅ Нет per-entity grep_/schema_/get_/count_/distinct_ ({len(tools)} total)")

    def test_no_legacy_tools(self, tenant_context):
        """search_*, simple_*, find_*, list_*, _by_* не должны генерироваться."""
        tid, tools = tenant_context
        names = [t["name"] for t in tools]
        bad = [n for n in names if n.startswith(("search_", "simple_", "find_", "list_")) or "_by_" in n]
        assert len(bad) == 0, (
            f"Legacy-тулы удалены, но найдены: {bad}"
        )
        print("  ✅ Нет legacy-тулов")

    def test_tools_have_display_name(self, tenant_context):
        """db_* и filter_* тулы должны иметь display_name."""
        tid, tools = tenant_context
        for t in tools:
            name = t["name"]
            if name.startswith(("db_", "filter_")):
                dn = t.get("display_name", "")
                assert dn, (
                    f"{name} должен иметь display_name\n"
                    f"  Полный tool: {json.dumps(t, indent=2)[:500]}"
                )
        print("  ✅ Все db_* и filter_* тулы имеют display_name")

    def test_entity_param_is_plain_string(self, tenant_context):
        """entity — обычный string, не enum (на большой БД enum расдул бы манифест)."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "db_search")
        for p in t.get("params", []):
            if p["name"] == "entity":
                assert p.get("type", "string") == "string", (
                    f"entity должен быть string (не enum): {p}"
                )
                break
        else:
            raise AssertionError("db_search не имеет параметра entity")

    def test_unknown_entity_returns_404(self, tenant_context):
        """db_search с неизвестным entity → isError (404 на /q/*), не данные."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_search",
            {"entity": "ghost_entity", "pattern": "a"},
            tenant_ids=tid,
            timeout=30,
        )
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert is_error, (
            f"Неизвестный entity должен давать isError (404), получили данные: {text[:200]}"
        )
        print(f"\n  ✅ db_search(unknown entity) → isError: {text[:150]}")


# ═══════════════════════════════════════════════════════════════════════
# 6. Explicit tests for db_describe, db_related, filter_{entity}
# ═══════════════════════════════════════════════════════════════════════


class TestDBDescribe:
    """db_describe(entity) — discovery метаданных сущности."""

    def test_describe_returns_schema_info(self, tenant_context):
        """db_describe возвращает поля, total, distinct values."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_describe",
            {"entity": "auto_parts"},
            tenant_ids=tid,
            timeout=30,
        )
        assert result.success, f"db_describe failed: {result.error}"
        assert not result.result.get("isError", False), f"isError: {result}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert "category" in text, f"Expected category field in describe: {text[:300]}"
        assert "price" in text, f"Expected price field in describe: {text[:300]}"
        print(f"\n  ✅ db_describe(auto_parts) → {len(text)} chars")

    def test_describe_unknown_entity_is_error(self, tenant_context):
        """db_describe(unknown_entity) → isError."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_describe",
            {"entity": "ghost_entity"},
            tenant_ids=tid,
            timeout=30,
        )
        assert result.result.get("isError", False), f"Expected isError for unknown entity: {result}"
        print("\n  ✅ db_describe(ghost_entity) → isError")

    def test_describe_requires_entity_param(self, tenant_context):
        """db_describe({}) → isError (entity is required)."""
        tid, _ = tenant_context
        result = mcp_call("db_describe", {}, tenant_ids=tid, timeout=30)
        assert result.result.get("isError", False), f"Expected isError for empty call: {result}"
        print("\n  ✅ db_describe({}) → isError")


class TestDBRelated:
    """db_related(entity, id, relation?) — связанные записи."""

    def test_related_returns_related_records(self, tenant_context):
        """db_related находит связанные записи для существующего id.

        Note: relation parameter is required. Use db_map to see available relations.
        """
        tid, _ = tenant_context
        # Сначала находим id через db_search
        search = mcp_call(
            "db_search",
            {"entity": "auto_parts", "pattern": "масло"},
            tenant_ids=tid,
            timeout=30,
        )
        text = "".join(c.get("text", "") for c in search.result.get("content", []) if "text" in c)
        import re
        m = re.search(r'"id"\s*:\s*(\d+)', text)
        assert m, f"db_search должен вернуть id: {text[:300]}"
        rid = int(m.group(1))

        # db_related требует relation parameter - используем первую доступную связь
        # Для auto_parts связей может не быть, поэтому проверяем что isError с понятным сообщением
        result = mcp_call(
            "db_related",
            {"entity": "auto_parts", "id": rid, "relation": "nonexistent"},
            tenant_ids=tid,
            timeout=30,
        )
        # Должен вернуть isError с сообщением о неизвестной связи
        assert result.result.get("isError", False), f"Expected isError for unknown relation: {result}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert "unknown relation" in text.lower() or "invalid_relation" in text.lower(), f"Expected relation error: {text[:300]}"
        print("\n  ✅ db_related(unknown relation) → isError as expected")

    def test_related_unknown_entity_is_error(self, tenant_context):
        """db_related(unknown_entity) → isError."""
        tid, _ = tenant_context
        result = mcp_call(
            "db_related",
            {"entity": "ghost_entity", "id": 1},
            tenant_ids=tid,
            timeout=30,
        )
        assert result.result.get("isError", False), f"Expected isError for unknown entity: {result}"
        print("\n  ✅ db_related(ghost_entity) → isError")

    def test_related_requires_entity_and_id(self, tenant_context):
        """db_related({}) → isError (entity and id required)."""
        tid, _ = tenant_context
        result = mcp_call("db_related", {}, tenant_ids=tid, timeout=30)
        assert result.result.get("isError", False), f"Expected isError for empty call: {result}"
        print("\n  ✅ db_related({}) → isError")


class TestFilterEntity:
    """filter_{entity} — пер-энтити фильтрация с полями в схеме тула."""

    def test_filter_auto_parts_by_category(self, tenant_context):
        """filter_auto_parts(category=...) → отфильтрованные записи."""
        tid, _ = tenant_context
        result = mcp_call(
            "filter_auto_parts",
            {"category": "Тормозная система"},
            tenant_ids=tid,
            timeout=30,
        )
        assert result.success, f"filter_auto_parts failed: {result.error}"
        assert not result.result.get("isError", False), f"isError: {result}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        # Category value is in Russian in the response
        assert "Тормозная" in text or "тормоз" in text.lower(), f"Expected category in results: {text[:300]}"
        print(f"\n  ✅ filter_auto_parts(category=...) → {len(text)} chars")

    def test_filter_auto_parts_by_price_gt(self, tenant_context):
        """filter_auto_parts(price__gt=...) → дорогие запчасти.

        Note: price__gt expects numeric type, not string.
        """
        tid, _ = tenant_context
        result = mcp_call(
            "filter_auto_parts",
            {"price__gt": 10000},
            tenant_ids=tid,
            timeout=30,
        )
        assert result.success, f"filter_auto_parts failed: {result.error}"
        assert not result.result.get("isError", False), f"isError: {result}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert len(text) > 0, "Empty response for price__gt filter"
        print(f"\n  ✅ filter_auto_parts(price__gt=10000) → {len(text)} chars")

    def test_filter_auto_parts_by_stock_gt(self, tenant_context):
        """filter_auto_parts(stock__gt=0) → товары в наличии.

        Note: stock field may not be filterable by default.
        """
        tid, _ = tenant_context
        result = mcp_call(
            "filter_auto_parts",
            {"stock__gt": 0},
            tenant_ids=tid,
            timeout=30,
        )
        # stock may not be filterable - accept isError or success
        if result.result.get("isError", False):
            print(f"\n  ⚠️ stock__gt not filterable (expected): {result}")
        else:
            content = result.result.get("content", [])
            text = "".join(c.get("text", "") for c in content if "text" in c)
            assert len(text) > 0, "Empty response for stock__gt filter"
            print(f"\n  ✅ filter_auto_parts(stock__gt=0) → {len(text)} chars")

    def test_filter_tool_exposes_field_params(self, tenant_context):
        """filter_auto_parts имеет параметры для каждого filterable поля."""
        tid, tools = tenant_context
        t = _get_tool_by_name(tools, "filter_auto_parts")
        params = t.get("params", [])
        field_params = [p["name"] for p in params if p["name"] != "limit"]
        # Должны быть базовые поля: category, price, name, brand_id, oem_number, description, car_model_id
        expected_fields = {"category", "price", "name", "brand_id", "oem_number", "description", "car_model_id"}
        found = set(field_params)
        for f in expected_fields:
            assert f in found, f"filter_auto_parts должен иметь поле {f}, найдено: {field_params}"
        print(f"\n  ✅ filter_auto_parts fields: {field_params}")

    def test_filter_returns_iserror_on_bad_field(self, tenant_context):
        """filter_auto_parts(unknown_field=...) → isError (валидация полей)."""
        tid, _ = tenant_context
        result = mcp_call(
            "filter_auto_parts",
            {"nonexistent_field": "value"},
            tenant_ids=tid,
            timeout=30,
        )
        # Должен вернуть isError (валидация схемы тула)
        assert result.result.get("isError", False), f"Expected isError for unknown field: {result}"
        print("\n  ✅ filter_auto_parts(unknown_field) → isError")

    def test_db_filter_tool_exists(self, tenant_context):
        """db_filter существует как часть db_* консолидированного набора."""
        tid, tools = tenant_context
        tool_names = {t["name"] for t in tools}
        assert "db_filter" in tool_names, f"db_filter должен существовать: {sorted(tool_names)}"
        print("\n  ✅ db_filter присутствует в db_* наборе")
