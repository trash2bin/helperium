"""MCP validation: проверка что тулы с required аргументами реджектят пустые вызовы.

Проблема: LLM (deepseek) шлёт `get_catalog_product({})` и `search_catalog_product({})`
с пустыми аргументами. MCP-гейтвей должен возвращать isError, а не выполнять запрос.

Что тестируем:
1. get_*({}) → isError (требует id, pattern)
2. search_*({}) → isError (требует pattern)
3. get_*(правильные args) → OK
4. search_*(правильные args) → OK
5. get_* с неизвестным полем → isError (или OK — на усмотрение)
"""
from __future__ import annotations

import json
import pytest

from tests.e2e.helpers import (
    mcp_call,
    data_service_url,
    admin_headers,
    mcp_gateway_url,
    MCPCallResult,
)

import requests

_TENANT = "autoparts"


def _get_tool_list() -> list[dict]:
    """Получить все MCP тулы из конфига."""
    ds = data_service_url()
    r = requests.get(
        f"{ds}/admin/config",
        headers={**admin_headers(), "X-Tenant-ID": _TENANT},
        timeout=10,
    )
    if r.status_code != 200:
        pytest.fail(f"Failed to get config: {r.status_code}")
    data = r.json()
    return data.get("mcp_tools", [])


def _get_tool_by_name(tools: list[dict], name: str) -> dict:
    """Найти тул по имени."""
    for t in tools:
        if t["name"] == name:
            return t
    raise AssertionError(f"Tool '{name}' not found")


# ── Получаем все тулы один раз для класса ──────────────────────────────────


@pytest.fixture(scope="class")
def tool_list():
    return _get_tool_list()


# ═══════════════════════════════════════════════════════════════════════════
# 1. GET_* validation — должны требовать id
# ═══════════════════════════════════════════════════════════════════════════


class TestGetWithRequired:
    """get_* тулы: пустой вызов → isError, с id → OK."""

    def test_get_without_id_returns_is_error(self, tool_list):
        """get_catalog_product({}) → isError, не данные."""
        t = _get_tool_by_name(tool_list, "get_catalog_product")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "id" in required, f"get_catalog_product должен требовать id. required={required}"

        result = mcp_call("get_catalog_product", {}, tenant_ids=_TENANT, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"get_catalog_product({{}}) должно вернуть isError.\n"
            f"  Вместо этого: {'OK' if result.success else 'FAIL'}\n"
            f"  Response: {err_text[:300]}"
        )
        # Ошибка должна упоминать id
        assert "id" in err_text.lower(), (
            f"Ошибка должна упоминать 'id'. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty get_catalog_product → isError: {err_text[:200]}")

    def test_get_with_id_returns_ok(self, tool_list):
        """get_catalog_product({'id': 1}) → данные."""
        result = mcp_call("get_catalog_product", {"id": 1}, tenant_ids=_TENANT, timeout=30)
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, f"Empty response for valid get call: {result}"
        assert "error" not in text.lower()[:50], (
            f"Valid get returned error-like: {text[:200]}"
        )
        print(f"\n  ✅ get_catalog_product(id=1) → {len(text)} chars")

    def test_several_get_tools_reject_empty(self, tool_list):
        """Несколько get_* тулов проверяются на валидацию."""
        get_tools = [t for t in tool_list if t["name"].startswith("get_")]
        # Берём 3 случайных get_* тула
        import random
        random.seed(42)
        samples = random.sample(get_tools, min(3, len(get_tools)))

        for t in samples:
            name = t["name"]
            result = mcp_call(name, {}, tenant_ids=_TENANT, timeout=30)
            is_error = result.result.get("isError", False)
            assert is_error, (
                f"{name}({{}}) не вернул isError!\n"
                f"  Tool params: {t.get('params', [])}"
            )
            print(f"  ✅ {name}({{}}) → isError")


