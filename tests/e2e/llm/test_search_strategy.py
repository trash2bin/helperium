"""E2E диагностика search стратегий: MCP-level (без LLM) + LLM diagnostic.

=== Часть 1: MCP Health Check ===
Проверяет инфраструктуру — grep_*/filter_*/schema_* тулы на уровне MCP (без LLM).
Это детерминированные тесты — они всегда проходят одинаково.

=== Часть 2: LLM Diagnostic ===
НЕ ТЕСТ, а диагностика — логирует поведение LLM с новыми тулами (grep/filter/schema).
Не содержит assert'ов на поведение модели (оно недетерминированно).
Только логи для анализа.

Требует:
- Все сервисы запущены
- autoparts tenant с переписанным конфигом
- Агент autoparts в api-service
- LLM провайдер — только для Части 2
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
import requests

from tests.e2e.helpers import (
    mcp_call,
    data_service_url,
    admin_headers,
)

# ── Tenant / Agent (существуют в системе) ──────────────────────────────────

_AGENT_NAME = "autoparts"
_AGENT_TENANT = "autoparts"
_LLM_REQUIRED = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")




# =============================================================================
# Part 1: MCP-level health checks (NO LLM required) — ДЕТЕРМИНИРОВАННЫЕ
# =============================================================================


def _cfg_tools_to_mcp_schema(cfg_tools: list[dict]) -> list[dict]:
    """Convert config.MCPTool to MCP wire format (inputSchema)."""
    result = []
    for t in cfg_tools:
        props = {}
        required = []
        for p in t.get("params", []):
            pname = p["name"]
            ptype = p.get("type", "string")
            schema_type = {"int": "integer", "float": "number", "bool": "boolean"}.get(ptype, "string")
            prop = {"type": schema_type}
            if "description" in p and p["description"]:
                prop["description"] = p["description"]
            if p.get("required") is True:
                required.append(pname)
            props[pname] = prop
        result.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": {"type": "object", "properties": props, "required": required},
        })
    return result


@pytest.mark.skipif(not _AGENT_TENANT, reason="no tenant configured")
class TestSearchMCP:
    """Проверка что grep_*/filter_*/schema_* тулы работают на уровне MCP (без LLM)."""

    @pytest.fixture(scope="class", autouse=True)
    def _fetch_tools(self, request):
        """Получить MCP тулы через admin config API (без SSE)."""
        ds = data_service_url()
        h = admin_headers()
        try:
            r = requests.get(
                f"{ds}/admin/config",
                headers={**h, "X-Tenant-ID": _AGENT_TENANT},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                tools = data.get("mcp_tools", [])
                request.cls.mcp_tools = _cfg_tools_to_mcp_schema(tools)
                return
        except Exception:
            pass
        request.cls.mcp_tools = []

    def test_grep_tools_names_and_schema(self):
        """grep_* и filter_* есть, search_*/simple_*/find_*/list_* нет."""
        assert len(self.mcp_tools) > 0, (
            f"No MCP tools — tenant {_AGENT_TENANT} needs config rewrite:\n"
            f"  curl -X POST -H 'Authorization: Bearer secret' -H 'X-Tenant-ID: {_AGENT_TENANT}' http://127.0.0.1:8084/admin/config/rewrite"
        )
        names = [t["name"] for t in self.mcp_tools]

        grep_tools = [n for n in names if n.startswith("grep_")]
        filter_tools = [n for n in names if n.startswith("filter_")]
        schema_tools = [n for n in names if n.startswith("schema_")]
        assert len(grep_tools) > 0, f"No grep_*! Names: {names}"
        assert len(filter_tools) > 0, f"No filter_*! Names: {names}"
        assert len(schema_tools) > 0, f"No schema_*! Names: {names}"

        bad_old = [n for n in names if n.startswith(("search_", "simple_", "find_", "list_"))]
        assert len(bad_old) == 0, f"Old tools still present: {bad_old}"
        print(f"\n  ✅ grep_* tools ({len(grep_tools)}): {grep_tools}")
        print(f"  ✅ filter_* tools ({len(filter_tools)}): {filter_tools}")
        print(f"  ✅ schema_* tools ({len(schema_tools)}): {schema_tools}")

        # JSON Schema первого grep тула
        first = [t for t in self.mcp_tools if t["name"].startswith("grep_")][0]
        props = first.get("inputSchema", {}).get("properties", {})
        required = first.get("inputSchema", {}).get("required", [])

        print(f"\n  🛠️  {first['name']} schema:")
        print(f"     required: {required}")
        for k, v in props.items():
            print(f"     - {k}: {v.get('type', '?')} desc={v.get('description', '')[:70]}")

        assert "pattern" in required, f"pattern should be REQUIRED! required={required}"
        assert "pattern" in props, f"pattern missing! props={list(props.keys())[:15]}"
        assert "limit" in props, "limit missing"

    def test_grep_catalog_product_with_pattern_returns_data(self):
        """grep_catalog_product(pattern='oil') → данные через MCP."""
        result = mcp_call(
            "grep_catalog_product", {"pattern": "oil"},
            tenant_ids=_AGENT_TENANT, timeout=30,
        )
        assert result, f"MCP call failed: {result.error}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert len(text) > 0, "Empty result"
        print(f"\n  ✅ grep_catalog_product(pattern='oil') → {len(text)} chars")

    def test_grep_catalog_product_without_params_returns_error(self):
        """grep_catalog_product({}) → isError с подсказкой."""
        result = mcp_call(
            "grep_catalog_product", {},
            tenant_ids=_AGENT_TENANT, timeout=15,
        )
        is_error = result.result.get("isError", False)
        content = result.result.get("content", [])
        err_text = "".join(c.get("text", "") for c in content if "text" in c)
        assert is_error or not result.success, f"Expected 400 for empty grep"
        assert "pattern" in err_text.lower() or "required" in err_text.lower()
        print(f"\n  ✅ Empty grep → isError with field hint")

    def test_filter_catalog_product(self):
        """filter_catalog_product(category='Brake') → данные."""
        result = mcp_call(
            "filter_catalog_product", {"category": "Brake"},
            tenant_ids=_AGENT_TENANT, timeout=30,
        )
        if not result or not result.result.get("content"):
            result = mcp_call(
                "filter_catalog_product", {"category": "Тормозная система"},
                tenant_ids=_AGENT_TENANT, timeout=30,
            )
        assert result, f"MCP call failed: {result.error}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert len(text) > 0, "Empty result"
        print(f"\n  ✅ filter_catalog_product(category=...) → {len(text)} chars")

    def test_schema_catalog_product(self):
        """schema_catalog_product() → мета-информация."""
        result = mcp_call(
            "schema_catalog_product", {},
            tenant_ids=_AGENT_TENANT, timeout=30,
        )
        assert result, f"MCP call failed: {result.error}"
        content = result.result.get("content", [])
        text = "".join(c.get("text", "") for c in content if "text" in c)
        assert len(text) > 0, "Empty result"
        assert "total" in text or "fields" in text or "entity" in text, \
            f"Schema response missing expected fields: {text[:200]}"
        print(f"\n  ✅ schema_catalog_product → {len(text)} chars")


# =============================================================================
# Part 2: LLM Diagnostic (NOT a test — no assertions on model behavior)
# =============================================================================


@pytest.mark.skipif(not _LLM_REQUIRED, reason="LLM API key not set")
class TestSearchLLMDiagnostic:
    """Диагностика LLM с grep/filter/schema стратегиями.

    НЕ содержит assert'ов на поведение модели (оно недетерминированно).
    Только логирует что модель сделала: какие тулы вызвала, с какими аргументами,
    какие ошибки получила.

    Анализируй логи руками, а не через этот код.
    """

    def _chat(self, message: str) -> dict:
        """Chat with existing agent via SSE. Returns parsed events."""
        from tests.e2e.helpers import api_service_url

        api = api_service_url()
        session_id = f"diag-{uuid.uuid4().hex[:8]}"

        r = requests.post(
            f"{api}/api/chat/{_AGENT_NAME}",
            json={"message": message, "session_id": session_id},
            headers={
                "X-Tenant-ID": _AGENT_TENANT,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HelperiumE2E/1.0)",
            },
            timeout=120,
            stream=True,
        )

        result = {"events": [], "tool_calls": [], "tool_results": [],
                   "final_text": "", "errors": [], "status_messages": [],
                   "session_id": session_id}

        import socket as _socket
        try:
            sock = getattr(getattr(getattr(r.raw, "_fp", None), "fp", None), "_sock", None)
            if sock is not None:
                sock.settimeout(15)
        except (AttributeError, OSError):
            pass

        try:
            for line_bytes in r.iter_lines():
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8", errors="replace")
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                result["events"].append(payload)
                ev_type = payload.get("type", "")
                if ev_type == "status":
                    result["status_messages"].append(payload.get("message", ""))
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
        except (requests.ConnectionError, TimeoutError, OSError) as e:
            if not result["events"]:
                result["errors"].append(str(e))

        return result

    # ── Prompts ─────────────────────────────────────────────────────

    PROMPT_PATTERN = "Найди запчасти по слову 'масло' или 'oil' в каталоге продукта"
    PROMPT_FILTER = (
        "Покажи товары из категории тормозная система, цена до 5000. "
        "Используй filter_catalog_product с параметрами."
    )
    PROMPT_BRANDS = "Найди все бренды в каталоге. Используй grep_brands."

    # ── Diagnostics ─────────────────────────────────────────────────

    @staticmethod
    def _report(result: dict):
        """Log structured diagnostic report."""
        print(f"\n{'='*70}")
        print(f"  📊 Session: {result.get('session_id', '?')}")
        print(f"  📊 Events: {len(result['events'])}")
        print(f"  📊 Tool calls: {len(result['tool_calls'])}")

        used_grep = any("grep_" in tc.get("name", "") for tc in result["tool_calls"])
        used_filter = any("filter_" in tc.get("name", "") for tc in result["tool_calls"])
        used_schema = any("schema_" in tc.get("name", "") for tc in result["tool_calls"])

        print(f"  📊 grep_* used: {used_grep}")
        print(f"  📊 filter_* used: {used_filter}")
        print(f"  📊 schema_* used: {used_schema}")

        if result["tool_calls"]:
            print(f"\n  ┌─ Tool calls ──────────────────────────────")
            for i, tc in enumerate(result["tool_calls"]):
                name = tc.get("name", "?")
                args = tc.get("arguments", {})
                args_str = json.dumps(args, ensure_ascii=False)
                has_args = "YES" if args else "EMPTY!"
                print(f"  │ {i+1}. {name}({args_str[:150]}) [{has_args}]")
            print(f"  └{'─'*45}")

        if result["tool_results"]:
            print(f"\n  ┌─ Tool results ────────────────────────────")
            error_count = 0
            for i, tr in enumerate(result["tool_results"][:10]):
                is_err = tr.get("isError", False)
                if is_err:
                    error_count += 1
                tag = "❌ isError" if is_err else "✅ OK"
                preview = (tr.get("result", "") or "")[:120]
                print(f"  │ {i+1}. {tag}  {preview}")
            if len(result["tool_results"]) > 10:
                print(f"  │ ... +{len(result['tool_results'])-10} more")
            if error_count:
                print(f"  │ ⚠️  {error_count}/{len(result['tool_results'])} results have isError=True")
            print(f"  └{'─'*45}")

        if result["errors"]:
            print(f"\n  ❌ Errors ({len(result['errors'])}):")
            for e in result["errors"][:5]:
                print(f"     {e[:200]}")

        if result["final_text"]:
            snippet = result["final_text"][:500]
            print(f"\n  ┌─ LLM Response ───────────────────────────")
            for line in snippet.split("\n"):
                print(f"  │ {line}")
            print(f"  └{'─'*45}")
        else:
            print(f"\n  ⚠️  No response text")

        return result

    def test_diagnostic_pattern(self):
        """DIAGNOSTIC: LLM с запросом текстового поиска."""
        print(f"\n  🎯 Prompt: {self.PROMPT_PATTERN}")
        result = self._chat(self.PROMPT_PATTERN)
        self._report(result)

    def test_diagnostic_filters(self):
        """DIAGNOSTIC: LLM с запросом field-фильтрации."""
        print(f"\n  🎯 Prompt: {self.PROMPT_FILTER[:80]}...")
        result = self._chat(self.PROMPT_FILTER)
        self._report(result)

    def test_diagnostic_brands(self):
        """DIAGNOSTIC: LLM с запросом брендов (поиск другой сущности)."""
        print(f"\n  🎯 Prompt: {self.PROMPT_BRANDS}")
        result = self._chat(self.PROMPT_BRANDS)
        self._report(result)
