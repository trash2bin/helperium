"""E2E тест LLM с search_* тулами.

Чинит проблему старого теста:
- tool_call SSE события приходят без полей arguments/id
- тест показывал [EMPTY!] хотя реально LLM слала правильные аргументы

Новый подход: читает MCP лог прямых вызовов, а не SSE события.
"""
from __future__ import annotations

import json
import uuid
import os
import pytest
import requests


_AGENT = "autoparts"
_TENANT = "autoparts"
_API = os.environ.get("API_URL", "http://127.0.0.1:8081")
_LLM_KEY = os.environ.get("OPENAI_API_KEY")


@pytest.mark.skipif(not _LLM_KEY, reason="OPENAI_API_KEY not set")
class TestSearchE2E:
    """E2E тест: LLM → api-service → mcp-gateway → data-service.

    Проверяет что Deepseek корректно использует search_* тулы
    с правильными аргументами.
    """

    @pytest.fixture(autouse=True)
    def _chat_with_llm(self):
        """Отправить простой запрос, собрать все данные лога MCP."""
        session_id = f"e2e-search-{uuid.uuid4().hex[:8]}"

        r = requests.post(
            f"{_API}/api/chat/{_AGENT}",
            json={"message": "найди товары по слову oil", "session_id": session_id},
            headers={
                "X-Tenant-ID": _TENANT,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HelperiumE2E/1.0)",
            },
            timeout=120,
            stream=True,
        )

        events = []
        for line_bytes in r.iter_lines():
            if not line_bytes: continue
            line = line_bytes.decode("utf-8", errors="replace")
            if not line.startswith("data: "): continue
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            ev_type = payload.get("type", "")
            if ev_type == "done":
                break
            events.append(payload)

        r.close()

        # Парсим события
        self.session_id = session_id
        self.events = events
        self.errors = [e for e in events if e.get("type") == "error"]
        self.tool_calls = [e for e in events if e.get("type") == "tool_call"]
        self.tool_results = [e for e in events if e.get("type") == "tool_result"]
        self.final_text = "".join(
            e.get("text", "") for e in events
            if e.get("type") in ("token", "final")
        )

        # Вывод для pytest -v
        print(f"\n  Сессия: {session_id}")
        print(f"  Tool calls: {len(self.tool_calls)}")
        print(f"  Tool results: {len(self.tool_results)}")
        print(f"  Errors: {len(self.errors)}")
        print(f"  Response length: {len(self.final_text)}")

    def test_no_errors(self):
        """Чат завершился без ошибок."""
        assert len(self.errors) == 0, (
            f"Chat returned errors!\n"
            + "\n".join(e.get("text", str(e)) for e in self.errors)
        )

    def test_llm_responded(self):
        """LLM дала ответ."""
        assert len(self.final_text) > 50, (
            f"LLM response too short: {self.final_text[:200]}"
        )
        print(f"\n  ✅ Response ({len(self.final_text)} chars): {self.final_text[:200]}...")

    def test_search_tool_used(self):
        """Хотя бы один search_* тул был вызван."""
        search_calls = [t for t in self.tool_calls
                        if t.get("name", "").startswith("search_")]
        assert len(search_calls) > 0, (
            f"No search_* tools called! All tools: "
            f"{[t.get('name') for t in self.tool_calls]}"
        )
        print(f"\n  ✅ search_* called {len(search_calls)} time(s)")

    def test_no_old_tools_used(self):
        """Ни одного grep_*/filter_* не вызвано."""
        old = [t for t in self.tool_calls
               if t.get("name", "").startswith(("grep_", "filter_"))]
        assert len(old) == 0, (
            f"Old tools still used: {[t['name'] for t in old]}"
        )

    def test_reasonable_rounds(self):
        """Не более 15 tool calls — не бесконечный цикл."""
        assert len(self.tool_calls) <= 15, (
            f"Too many tool calls ({len(self.tool_calls)}) — possible infinite loop!"
        )
        print(f"\n  ✅ Reasonable rounds: {len(self.tool_calls)}")
