"""E2E: v5 tool surface через новую архитектуру (TestTenant + rewrite).

Демонстрирует паттерн добавления нового теста: scenario + rewrite →
готовый тенант с v5-инструментами. Без ручного setup/teardown.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def auto_shop():
    """auto-shop тенант с filterable_rules (v5 filter_* инструменты)."""
    from tests.e2e.helpers import make_tenant

    rules = [
        {"entity": "auto_parts", "field": "category"},
        {"entity": "auto_parts", "field": "price"},
    ]
    t = make_tenant("auto-shop", prefix="v5", filterable_rules=rules)
    t.register(rewrite=True)
    yield t
    t.cleanup()


def test_v5_has_db_tools(auto_shop):
    """5 консолидированных db_* инструментов."""
    names = [x["name"] for x in auto_shop.tools()]
    for db_tool in ["db_map", "db_describe", "db_search", "db_get", "db_related"]:
        assert db_tool in names, f"missing {db_tool}: {names}"


def test_v5_has_filter_entity(auto_shop):
    """Пер-энтити filter_{entity} из filterable_rules."""
    names = [x["name"] for x in auto_shop.tools()]
    assert "filter_auto_parts" in names, f"no filter_auto_parts: {names}"


def test_v5_filter_works(auto_shop):
    """filter_auto_parts(price__gt=...) возвращает данные."""
    result = auto_shop.mcp_call(
        "filter_auto_parts", {"price__gt": 100}, timeout=30
    )
    assert result, f"filter_auto_parts failed: {result.error}"
    assert not result.result.get("isError", False), (
        f"filter returned isError: {result.result}"
    )
