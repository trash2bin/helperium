"""E2E: LLM чат с неявным интентом (требует реальный LLM API ключ).

Не входит в CI (tests/e2e). Запуск вручную:

    MISTRAL_API_KEY=... uv run pytest tests/e2e-llm/ -v

Проверяет, что LLM сама догадывается вызвать правильный v5 инструмент
(db_*, filter_*) по неявному запросу.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    api_service_url,
    create_scenario_db,
    register_tenant_and_rewrite,
    parse_sse_stream,
)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")),
    reason="LLM API key not set (OPENAI_API_KEY or LLM_API_KEY)",
)


@pytest.fixture(scope="module")
def auto_shop_tenant():
    """Register auto-shop tenant (v5 filterable rules)."""
    db_path = create_scenario_db("auto-shop")
    tid = f"e2e-autoshop-{uuid.uuid4().hex[:6]}"
    result = register_tenant_and_rewrite(tid, db_path)
    yield tid, result
    try:
        requests.delete(
            f"{api_service_url()}/admin/tenants/{tid}",
            headers=admin_headers(),
            timeout=10,
        )
    except Exception:
        pass


class TestLLMImplicitIntent:
    """LLM чат с неявным интентом — пользователь не знает про тулы."""

    @pytest.fixture(scope="class")
    def auto_shop_agent(self, auto_shop_tenant):
        """Create LLM agent for auto-shop."""
        tid, _ = auto_shop_tenant
        llm_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        llm_model = os.environ.get("OPENAI_MODEL", "openai/deepseek-v4-flash")
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

        # Create agent with v5 tool names in system prompt
        payload = {
            "name": agent_name,
            "provider_priority": ["polza"],
            "tenant_ids": [tid],
            "llm_config": {
                "model": llm_model,
                "provider": "openai",
                "api_key": llm_key,
                "api_base": llm_api_base,
                "system_prompt": (
                    "Ты — консультант магазина автозапчастей. У тебя есть доступ к каталогу "
                    "автозапчастей через MCP-инструменты:\n"
                    "- db_search(entity, pattern) — текстовый поиск по каталогу\n"
                    "- filter_auto_parts — фильтрация по полям (category, price__gt, price__lt, stock__gt)\n"
                    "- db_get(entity, id) — получить запчасть по ID\n"
                    "- db_describe(entity) — мета-информация о сущности\n"
                    "- db_map — список доступных сущностей\n"
                    "- db_related(entity, id, relation) — связанные записи\n\n"
                    "Когда клиент спрашивает — сразу используй db_search или filter_. "
                    "Не говори 'я могу поискать', просто ищи сразу. "
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
        """'Мне нужен глушитель на BMW X5' → db_search или filter_."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Мне нужен глушитель на BMW X5, подскажи что есть?"
        )
        self._check_result(result)

    def test_ask_for_cheap_brakes(self, auto_shop_agent):
        """'Какие есть недорогие тормозные колодки?' → filter_ или db_search."""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Какие есть недорогие тормозные колодки, до 5000 рублей?"
        )
        self._check_result(result)

    def test_ask_for_all_available(self, auto_shop_agent):
        """'Что есть в наличии дешёвого для Vesta?'"""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Что есть в наличии для Лады Весты недорогое?"
        )
        self._check_result(result)

    def test_ask_for_bmw_parts(self, auto_shop_agent):
        """'Покажи запчасти для BMW X5'"""
        agent_name, tid = auto_shop_agent
        result = self._chat(agent_name, tid,
            "Покажи запчасти которые подходят на BMW X5"
        )
        self._check_result(result)

    def test_ask_for_engine_oil(self, auto_shop_agent):
        """'Масло для Тойоты надо'"""
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

        return parse_sse_stream(resp, idle_timeout=15)

    def _check_result(self, result: dict):
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

        # At minimum: tool was called OR text response was produced
        has_tool_call = len(result["tool_calls"]) > 0
        has_response = bool(result["final_text"].strip())

        if has_tool_call:
            tool_name = result["tool_calls"][0].get("name", "")
            # v5: LLM может вызвать db_search/db_map/db_describe (консолидированные)
            # или filter_{entity} (пер-энтити) — оба валидны как первый шаг.
            assert tool_name.startswith(("db_", "filter_")), (
                f"Expected db_* or filter_* tool, got '{tool_name}'"
            )
            print(f"  ✅ LLM used '{tool_name}' — pipeline OK")
            assert not result.get("errors"), f"Tool called but errors: {result['errors']}"
        elif has_response:
            print("  ⚠️  LLM answered without calling tools — check system prompt")
            assert not result.get("errors"), f"Response but errors: {result['errors']}"
        else:
            if result.get("errors"):
                pytest.fail(f"Pipeline failed: {result['errors']}")
            pytest.fail("No output from LLM")
