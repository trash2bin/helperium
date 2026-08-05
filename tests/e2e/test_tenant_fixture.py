"""Пример расширяемой архитектуры e2e-тестов (TestTenant + factory fixtures).

Демонстрирует, как добавить новый тест за 10 строк:
  - tenant()  — один тенант (scenario-DB + register + авто-cleanup)
  - tenants() — несколько тенантов (изоляция, composite)

Сценарий 'clinic' — пример нового сценария (data-service/testdata/scenarios/clinic).
"""

from __future__ import annotations


from tests.e2e.helpers import data_service_url


def test_one_tenant_via_fixture(tenant):
    """Минимальный тест: создаём тенанта из scenario (rewrite авто)."""
    t = tenant("clinic")  # seed clinic DB + register (rewrite — нет config.json)

    assert t.id.startswith("e2e-")
    assert t.db_path.exists()

    # rewrite-сценарий: config генерируется на сервере, но тулы должны быть
    tools = t.tools()
    assert tools, f"clinic tenant should have MCP tools, got {tools}"


def test_one_tenant_rewrite(tenant):
    """Тот же тенант через introspection-rewrite (v5 инструменты)."""
    t = tenant("clinic", rewrite=True)

    tools = t.tools()
    names = [x["name"] for x in tools]
    # v5: консолидированные db_* + пер-энтити filter_{entity}
    assert any(n.startswith("db_") for n in names), f"no db_* tools: {names}"
    assert any(n.startswith("filter_") for n in names), f"no filter_* tools: {names}"


def test_two_tenants_isolation(tenants):
    """Изоляция: два тенанта из одного сценария, разные id."""
    a = tenants("sqlite-testseed")
    b = tenants("sqlite-testseed", prefix="other")

    assert a.id != b.id
    assert a.db_path != b.db_path

    # Оба зарегистрированы и отвечают
    for t in (a, b):
        r = _tenant_manifest(t.id)
        assert r == 200, f"tenant {t.id} not reachable: {r}"


def _tenant_manifest(tenant_id: str) -> int:
    import requests

    r = requests.get(
        f"{data_service_url()}/mcp/manifest",
        headers={"X-Tenant-ID": tenant_id},
        timeout=10,
    )
    return r.status_code
