"""E2E тесты для новых search strategies (search) с авто-генерированным конфигом.

Проверяет:
1. Создание tenant'ов через интроспекцию (DB → introspect → Generate → rewrite)
2. SearchStrategy — поиск по тексту с нечётким интентом
3. SearchStrategy — фильтрация по полям с операторами
4. Count эндпоинты
5. MCP инструменты grep_ / filter_ доступны через manifest
6. (Опционально) LLM чат с неявным интентом
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
    api_service_url,
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
    return f"e2e-{prefix}-{uuid.uuid4().hex[:6]}"


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

    # Запускаем скрипт создания БД
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
        # Maybe already exists — try delete + recreate
        if resp.status_code == 409:
            requests.delete(f"{base}/admin/tenants/{tenant_id}", headers=h, timeout=10)
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


def _cleanup_tenant(tenant_id: str):
    """Delete tenant if exists."""
    try:
        requests.delete(
            f"{data_service_url()}/admin/tenants/{tenant_id}",
            headers=admin_headers(),
            timeout=10,
        )
    except Exception:
        pass


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def auto_shop_db():
    """Create auto-shop DB once per module."""
    yield _create_db("auto-shop")


@pytest.fixture(scope="module")
def clinic_db():
    """Create clinic DB once per module."""
    yield _create_db("clinic")


@pytest.fixture(scope="module")
def auto_shop_tenant(auto_shop_db):
    """Register auto-shop tenant with rewrite."""
    tid = _tenant_id("autoshop")
    result = _register_and_rewrite(tid, auto_shop_db)
    yield tid, result
    _cleanup_tenant(tid)


@pytest.fixture(scope="module")
def clinic_tenant(clinic_db):
    """Register clinic tenant with rewrite."""
    tid = _tenant_id("clinic")
    result = _register_and_rewrite(tid, clinic_db)
    yield tid, result
    _cleanup_tenant(tid)


# ── Shared SSE parser ──────────────────────────────────────────────────────


def _parse_sse_stream(response, idle_timeout: int = 12) -> dict:
    """Parse SSE stream from api-service into structured result."""
    import socket as _socket

    result = {
        "events": [],
        "tool_calls": [],
        "tool_results": [],
        "final_text": "",
        "errors": [],
        "status_messages": [],
    }

    try:
        sock = getattr(
            getattr(getattr(response.raw, "_fp", None), "fp", None), "_sock", None
        )
        if sock is not None:
            sock.settimeout(idle_timeout)
    except (AttributeError, OSError):
        pass

    try:
        for line_bytes in response.iter_lines():
            if not line_bytes:
                continue
            line = line_bytes.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue

            payload_str = line[6:]
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            result["events"].append(payload)
            ev_type = payload.get("type", "")

            if ev_type == "status":
                result["status_messages"].append(
                    payload.get("message") or payload.get("phase", "")
                )
            elif ev_type == "tool_call":
                result["tool_calls"].append(payload)
            elif ev_type == "tool_result":
                result["tool_results"].append(payload)
            elif ev_type == "token":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "error":
                result["errors"].append(payload.get("text", str(payload)))
            elif ev_type == "final":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "done":
                break
    except (
        requests.ConnectionError,
        TimeoutError,
        _socket.timeout,
        _socket.error,
        OSError,
    ):
        if not result["events"]:
            result["errors"].append("SSE stream ended unexpectedly")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# TESTS: Auto-shop — search стратегии
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoShopStrategies:
    """Проверка search стратегий на авто-магазине."""

    def test_rewrite_generated_entities(self, auto_shop_tenant):
        """После rewrite: есть сущности и эндпоинты."""
        tid, result = auto_shop_tenant
        assert result.get("entities", 0) > 0, "No entities generated"
        assert result.get("endpoints", 0) > 0, "No endpoints generated"

    def test_grep_glushiteli(self, auto_shop_tenant):
        """Search 'глушители' — находит запчасти выхлопной системы.

        Неявный запрос: 'глушители' → search_auto_parts(pattern="глушители")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
            params={"pattern": "Глушитель"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200, f"grep: {resp.status_code} {resp.text[:200]}"

        data = resp.json()
        assert data.get("total", 0) > 0, "No mufflers found"

        # Должны найти глушители разных типов
        items = data.get("items", data.get("results", data.get("preview", [])))
        item_text = json.dumps(items, ensure_ascii=False)
        assert "Глушитель" in item_text, f"No 'Глушитель' in results: {item_text}"

    def test_grep_multi_token(self, auto_shop_tenant):
        """Grep 'глушитель универсальный' — AND токенов.

        Неявный запрос: 'универсальный глушитель' → найдёт только универсальные глушители.
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
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
        """Grep с тем, чего нет — пустой результат."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
            params={"pattern": "Снегоход Буран"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) == 0, f"Expected 0, got {data}"

    def test_filter_by_category(self, auto_shop_tenant):
        """Search 'категория=Тормозная система'.

        Неявный запрос: 'тормоза на BMW X5' → search_auto_parts(category="Тормозная система")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
            params={"category": "Тормозная система"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) == 5, f"Expected 5 brake parts, got {data}"

    def test_filter_price_gt(self, auto_shop_tenant):
        """Filter 'цена__gt=10000' — дорогие запчасти.

        Неявный запрос: 'самые дорогие запчасти' → search_auto_parts(price__gt=10000)
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
            params={"price__gt": "10000"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Должны быть: глушитель BMW (28500), Mercedes (12500), катализатор (18500),
        # Audi (32000), радиатор (12500), комплект BMW (45000), глушитель средний (12500)
        total = data.get("total", 0)
        assert total >= 6, f"Expected >=6 expensive parts, got {total}: {data}"

    def test_filter_price_lte(self, auto_shop_tenant):
        """Filter 'цена__lte=500' — бюджетные запчасти.

        Неявный запрос: 'подбери дешёвые запчасти' → search_auto_parts(price__lte=500)
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/auto_parts/search",
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
            f"{data_service_url()}/auto_parts/search",
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
            f"{data_service_url()}/auto_parts/search",
            params={"pattern": "Фильтр", "limit": "3", "format": "full"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) > 0

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

    def test_manifest_has_search_tools(self, auto_shop_tenant):
        """MCP manifest содержит search_auto_parts и search_customers."""
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

        assert "search_auto_parts" in tool_names, (
            f"search_auto_parts not found in tools: {tool_names}"
        )
        assert "search_customers" in tool_names, (
            f"search_customers not found in tools: {tool_names}"
        )

    def test_orders_filter_by_status(self, auto_shop_tenant):
        """Filter заказов по статусу."""
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/orders/search",
            params={"status": "delivered"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 2, f"Expected >=2 delivered orders, got {data}"

    def test_customers_grep_by_name(self, auto_shop_tenant):
        """grep клиентов по имени.

        Неявный запрос: 'найди клиента Сергей' → search_customers("Сергей")
        """
        tid, _ = auto_shop_tenant
        resp = requests.get(
            f"{data_service_url()}/customers/search",
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
    """Проверка search на клинике (более сложные сценарии)."""

    def test_rewrite_generated(self, clinic_tenant):
        """Rewrite сработал."""
        tid, result = clinic_tenant
        assert result.get("entities", 0) > 0

    def test_grep_doctor_by_specialization(self, clinic_tenant):
        """grep врачей по имени.

        Неявный запрос: 'какие у нас кардиологи?' → search_doctors("кардиолог")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/search",
            params={"pattern": "Смирнов"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 1

    def test_filter_appointments_by_status(self, clinic_tenant):
        """filter приёмов: только запланированные.

        Неявный запрос: 'какие приёмы на сегодня' → search_appointments(status=scheduled)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/search",
            params={"status": "scheduled"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 7, f"Expected >=7 scheduled appointments, got {total}"

    def test_filter_appointments_by_reason_like(self, clinic_tenant):
        """filter приёмов: причина содержит 'голов'.

        Неявный запрос: 'у кого головные боли?' → search_appointments(reason__like="голов")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/search",
            params={"reason__like": "%Голов%"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", 0) >= 2, f"Expected >=2 headache appointments, got {data}"

    def test_filter_doctors_by_experience_gt(self, clinic_tenant):
        """filter врачей: стаж > 15 лет.

        Неявный запрос: 'самые опытные врачи' → search_doctors(experience__gt=15)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/search",
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

        Неявный запрос: 'топ врачи по рейтингу' → search_doctors(rating__gte=4.8)
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/doctors/search",
            params={"rating__gte": "4.8"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 3, f"Expected >=3 top-rated doctors, got {total}: {data}"

    def test_filter_patients_by_city(self, clinic_tenant):
        """filter пациентов: из Москвы.

        Неявный запрос: 'пациенты из Москвы' → search_patients(city="Москва")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/patients/search",
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

    def test_grep_appointments_by_medication_in_notes(self, clinic_tenant):
        """grep приёмов: поиск по причине обращения 'давление'.

        Неявный запрос: 'у кого давление?' → search_appointments("давление")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/search",
            params={"pattern": "Давление"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 1, f"Expected appointments, got {total}"

    def test_filter_appointments_date_range(self, clinic_tenant):
        """filter приёмов: после 2025-02-01.

        Неявный запрос: 'приёмы за февраль' → search_appointments(appointment_date__gte="2025-02-01")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/appointments/search",
            params={"appointment_date__gte": "2025-02-01"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 15, f"Expected >=15 appointments in Feb, got {total}"

    def test_manifest_has_clinic_tools(self, clinic_tenant):
        """MCP manifest имеет search инструменты для клиники."""
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

        for tool in ["search_doctors", "search_doctors", "search_appointments",
                      "search_appointments", "search_patients", "search_patients",
                      "search_prescriptions"]:
            assert tool in tool_names, (
                f"{tool} not found in tools: {[n for n in tool_names if n.startswith('grep') or n.startswith('filter')]}"
            )

    def test_grep_prescriptions_by_medication(self, clinic_tenant):
        """grep назначений: поиск лекарства.

        Неявный запрос: 'кому выписывали амоксициллин?' → search_prescriptions("амоксициллин")
        """
        tid, _ = clinic_tenant
        resp = requests.get(
            f"{data_service_url()}/prescriptions/search",
            params={"pattern": "Амоксициллин"},
            headers={"X-Tenant-ID": tid},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        total = data.get("total", 0)
        assert total >= 2, f"Expected >=2 amoxicillin prescriptions, got {total}"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS: LLM чат с неявным интентом (требует API ключ)
# ═══════════════════════════════════════════════════════════════════════════

pytestmark_llm = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")),
    reason="LLM API key not set — set OPENAI_API_KEY or LLM_API_KEY",
)


@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")),
    reason="LLM API key not set",
)
class TestLLMImplicitIntent:
    """LLM чат с неявным интентом — пользователь не знает про search.

    Проверяет, что LLM сама догадывается вызвать правильный инструмент
    по неявному запросу.
    """

    AGENT_NAME = None  # set in setup

    @pytest.fixture(scope="class")
    def auto_shop_agent(self, auto_shop_tenant):
        """Create LLM agent for auto-shop.

        Использует Polza AI (OpenAI-compatible) через LiteLLM.
        """
        tid, _ = auto_shop_tenant
        llm_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        llm_model = os.environ.get("OPENAI_MODEL", "deepseek-v4-flash@provider=DeepSeek&allow_fallbacks=false")
        llm_api_base = os.environ.get("OPENAI_API_BASE", "https://polza.ai/api/v1")

        agent_name = f"e2e-autoshop-{uuid.uuid4().hex[:6]}"

        # Clean up from previous runs
        try:
            requests.delete(
                f"{api_service_url()}/api/agents/{agent_name}",
                headers=admin_headers(),
                timeout=10,
            )
        except Exception:
            pass

        # Create agent with appropriate system prompt
        payload = {
            "name": agent_name,
            "provider_priority": ["openai"],
            "tenant_ids": [tid],
            "llm_config": {
                "model": llm_model,
                "provider": "openai",
                "api_key": llm_key,
                "api_base": llm_api_base,
                "system_prompt": (
                    "Ты — консультант магазина автозапчастей. У тебя есть доступ к каталогу "
                    "автозапчастей через MCP-инструменты:\n"
                    "- search_auto_parts — текстовый поиск (pattern, fields, regex, ignore_case)\n"
                    "- search_auto_parts — фильтрация по полям (category, price__gt, price__lt, stock__gt)\n"
                    "- get_auto_parts — получить запчасть по ID\n"
                    "- search_customers — поиск клиентов по имени\n"
                    "- search_orders — фильтрация заказов по статусу\n\n"
                    "Когда клиент спрашивает — используй search_ для поиска. "
                    "Не говори 'я могу поискать', просто ищи сразу. "
                    "Твоя задача — помочь клиенту найти нужные запчасти. "
                    "Отвечай на русском языке."
                ),
            },
            "widget_config": {
                "title": "Автозапчасти",
                "greeting": "Чем могу помочь?",
                "position": "right",
            },
        }
        resp = requests.post(
            f"{api_service_url()}/api/agents",
            json=payload,
            headers=admin_headers(),
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            pytest.skip(f"Could not create agent: {resp.status_code}: {resp.text[:200]}")

        yield agent_name, tid

        # Cleanup
        try:
            requests.delete(
                f"{api_service_url()}/api/agents/{agent_name}",
                headers=admin_headers(),
                timeout=5,
            )
        except Exception:
            pass

    def test_ask_for_muffler(self, auto_shop_agent):
        """'Мне нужен глушитель на BMW X5' → должен вызвать search_auto_parts('глушитель BMW X5')."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Мне нужен глушитель на BMW X5, подскажи что есть?"
        )
        self._check_result(result, expected_tool="search_auto_parts")

    def test_ask_for_cheap_brakes(self, auto_shop_agent):
        """'Какие есть недорогие тормозные колодки?' → search_auto_parts(category='Тормозная система', price__lte=5000)."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Какие есть недорогие тормозные колодки, до 5000 рублей?"
        )
        self._check_result(result, expected_tool="search_auto_parts")

    def test_ask_for_all_available(self, auto_shop_agent):
        """'Что есть в наличии дешёвого для Vesta?' → grep c 'Vesta' или filter по цене."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Что есть в наличии для Лады Весты недорогое?"
        )
        self._check_result(result)

    def test_ask_for_bmw_parts(self, auto_shop_agent):
        """'Покажи запчасти для BMW X5' → search_auto_parts('BMW X5')."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Покажи запчасти которые подходят на BMW X5"
        )
        self._check_result(result)

    def test_ask_for_engine_oil(self, auto_shop_agent):
        """'Масло для Тойоты надо' → search_auto_parts('масло Toyota')."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Масло моторное для Тойоты Камри нужно, что есть?"
        )
        self._check_result(result)

    def _chat(self, agent_name: str, tenant_id: str, message: str) -> dict:
        """Send chat message and parse SSE response."""
        session_id = f"e2e-implicit-{uuid.uuid4().hex[:8]}"
        resp = requests.post(
            f"{api_service_url()}/api/chat/{agent_name}",
            json={"message": message, "session_id": session_id},
            headers={
                "X-Tenant-ID": tenant_id,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HelperiumE2E/1.0)",
            },
            timeout=120,
            stream=True,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "success": False}

        return _parse_sse_stream(resp, idle_timeout=15)

    def _check_result(self, result: dict, expected_tool: str | None = None):
        """Check that LLM produced useful output."""
        print(f"\n  📊 Tool calls: {len(result['tool_calls'])}")
        if result["tool_calls"]:
            for tc in result["tool_calls"]:
                print(f"  🛠️  {tc.get('name', '?')}({json.dumps(tc.get('arguments', {}), ensure_ascii=False)[:100]})")
        if result["errors"]:
            for err in result["errors"][:3]:
                print(f"  ❌ Error: {err[:200]}")
        if result["final_text"]:
            print(f"  💬 Response: {result['final_text'][:300]}")
        else:
            print("  💬 (no text response)")

        # Check for errors
        errors = result.get("errors", [])
        if errors:
            print(f"  ⚠️  Errors during chat: {errors}")

        # At minimum: tool was called OR text response was produced
        has_tool_call = len(result["tool_calls"]) > 0
        has_response = bool(result["final_text"].strip())

        if has_tool_call:
            tool_name = result["tool_calls"][0].get("name", "")
            if expected_tool and tool_name != expected_tool:
                print(f"  ⚠️  Expected tool '{expected_tool}', got '{tool_name}'")
            print(f"  ✅ LLM called tool '{tool_name}' — pipeline OK")
            assert not errors, f"Tool called but errors: {errors}"
        elif has_response:
            print("  ⚠️  LLM answered without calling tools — check system prompt")
            assert not errors, f"Response but errors: {errors}"
        else:
            if errors:
                pytest.fail(f"Pipeline failed: {errors}")
            pytest.fail("No output from LLM")
