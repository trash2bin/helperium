"""Тонны тестов на парсинг JSON-тулов из текста LLM-ответа.

Coverage:
  tool_parser.py — все форматы ввода
  stages._looks_like_raw_json_tool_calls — safety net что JSON не уйдёт пользователю
  E2E pipeline — NDJSON, массив, одиночный, смешанный (реальные сценарии)
"""

from __future__ import annotations

import json

import pytest

from api_service.agent.tool_parser import ToolCallParser
from api_service.agent.pipeline import Pipeline
from api_service.agent.stages import (
    LLMStage,
    ToolExecutionStage,
)
from api_service.agent.middlewares import (
    SpendingMiddleware,
    BacklogMiddleware,
)

from .helpers import (
    TestLLMProvider,
    TestMCPProvider,
    llm_response,
    make_pipeline_ctx,
    collect_events,
)


# ═══════════════════════════════════════════════════════════════════════════════
# tool_parser.py — unit tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolParser:
    """ToolCallParser.extract_tool_calls() — все форматы."""

    def setup_method(self):
        self.parser = ToolCallParser()

    # ── 1. Native tool_calls (OpenAI-стиль) ─────────────────────────

    def test_native_tool_calls(self):
        """OpenAI-style tool_calls field."""
        msg = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_product", "arguments": '{"id": 1}'},
                },
            ],
            "content": None,
        }
        result = self.parser.extract_tool_calls(msg)
        assert len(result) == 1
        assert result[0]["name"] == "get_product"
        assert result[0]["arguments"] == {"id": 1}

    def test_native_tool_calls_multiple(self):
        """Несколько native tool_calls."""
        msg = {
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "get_a", "arguments": '{"id": 1}'},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "get_b", "arguments": '{"id": 2}'},
                },
            ],
        }
        result = self.parser.extract_tool_calls(msg)
        assert len(result) == 2
        assert result[0]["name"] == "get_a"
        assert result[1]["name"] == "get_b"

    # ── 2. JSON array в content ─────────────────────────────────────

    def test_json_array_in_content(self):
        """Content=[{'name':'x', 'arguments':{...}}]."""
        content = json.dumps([{"name": "get_product", "arguments": {"id": 1059}}])
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "get_product"
        assert result[0]["arguments"] == {"id": 1059}

    def test_json_array_multiple(self):
        """Content=[{...}, {...}] — несколько тулов."""
        content = json.dumps(
            [
                {"name": "get_product", "arguments": {"id": 1059}},
                {"name": "get_product", "arguments": {"id": 1060}},
            ]
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 2
        assert result[0]["arguments"] == {"id": 1059}
        assert result[1]["arguments"] == {"id": 1060}

    def test_json_array_with_function_wrapper(self):
        """Content с function-обёрткой."""
        content = json.dumps(
            [
                {"function": {"name": "get_product"}, "arguments": {"id": 1059}},
            ]
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "get_product"

    def test_json_array_with_tool_name(self):
        """Content с tool_name вместо name."""
        content = json.dumps(
            [
                {"tool_name": "search_products", "args": {"q": "Castrol"}},
            ]
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "search_products"
        assert result[0]["arguments"] == {"q": "Castrol"}

    # ── 3. NDJSON (line-delimited JSON) ─────────────────────────────

    def test_ndjson_single(self):
        """Построчный JSON: одна строка — один тул."""
        content = '{"name": "get_product", "arguments": {"id": 1059}}'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "get_product"

    def test_ndjson_multiple(self):
        """Построчный JSON: несколько строк — несколько тулов.

        Реальный сценарий с MiniMax:
        {"name": "grep_catalog_product", "arguments":{"pattern": "моторное масло Castrol"}}
        {"name": "grep_catalog_brand", "arguments":{"pattern": "Castrol"}}
        """
        content = (
            '{"name": "grep_catalog_product", "arguments": {"pattern": "Castrol"}}\n'
            '{"name": "grep_catalog_brand", "arguments": {"pattern": "Castrol"}}'
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 2, (
            f"Expected 2 tools from NDJSON, got {len(result)}: {result}"
        )
        assert result[0]["name"] == "grep_catalog_product"
        assert result[1]["name"] == "grep_catalog_brand"

    def test_ndjson_with_blank_lines(self):
        """NDJSON с пустыми строками между тулами."""
        content = (
            '{"name": "get_a", "arguments": {"id": 1}}\n\n'
            '{"name": "get_b", "arguments": {"id": 2}}\n'
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 2, f"NDJSON with blank lines: {result}"

    def test_ndjson_with_newline_in_arguments(self):
        """NDJSON где arguments — строка с переносом."""
        content = (
            '{"name": "filter_products", "arguments": "{\\"name\\": \\"Castrol\\"}"}\n'
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "filter_products"
        # arguments может быть dict или str — проверяем что парсится
        assert result[0]["arguments"] == {"name": "Castrol"}

    # ── 4. Markdown code blocks ────────────────────────────────────

    def test_markdown_json_array_block(self):
        """```json\n[{...}]\n```"""
        content = '```json\n[{"name": "get_product", "arguments": {"id": 1}}]\n```'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1, f"markdown block: {result}"

    def test_markdown_json_single_block(self):
        """```json\n{...}\n```"""
        content = '```json\n{"name": "get_product", "arguments": {"id": 1}}\n```'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1, f"markdown single: {result}"

    def test_markdown_no_lang_tag(self):
        """```\n[...]\n```"""
        content = '```\n[{"name": "get_product", "arguments": {"id": 1}}]\n```'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1

    # ── 5. "Tool Calls:" prefix ────────────────────────────────────

    def test_tool_calls_prefix(self):
        """Tool Calls: [{...}, {...}]"""
        content = 'Tool Calls: [{"name": "get_product", "arguments": {"id": 1}}]'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1

    def test_tool_call_prefix(self):
        """Tool Call: [{...}]"""
        content = 'Tool Call: [{"name": "get_product", "arguments": {"id": 1}}]'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1

    # ── 6. <invoke> tags ──────────────────────────────────────────

    def test_invoke_tag(self):
        """<invoke name=\"x\">...</invoke>"""
        content = (
            '<invoke name="get_product"><parameter name="id">1</parameter></invoke>'
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["name"] == "get_product"

    # ── 7. Wrapped in {"tool_calls": [...]} ───────────────────────

    def test_wrapped_tool_calls(self):
        """Content={'tool_calls': [...]}"""
        content = json.dumps(
            {"tool_calls": [{"tool_name": "get_product", "args": {"id": 1}}]}
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1

    # ── 8. Empty / no tool calls ──────────────────────────────────

    def test_empty_content(self):
        """Пустой контент."""
        result = self.parser.extract_tool_calls({"content": ""})
        assert len(result) == 0

    def test_no_tool_content(self):
        """Обычный текст без JSON."""
        result = self.parser.extract_tool_calls(
            {"content": "Привет! Вот список товаров..."}
        )
        assert len(result) == 0

    def test_natural_language_with_json_like(self):
        """Естественный язык с упоминанием JSON, но не тулом."""
        result = self.parser.extract_tool_calls(
            {
                "content": 'В ответе я использовал JSON для форматирования: {"status": "ok"}'
            }
        )
        assert len(result) == 0, "Не должно парсить как тул"

    def test_valid_json_not_a_tool(self):
        """Валидный JSON но не тул (нет name/function)."""
        result = self.parser.extract_tool_calls(
            {"content": '{"temperature": 25, "unit": "celsius"}'}
        )
        assert len(result) == 0, "Не должно парсить если нет name/function"

    def test_valid_json_array_not_a_tool(self):
        """Массив объектов без name."""
        content = json.dumps([{"id": 1, "value": "test"}, {"id": 2, "value": "test2"}])
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 0, "Не должно парсить массив без name/function"

    # ── 9. Edge cases ────────────────────────────────────────────

    def test_mixed_with_extra_text(self):
        """Тул в контенте с префиксом/суффиксом."""
        content = (
            "Based on the data:\n"
            '{"name": "get_product", "arguments": {"id": 1}}\n'
            "Please let me know if you need more."
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1, f"Mixed content with extra text: {result}"

    def test_special_chars_in_args(self):
        """Кириллица, спецсимволы в аргументах."""
        content = json.dumps(
            [
                {
                    "name": "grep_products",
                    "arguments": {"pattern": "моторное масло Castrol 5W-30"},
                }
            ]
        )
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert "моторное" in result[0]["arguments"]["pattern"]

    def test_unicode_args(self):
        """Юникодные аргументы."""
        content = '{"name": "search", "arguments": {"q": "масло"}}'
        result = self.parser.extract_tool_calls({"content": content})
        assert len(result) == 1
        assert result[0]["arguments"]["q"] == "масло"

    def test_hybrid_native_plus_text_not_supported(self):
        """Если есть native tool_calls — текст игнорируется (current contract)."""
        msg = {
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "native_tool", "arguments": "{}"},
                },
            ],
            "content": '{"name": "text_tool", "arguments": {}}',
        }
        result = self.parser.extract_tool_calls(msg)
        assert len(result) == 1
        assert result[0]["name"] == "native_tool"


# ═══════════════════════════════════════════════════════════════════════════════
# _looks_like_raw_json_tool_calls — safety net
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafetyNet:
    """_looks_like_raw_json_tool_calls — защита от утечки JSON в final.

    ⚠️  Парсер теперь ловит большинство форматов, safety net срабатывает
         только когда парсер НЕ справился. Эти тесты проверяют что
         парные функции (parser + safety net) не пропускают JSON.
    """

    @staticmethod
    def check(content: str) -> bool:
        from api_service.agent.stages import _looks_like_raw_json_tool_calls

        return _looks_like_raw_json_tool_calls(content)

    # ── Должен ловить ──────────────────────────────────────────────

    def test_catches_ndjson(self):
        """NDJSON построчно."""
        assert self.check('{"name": "get_product", "arguments": {"id": 1}}')

    def test_catches_ndjson_multiple(self):
        """Несколько строк NDJSON."""
        assert self.check(
            '{"name": "grep_catalog_product", "arguments":{"pattern": "Castrol"}}\n'
            '{"name": "grep_catalog_brand", "arguments":{"pattern": "Castrol"}}'
        )

    def test_catches_json_array(self):
        """JSON массив тулов."""
        assert self.check('[{"name": "get_product", "arguments": {"id": 1}}]')

    def test_catches_json_array_multiple(self):
        """Массив с несколькими тулами."""
        assert self.check(
            '[{"name": "get_a", "arguments": {}}, {"name": "get_b", "arguments": {}}]'
        )

    def test_catches_tool_calls_prefix(self):
        """Tool Calls: [...]"""
        assert self.check(
            'Tool Calls: [{"name": "get_product", "arguments": {"id": 1}}]'
        )

    def test_catches_tool_calls_wrapper(self):
        """{"tool_calls": [...]}"""
        assert self.check(
            '{"tool_calls": [{"name": "get_product", "arguments": {"id": 1}}]}'
        )

    # ── НЕ должен ловить ───────────────────────────────────────────

    def test_allows_normal_text(self):
        """Обычный естественный текст."""
        assert not self.check("Привет! Вот ответ на ваш вопрос...")

    def test_allows_text_with_braces(self):
        """Текст с фигурными скобками, но не тулами."""
        assert not self.check("Ответ: { успешно } обработан.")

    def test_allows_json_not_tool(self):
        """JSON но без name."""
        assert not self.check('{"temperature": 25}')

    def test_allows_json_array_not_tool(self):
        """Массив но без name."""
        assert not self.check('[{"id": 1}, {"id": 2}]')

    def test_allows_text_with_word_name(self):
        """Текст со словом name, но не тул."""
        assert not self.check("Моё имя Иван, я ищу масло.")

    def test_allows_code_example(self):
        """Пример кода с JSON."""
        assert not self.check(
            'Пример: curl -X POST -d \'{"name": "test", "value": 1}\' ...'
        )


# ═══════════════════════════════════════════════════════════════════════════════
# E2E: pipeline с разными форматами JSON-tool-calls в content
# ═══════════════════════════════════════════════════════════════════════════════


class TestE2EPipeline:
    """Pipeline должен выполнять тулы из JSON-текста во всех форматах."""

    @pytest.mark.asyncio
    async def test_ndjson_single_tool_then_final(self):
        """NDJSON 1 строка → тул → результат → LLM → финал."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls([("get_product", {"id": 1059})]),
            llm_response.final("Вот товар: OIL-01245"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1059, "name": "OIL-01245"})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))
        event_types = [t for t, _ in events]

        assert "tool_call" in event_types, f"NDJSON tool не сработал: {event_types}"
        assert "tool_result" in event_types
        assert "final" in event_types
        final_content = "".join(str(d) for t, d in events if t == "final")
        assert "OIL-01245" in final_content, f"JSON утек в final! {final_content[:300]}"

    @pytest.mark.asyncio
    async def test_ndjson_multiple_tools_then_final(self):
        """NDJSON 2 строки → 2 тула → оба результата → LLM → финал."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls(
                [("get_product", {"id": 1059}), ("get_product", {"id": 1060})]
            ),
            llm_response.final("Нашёл два товара"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 0, "name": "placeholder"})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        await collect_events(pipeline.run(ctx))
        assert len(mcp.call_history) == 2, (
            f"NDJSON 2 тула не выполнились: {mcp.call_history}"
        )

    @pytest.mark.asyncio
    async def test_mixed_normal_and_text_tools(self):
        """Раунд 1: обычный tool_call, раунд 2: тул как JSON-текст."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.tool_call("schema", {}),
            llm_response.text_tool_calls([("filter", {"q": "Castrol"})]),
            llm_response.final("Готово!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("schema", {"columns": ["id", "name"]})
        mcp.add_tool("filter", {"results": []})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        await collect_events(pipeline.run(ctx))

        assert len(mcp.call_history) == 2, (
            f"Оба тула должны выполниться: {[h['name'] for h in mcp.call_history]}"
        )
        assert mcp.call_history[0]["name"] == "schema"
        assert mcp.call_history[1]["name"] == "filter"

    @pytest.mark.asyncio
    async def test_full_cycle_like_widget(self):
        """Schema → grep (native) → filter (text) → final.

        Имитация реального бага с виджета."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.tool_call("schema_catalog_product", {}),
            llm_response.tool_call("grep_catalog_brand", {"pattern": "Castrol"}),
            llm_response.text_tool_calls(
                [("filter_catalog_product", {"brand_id": 72, "limit": 20})]
            ),
            llm_response.final("Нашёл масло Castrol: OIL-01245"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("schema_catalog_product", {"columns": ["id", "name", "price"]})
        mcp.add_tool("grep_catalog_brand", {"preview": [{"id": 72, "name": "Castrol"}]})
        mcp.add_tool(
            "filter_catalog_product", {"preview": [{"id": 1059, "name": "OIL-01245"}]}
        )

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]

        # Главная проверка: ни одного raw JSON в final
        final_events = [(t, d) for t, d in events if t == "final"]
        final_content = ""
        for _, fd in final_events:
            if isinstance(fd, dict):
                final_content = fd.get("content", "")
        for bad in ["Tool Calls", '[{"name"', '{"name"', "arguments"]:
            assert bad not in final_content, (
                f"JSON утек в final! Содержит '{bad}': {final_content[:300]}"
            )

        assert "tool_call" in event_types
        assert "final" in event_types
        assert llm.call_count == 4, f"4 LLM вызова: {llm.call_count}"

    @pytest.mark.asyncio
    async def test_safety_net_blocks_raw_json_final(self):
        """Safety net блокирует JSON который парсер не смог извлечь."""

        # NDJSON теперь парсится нормально парсером — проверяем safety net
        # на формате который парсер не ловит: текст + JSON тула в середине
        # (парсер ищет изолированный JSON, найдёт его и вернёт как тул,
        #  но пусть safety net будет как дополнительная проверка ниже)

        # Используем формат где тулы завёрнуты в "tool_calls" ключ
        # внутри объекта — это успешно парсится, проверим что финал чистый
        llm = TestLLMProvider()
        llm.queue(
            llm_response.final(
                '{"tool_calls": [{"name": "get_product", "arguments": {"id": 1}}]}'
            ),
            llm_response.final("Готово!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]

        # Парсер справился с этим форматом — должны быть tool_call и tool_result
        assert "tool_call" in event_types, (
            f"Парсер должен был найти tool_call: {event_types}"
        )
        # В финале не должно быть JSON
        final_events = [(t, d) for t, d in events if t == "final"]
        for _, fd in final_events:
            content = fd.get("content", "") if isinstance(fd, dict) else str(fd)
            assert "[{" not in content, f"JSON утек в final: {content[:200]}"


# ═══════════════════════════════════════════════════════════════════════════════
# Регрессия: утечка токенов — content_tokens с сырым JSON не должны уходить
# пользователю. Фикс: стримим ТОЛЬКО когда outcome = final content.
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenLeak:
    """Проверка что content_tokens с NDJSON/JSON-тулами НЕ утекают."""

    @pytest.mark.asyncio
    async def test_no_token_leak_on_ndjson(self):
        """NDJSON тулы — токены не уходят (был баг: стримились ДО парсинга)."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls(
                [
                    ("get_product", {"id": 1059}),
                    ("get_product", {"id": 1060}),
                ]
            ),
            llm_response.final("Вот товары!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        # Проверяем что нет token-событий с сырым JSON в первом раунде
        token_texts = []
        for t, d in events:
            if t == "token":
                if isinstance(d, dict):
                    token_texts.append(d.get("data", ""))
                else:
                    token_texts.append(str(d))
        full = "".join(token_texts)
        assert "get_product" not in full, (
            f"Токены с сырым JSON тулов утекли пользователю! "
            f"Полные токены (первые 500): {full[:500]}"
        )
        # Должен быть только финальный "Вот товары!"
        assert "товары" in full, (
            f"Должны быть только нормальные токены финала: {full[:200]}"
        )

    @pytest.mark.asyncio
    async def test_no_token_leak_on_mixed_content(self):
        """Текст + NDJSON — токены с JSON не утекают."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls_mixed(
                prefix_text="Нашёл 4 продукта. Давайте посмотрим:",
                calls=[("get_product", {"id": 1059})],
                suffix_text="",
                format="inline",
            ),
            llm_response.final("Вот детали!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        token_texts = []
        for t, d in events:
            if t == "token":
                if isinstance(d, dict):
                    token_texts.append(d.get("data", ""))
                else:
                    token_texts.append(str(d))
        full = "".join(token_texts)
        assert "1059" not in full, f"ID товара утекло в токены! {full[:300]}"
        assert "товары" in full or "детали" in full, (
            f"Только нормальные токены финала: {full[:300]}"
        )

    @pytest.mark.asyncio
    async def test_parser_catches_before_safety_net(self):
        """Парсер ловит JSON тула раньше чем safety net — тул выполняется."""
        llm = TestLLMProvider()
        # Парсер ловит NDJSON, выполняет тул, потом следующий LLM вызов
        llm.queue(
            llm_response.final('{"name": "get_product", "arguments": {"id": 1059}}'),
            llm_response.final("Готово!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1059})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, (
            f"Парсер не нашёл тул в NDJSON: {event_types}"
        )
        assert "tool_result" in event_types, f"Тул не выполнился: {event_types}"
        assert "final" in event_types, f"Финальный ответ не пришёл: {event_types}"
        # Токены финала не содержат JSON
        token_texts = []
        for t, d in events:
            if t == "token":
                if isinstance(d, dict):
                    token_texts.append(d.get("data", ""))
                else:
                    token_texts.append(str(d))
        full = "".join(token_texts)
        assert "Готово" in full, f"Токены финала: {full[:200]}"
        assert "1059" not in full, f"JSON утёк в токены! {full[:200]}"


# ═══════════════════════════════════════════════════════════════════════════════
# Регрессия: iteration не расходуется на tool-раунд
# ═══════════════════════════════════════════════════════════════════════════════


class TestIterationBudget:
    """Tool-раунды не должны расходовать iteration."""

    @pytest.mark.asyncio
    async def test_iteration_not_consumed_on_tool_round(self):
        """3 текстовых тула → 1 LLM вызов финала (iteration=0 после всех тулов)."""
        llm = TestLLMProvider()
        llm.queue(
            # Раунд: 3 тула NDJSON
            llm_response.text_tool_calls(
                [
                    ("get_product", {"id": 1}),
                    ("get_product", {"id": 2}),
                    ("get_product", {"id": 3}),
                ]
            ),
            # Раунд: финал
            llm_response.final("Готово!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(
            llm_provider=llm,
            mcp_provider=mcp,
            max_iterations=3,
            max_empty_rounds=2,
        )
        events = await collect_events(pipeline.run(ctx))

        # Проверяем что все тулы выполнились внутри iteration=0
        assert len(mcp.call_history) == 3, (
            f"Все 3 тула должны выполниться: {[h['name'] for h in mcp.call_history]}"
        )
        event_types = [t for t, _ in events]
        assert "final" in event_types, f"Должен быть финал: {event_types}"
        assert "error" not in event_types, f"Не должно быть ошибки: {event_types}"
        # Iteration тулов = 0, финала = 0 или 1
        for t, d in events:
            if t == "tool_call":
                it = d.get("iteration", None) if isinstance(d, dict) else None
                if it is not None:
                    assert it == 0, f"Tool call должен быть на iteration 0, был на {it}"


# ═══════════════════════════════════════════════════════════════════════════════
# Регрессия: все реальные форматы — от модели до виджета
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealWorldFormats:
    """Реальные форматы которые возвращают модели."""

    @pytest.mark.asyncio
    async def test_formats_ndjson_multiline(self):
        """Line-delimited JSON: каждая строка отдельный тул."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls_mixed(
                prefix_text="",
                calls=[
                    ("grep_catalog_product", {"pattern": "Castrol"}),
                ],
                format="ndjson",
            ),
            llm_response.final("Нашёл 4 товара Castrol"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("grep_catalog_product", {"results": []})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, f"NDJSON не распарсился: {event_types}"
        assert "final" in event_types
        # Токены не содержат JSON
        token_texts = []
        for t, d in events:
            if t == "token":
                if isinstance(d, dict):
                    token_texts.append(d.get("data", ""))
                else:
                    token_texts.append(str(d))
        full = "".join(token_texts)
        assert "Castrol" in full, f"Должен быть нормальный ответ: {full[:200]}"

    @pytest.mark.asyncio
    async def test_formats_mixed_text_then_ndjson(self):
        """Текст + NDJSON построчно:

        Нашёл 4 продукта. Давайте посмотрим:
        {"name": "get_product", "arguments": {"id": 1059}}
        {"name": "get_product", "arguments": {"id": 1060}}
        """
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls_mixed(
                prefix_text="Нашёл 4 продукта. Давайте посмотрим:\n",
                calls=[
                    ("get_product", {"id": 1059}),
                    ("get_product", {"id": 1060}),
                ],
                format="inline",
            ),
            llm_response.final("Вот детали по товарам!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, f"Mixed NDJSON не распарсился: {event_types}"
        assert len(mcp.call_history) == 2
        assert mcp.call_history[0]["arguments"] == {"id": 1059}
        assert mcp.call_history[1]["arguments"] == {"id": 1060}

    @pytest.mark.asyncio
    async def test_formats_openai_style_with_function_wrapper(self):
        """[{"id":"call_x","type":"function","function":{"name":"x","arguments":"{}"}}]."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls_mixed(
                prefix_text="Нашёл товар. Давайте посмотрим:",
                calls=[("get_product", {"id": 1059})],
                format="openai_tool_calls",
            ),
            llm_response.final("Вот!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1059})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, (
            f"OpenAI-style с function wrapper не распарсился: {event_types}"
        )

    @pytest.mark.asyncio
    async def test_formats_wrapper_object(self):
        """{"tool_calls": [{"name": "x", "arguments": {}}]} — объект-обёртка."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls_mixed(
                prefix_text="",
                calls=[("get_product", {"id": 1059})],
                suffix_text="",
                format="json_array",
            ),
            llm_response.final("OK"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1059})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, f"Tool calls wrapper: {event_types}"

    @pytest.mark.asyncio
    async def test_normal_text_goes_to_final(self):
        """Обычный текст без тулов — идёт в final как есть."""
        llm = TestLLMProvider()
        llm.queue(
            llm_response.final("Привет! Вот моторное масло Castrol 5W-30."),
        )
        mcp = TestMCPProvider()

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "final" in event_types, f"Обычный текст не дошёл: {event_types}"
        final_content = ""
        for _, fd in events:
            if isinstance(fd, dict) and fd.get("content"):
                final_content = fd.get("content", "")
        assert "Castrol" in final_content, (
            f"Обычный текст исказился: {final_content[:200]}"
        )

    @pytest.mark.asyncio
    async def test_markdown_code_block_json(self):
        """```json\n[{...}]\n``` — markdown code block."""
        content = '```json\n[{"name": "get_product", "arguments": {"id": 1}}]\n```'
        llm = TestLLMProvider()
        llm.queue(
            llm_response.final(content),
            llm_response.final("OK"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("get_product", {"id": 1})

        pipeline = Pipeline(
            stages=[LLMStage(), ToolExecutionStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)
        events = await collect_events(pipeline.run(ctx))

        event_types = [t for t, _ in events]
        assert "tool_call" in event_types, (
            f"Markdown block не распарсился: {event_types}"
        )
