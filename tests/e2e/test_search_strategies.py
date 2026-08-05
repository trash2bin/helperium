"""E2E тесты для новой архитектуры search strategies (v5).

Проверяет:
1. Создание tenant'ов через интроспекцию (DB → introspect → Generate → rewrite)
2. GrepStrategy — текстовый поиск (db_search)
3. FilterStrategy — фильтрация по полям (filter_{entity})
4. SchemaStrategy — discovery (db_describe)
5. Count/Distinct REST эндпоинты
6. MCP инструменты db_* + filter_* доступны через manifest

LLM-чат с неявным интентом — в tests/e2e-llm/ (требует API ключ).
"""

from __future__ import annotations

import json

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    data_service_url,
    ensure_scenario_db,
    register_tenant_and_rewrite,
    e2e_tenant_id,
    delete_tenant,
)

pytestmark = [
    pytest.mark.skipif(
        not admin_headers(),
        reason="ADMIN_TOKEN not set — register admin API calls",
    ),
]

# Default filterable rules for e2e tests (stock, city, experience, rating, reason)
E2E_FILTERABLE_RULES = [
    {
        "id": "e2e.allow",
        "allow_names": ["stock", "city", "experience", "rating", "reason"],
        "reason": "E2E: business fields for filter tests",
    },
]


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def auto_shop_db():
    """Create auto-shop DB once per module."""
    yield ensure_scenario_db("auto-shop")


@pytest.fixture(scope="module")
def clinic_db():
    """Create clinic DB once per module."""
    yield ensure_scenario_db("clinic")


@pytest.fixture(scope="module")
def auto_shop_tenant(auto_shop_db):
    """Register auto-shop tenant with rewrite."""
    tid = e2e_tenant_id("autoshop")
    result = register_tenant_and_rewrite(tid, auto_shop_db, E2E_FILTERABLE_RULES)
    yield tid, result
    delete_tenant(tid)


@pytest.fixture(scope="module")
def clinic_tenant(clinic_db):
    """Register clinic tenant with rewrite."""
    tid = e2e_tenant_id("clinic")
    result = register_tenant_and_rewrite(tid, clinic_db, E2E_FILTERABLE_RULES)
    yield tid, result
    delete_tenant(tid)


# ── Shared SSE parser ──────────────────────────────────────────────────────