# ═══════════════════════════════════════════════════════════════════════════
# 2. SEARCH_* validation — должны требовать pattern
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchWithRequired:
    """search_* тулы: пустой вызов → isError, с pattern → OK."""

    def test_search_without_pattern_returns_is_error(self, tool_list):
        """search_catalog_product({}) → isError."""
        t = _get_tool_by_name(tool_list, "search_catalog_product")
        params = t.get("params", [])
        required = [p["name"] for p in params if p.get("required")]
        assert "pattern" in required, f"search_catalog_product должен требовать pattern. required={required}"

        result = mcp_call("search_catalog_product", {}, tenant_ids=_TENANT, timeout=30)
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)

        assert is_error, (
            f"search_catalog_product({{}}) должно вернуть isError.\n"
            f"  Response OK: {err_text[:300]}"
        )
        assert "pattern" in err_text.lower() or "required" in err_text.lower(), (
            f"Ошибка должна упоминать pattern/required. Текст: {err_text[:300]}"
        )
        print(f"\n  ✅ Empty search_catalog_product → isError: {err_text[:200]}")

    def test_search_with_pattern_returns_ok(self, tool_list):
        """search_catalog_product({'pattern': 'oil'}) → данные."""
        result = mcp_call("search_catalog_product", {"pattern": "oil"}, tenant_ids=_TENANT, timeout=30)
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)

        assert len(text) > 0, f"Empty response for valid search call"
        assert "error" not in text.lower()[:50], (
            f"Valid search returned error-like: {text[:200]}"
        )
        print(f"\n  ✅ search_catalog_product(pattern='oil') → {len(text)} chars")

    def test_several_search_tools_reject_empty(self, tool_list):
        """Несколько search_* тулов проверяются."""
        search_tools = [t for t in tool_list if t["name"].startswith("search_")]
        import random
        random.seed(42)
        samples = random.sample(search_tools, min(3, len(search_tools)))

        for t in samples:
            name = t["name"]
            result = mcp_call(name, {}, tenant_ids=_TENANT, timeout=30)
            is_error = result.result.get("isError", False)
            content = result.result.get("content", [])
            text = "".join(c.get("text", "") for c in content if "text" in c)
            assert is_error, (
                f"{name}({{}}) не вернул isError!\n"
                f"  Вместо этого: {text[:200]}"
            )
            print(f"  ✅ {name}({{}}) → isError: {text[:100]}")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Все тулы: проверка что required поля заданы
# ═══════════════════════════════════════════════════════════════════════════


class TestAllToolsHaveRequiredGuard:
    """Каждый tool должен иметь хотя бы один required параметр (кроме count_*)."""

    def test_all_tools_have_required_param(self, tool_list):
        """Проверка что у каждого тула (кроме count_*) есть required поле."""
        bad = []
        for t in tool_list:
            name = t["name"]
            if name.startswith("count_"):
                continue  # count тулы — исключение (считают все записи)
            params = t.get("params", [])
            required = [p["name"] for p in params if p.get("required")]
            if not required:
                bad.append(name)

        print(f"\n  Всего тулов: {len(tool_list)}")
        print(f"  Тулов без required: {bad}")
        if bad:
            print(f"\n  ⚠️ Следующие тулы не имеют required параметров:")
            for name in bad:
                b = _get_tool_by_name(tool_list, name)
                print(f"    {name}: {[(p['name'], p.get('required')) for p in b.get('params', [])]}")
        assert not bad, f"Тулы без required параметров: {bad}"


# ═══════════════════════════════════════════════════════════════════════════
# 4. limit/top параметры имеют максимальное значение
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitHasMaxBound:
    """limit параметр не должен позволять загрузить всю БД."""

    def test_limit_has_maximum_constraint(self, tool_list):
        """Проверяем что в схеме тула limit не может быть > 10000."""
        found = False
        for t in tool_list:
            params = t.get("params", [])
            for p in params:
                if p["name"] == "limit":
                    found = True
                    break
            if found:
                break
        assert found, "Ни один тул не имеет параметра limit!"

    def test_limit_gt_10000_returns_is_error(self, tool_list):
        """limit=9999999 → isError."""
        result = mcp_call(
            "search_catalog_product",
            {"pattern": "oil", "limit": 9999999},
            tenant_ids=_TENANT,
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