class TestAutoShopStrategies:
    """Проверка grep/filter/schema стратегий на авто-магазине."""

    def test_rewrite_generated_entities(self, auto_shop_tenant):
        """После rewrite: есть сущности и эндпоинты."""
        tid, result = auto_shop_tenant
        assert result.get("entities", 0) > 0, "No entities generated"
        assert result.get("endpoints", 0) > 0, "No endpoints generated"

    def test_schema_auto_parts(self, auto_shop_tenant):
        """schema_{entity} — мета-информация о сущности.

        Первый шаг discovery: узнать какие есть поля, distinct values, count.
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/schema",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200, f"schema: {resp.status_code} {resp.text[:200]}"

        data = resp.json()
        assert data.get("total", 0) > 0, "No total count in schema"
        assert "fields" in data, f"No fields in schema: {list(data.keys())}"

        fields = data["fields"]
        assert "category" in fields, f"Expected category field, got: {list(fields.keys())}"
        assert "price" in fields, f"Expected price field, got: {list(fields.keys())}"

        print(f"\n  ✅ schema_auto_parts → total={data['total']}, fields={list(fields.keys())}")

        # Проверка что category содержит distinct значения
        cat = fields["category"]
        assert "distinct" in cat or "values" in cat, f"No distinct values for category: {list(cat.keys())}"
        values = cat.get("distinct", cat.get("values", []))
        assert len(values) > 1, f"Expected multiple category values, got: {values}"

    def test_grep_glushiteli(self, auto_shop_tenant):
        """Grep 'глушители' — находит запчасти выхлопной системы.

        Неявный запрос: 'глушители' → grep_auto_parts(pattern="глушители")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/grep",
            params={"pattern": "Глушитель"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200, f"grep: {resp.status_code} {resp.text[:200]}"

        data = resp.json()
        assert data.get("total", 0) > 0, "No mufflers found"

        items = data.get("items", data.get("results", data.get("preview", [])))
        item_text = json.dumps(items, ensure_ascii=False)
        assert "Глушитель" in item_text, f"No 'Глушитель' in results: {item_text}"

    def test_grep_multi_token(self, auto_shop_tenant):
        """Grep 'глушитель универсальный' — AND токенов.

        Multi-token AND: оба слова должны быть в результатах.
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/grep",
            params={"pattern": "Глушитель универсальный"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0

        items = data.get("items", data.get("results", data.get("preview", [])))
        item_text = json.dumps(items, ensure_ascii=False)

        # Должны быть универсальные (45мм и 52мм) — оба содержат слово "универсальный"
        assert "универсальный" in item_text

    def test_grep_not_found(self, auto_shop_tenant):
        """Grep с тем, чего нет — пустой результат с empty_hint."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/grep",
            params={"pattern": "Снегоход Буран"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) == 0, f"Expected 0, got {data}"
        # При total=0 должен быть empty_hint
        assert "empty_hint" in data, f"No empty_hint in response: {list(data.keys())}"
        hint = data["empty_hint"]
        assert "suggested_action" in hint, f"No suggested_action in empty_hint: {hint}"
        print(f"\n  ✅ grep empty → empty_hint: {hint['suggested_action'][:80]}")

    def test_grep_empty_pattern_error(self, auto_shop_tenant):
        """Grep без pattern — 400 ошибка."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/grep",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400 for empty pattern, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_filter_by_category(self, auto_shop_tenant):
        """Filter 'категория=Тормозная система'.

        Неявный запрос: 'тормоза на BMW X5' → filter_auto_parts(category="Тормозная система")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/filter",
            params={"category": "Тормозная система"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) == 5, f"Expected 5 brake parts, got {data}"

    def test_filter_price_gt(self, auto_shop_tenant):
        """Filter 'цена__gt=10000' — дорогие запчасти.

        Неявный запрос: 'самые дорогие запчасти' → filter_auto_parts(price__gt=10000)
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/filter",
            params={"price__gt": "10000"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 6, f"Expected >=6 expensive parts, got {total}: {data}"

    def test_filter_price_lte(self, auto_shop_tenant):
        """Filter 'цена__lte=500' — бюджетные запчасти.

        Неявный запрос: 'подбери дешёвые запчасти' → filter_auto_parts(price__lte=500)
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/filter",
            params={"price__lte": "500"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 2, f"Expected >=2 cheap parts, got {data}"

    def test_filter_in_stock(self, auto_shop_tenant):
        """Filter 'stock__gt=0' — товары в наличии."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/filter",
            params={"stock__gt": "0"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0

    def test_grep_with_limit_and_format_full(self, auto_shop_tenant):
        """grep format=full c limit.

        Неявный запрос: 'покажи подробную информацию по первым 3 запчастям'
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/grep",
            params={"pattern": "Фильтр", "limit": "3", "format": "full"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0
        returned = data.get("returned", data.get("count", 0))
        assert returned <= 3, f"Expected <=3 items, got {returned}"

    def test_distinct_brands_country(self, auto_shop_tenant):
        """distinct_{entity} — уникальные значения колонки (brands.country — string enum)."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/brands/distinct",
            params={"column": "country"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        values = data.get("values", data.get("distinct", []))
        assert len(values) > 1, f"Expected multiple country values, got: {values}"
        print(f"\n  ✅ distinct_brands(column='country') → {values}")

    def test_auto_parts_count(self, auto_shop_tenant):
        """count запчастей должен быть 35."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/count",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("count", 0) == 35, f"Expected 35 parts, got {data}"

    def test_manifest_has_correct_tools(self, auto_shop_tenant):
        """MCP manifest содержит консолидированные db_* и filter_{entity}, НЕ grep_*/schema_*."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/mcp/manifest",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        tools = data.get("mcp_tools", data.get("tools", []))
        tool_names = [t.get("name") for t in tools]

        # Должны быть консолидированные db_* тулы (v5)
        for db_tool in ("db_map", "db_describe", "db_search", "db_get", "db_related"):
            assert db_tool in tool_names, (
                f"{db_tool} not found in tools: {tool_names}"
            )

        # Должны быть пер-энтити filter_{entity} тулы
        assert "filter_auto_parts" in tool_names, (
            "filter_auto_parts not found"
        )
        assert "filter_brands" in tool_names, "filter_brands not found"
        assert "filter_orders" in tool_names, "filter_orders not found"

        # НЕ должно быть per-entity grep_*/schema_*/get_*/count_*/distinct_* тулов (v5)
        bad_old = [
            n for n in tool_names
            if n.startswith(("grep_", "schema_", "search_", "simple_", "find_", "list_", "get_", "count_", "distinct_"))
            or "_by_" in n
        ]
        assert len(bad_old) == 0, f"Legacy tools still present: {bad_old}"

        print(f"\n  ✅ Manifest: db_* + filter_* — нет легаси ({len(tool_names)} total)")

    def test_orders_filter_by_status(self, auto_shop_tenant):
        """Filter заказов по статусу."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/orders/filter",
            params={"status": "delivered"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 2, f"Expected >=2 delivered orders, got {data}"

    def test_customers_grep_by_name(self, auto_shop_tenant):
        """grep клиентов по имени.

        Неявный запрос: 'найди клиента Сергей' → grep_customers("Сергей")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/customers/grep",
            params={"pattern": "Сергей"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) == 1, f"Expected 1 Sergey, got {data}"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS: Clinic — более сложные сценарии
# ═══════════════════════════════════════════════════════════════════════════


class TestClinicStrategies:
    """Проверка grep/filter/schema на клинике."""

    def test_rewrite_generated(self, clinic_tenant):
        """Rewrite сработал."""
        tid, result = clinic_tenant
        assert result.get("entities", 0) > 0

    def test_schema_doctors(self, clinic_tenant):
        """schema_{entity} для врачей."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/schema",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0
        assert "fields" in data

        # Должны быть key-поля
        fields = data["fields"]
        for field_name in ("specialization", "experience", "rating"):
            assert field_name in fields, f"Expected '{field_name}' in schema fields: {list(fields.keys())}"

        print(f"\n  ✅ schema_doctors → total={data['total']}, fields={list(fields.keys())}")
        # Вывести distinct specialization
        spec = fields.get("specialization", {})
        values = spec.get("distinct", spec.get("values", []))
        if values:
            print(f"     specializations: {values}")

    def test_grep_doctor_by_name(self, clinic_tenant):
        """grep врачей по имени."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/grep",
            params={"pattern": "Смирнов"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 1

    def test_filter_appointments_by_status(self, clinic_tenant):
        """filter приёмов: только запланированные.

        Неявный запрос: 'какие приёмы на сегодня' → filter_appointments(status=scheduled)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/filter",
            params={"status": "scheduled"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 7, f"Expected >=7 scheduled appointments, got {total}"

    def test_filter_appointments_by_reason_like(self, clinic_tenant):
        """filter приёмов: причина содержит 'голов'."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/filter",
            params={"reason__like": "%Голов%"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 2, f"Expected >=2 headache appointments, got {data}"

    def test_filter_doctors_by_experience_gt(self, clinic_tenant):
        """filter врачей: стаж > 15 лет.

        Неявный запрос: 'самые опытные врачи' → filter_doctors(experience__gt=15)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/filter",
            params={"experience__gt": "15"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 2, f"Expected >=2 experienced doctors, got {total}"

    def test_filter_doctors_by_rating_gte(self, clinic_tenant):
        """filter врачей: рейтинг >= 4.8.

        Неявный запрос: 'топ врачи по рейтингу' → filter_doctors(rating__gte=4.8)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/filter",
            params={"rating__gte": "4.8"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 3, f"Expected >=3 top-rated doctors, got {total}: {data}"

    def test_filter_patients_by_city(self, clinic_tenant):
        """filter пациентов: из Москвы."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/patients/filter",
            params={"city": "Москва"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0, f"Expected Moscow patients, got {data}"

    def test_count_doctors(self, clinic_tenant):
        """count врачей = 10."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/count",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("count", 0) == 10, f"Expected 10 doctors, got {data}"

    def test_count_appointments(self, clinic_tenant):
        """count приёмов = 42."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/count",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("count", 0) == 42, f"Expected 42 appointments, got {data}"

    def test_grep_appointments_by_medication(self, clinic_tenant):
        """grep приёмов: поиск по полю 'reason'."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/grep",
            params={"pattern": "Давление"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 1, f"Expected appointments about pressure, got {total}"

    def test_filter_appointments_date_range(self, clinic_tenant):
        """filter приёмов: после 2025-02-01.

        Неявный запрос: 'приёмы за февраль' → filter_appointments(appointment_date__gte="2025-02-01")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/filter",
            params={"appointment_date__gte": "2025-02-01"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 15, f"Expected >=15 appointments in Feb, got {total}"

    def test_manifest_has_clinic_tools(self, clinic_tenant):
        """MCP manifest имеет консолидированные db_* + filter_* для клиники."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/mcp/manifest",
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        tools = data.get("mcp_tools", data.get("tools", []))
        tool_names = [t.get("name") for t in tools]

        # Консолидированные db_* тулы (v5)
        for db_tool in ("db_map", "db_describe", "db_search", "db_get", "db_related"):
            assert db_tool in tool_names, f"{db_tool} not found: {tool_names}"

        # Пер-энтити filter_{entity}
        assert "filter_doctors" in tool_names, "filter_doctors not found"
        assert "filter_appointments" in tool_names, "filter_appointments not found"
        assert "filter_patients" in tool_names, "filter_patients not found"

        # НЕ должно быть per-entity grep_*/schema_*/search_* тулов
        bad = [
            n for n in tool_names
            if n.startswith(("grep_", "schema_", "search_", "get_", "count_", "distinct_"))
        ]
        assert len(bad) == 0, f"Legacy per-entity tools still present: {bad}"

    def test_grep_prescriptions_by_medication(self, clinic_tenant):
        """grep назначений: поиск лекарства."""
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/prescriptions/grep",
            params={"pattern": "Амоксициллин"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 2, f"Expected >=2 amoxicillin prescriptions, got {total}"

    def test_filter_grep_combo(self, clinic_tenant):
        """LLM типично вызывает filter потом grep — раздельно.

        Сценарий: "найди кардиологов с опытом > 10 лет в Москве"
        1. filter_doctors(city="Москва", experience__gt=10)
        2. grep_doctors(pattern="кардиолог") если нужно сузить
        """
        tid, _ = clinic_tenant

        # Шаг 1: filter
        resp = requests.get(
            f"{data_service_url()}/doctors/filter",
            params={"city": "Москва", "experience__gt": "10"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        print(f"\n  ✅ Filter by city+experience → total={data.get('total', 0)}")

        # Шаг 2: grep если filter результатов много
        resp2 = requests.get(
            f"{data_service_url()}/doctors/grep",
            params={"pattern": "кардиолог", "limit": "5"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        print(f"  ✅ Grep 'кардиолог' → total={data2.get('total', 0)}")
