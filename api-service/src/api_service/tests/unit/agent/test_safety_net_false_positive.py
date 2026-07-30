"""TDD test: Safety Net (_looks_like_raw_json_tool_calls) ложно срабатывает.

Проблема: функция _looks_like_raw_json_tool_calls() проверяет наличие
'name' и 'arguments' в JSON через substring поиск и json.loads.
Это даёт FALSE POSITIVE на легитимные финальные ответы агента, которые
возвращают JSON с полями name и arguments (например, детали заказа,
данные студента, информация о продукте).

Функция должна:
  - Возвращать True для реальных нераспарсенных tool calls
  - Возвращать False для обычных JSON-ответов

Тест ПАДАЕТ пока safety net ложно срабатывает на легитимные ответы.
"""

from __future__ import annotations

import json


from api_service.agent.stages.llm import _looks_like_raw_json_tool_calls


class TestSafetyNetNoFalsePositive:
    """_looks_like_raw_json_tool_calls НЕ должен блокировать легитимные JSON-ответы."""

    def test_order_status_json(self):
        """JSON с деталями заказа: name + arguments — НЕ tool calls.

        Типичный ответ агента: пользователь спросил статус заказа, агент
        вернул JSON с name=клиента и arguments=сумма. Это НЕ tool call.
        """
        content = json.dumps(
            {"order_status": "completed", "name": "Иван", "total": 1500},
            ensure_ascii=False,
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            f"\n\n❌ TDD FAIL: Safety Net ложно сработал на легитимный JSON.\n"
            f"Answer: {content}\n"
            f"Ожидалось: False (это НЕ tool call).\n"
            f"Получено: True (заблокировано как tool call).\n"
            f"Причина: 'name' + 'arguments' в тексте → проверка №1."
        )

    def test_customer_data_json(self):
        """JSON с данными клиента: fields name и arguments — НЕ tool calls.

        Агент искал клиента и вернул JSON-результат:
        {"ok": true, "data": {"name": "Петр", "arguments": "..."}}
        """
        content = json.dumps(
            {"ok": True, "data": {"name": "Петр", "arguments": "договор №123"}},
            ensure_ascii=False,
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            f"\n\n❌ TDD FAIL: Safety Net ложно сработал на customer data.\n"
            f"Answer: {content}\n"
            f"Это данные клиента, а не tool call."
        )

    def test_search_result_with_name(self):
        """Результат поиска: JSON с полем name — НЕ tool call.

        Tool вернул: {"ok": true, "data": {"name": "Product", "arguments": "..."}}
        LLM должна показать это пользователю, а safety net блокирует.
        """
        content = json.dumps(
            {"ok": True, "total": 1, "data": [{"name": "Product A", "price": 100}]},
            ensure_ascii=False,
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            f"\n\n❌ TDD FAIL: Safety Net ложно сработал на search result.\n"
            f"Answer: {content}\n"
            f"Это данные продукта, а не tool call."
        )

    def test_curly_data_with_name_arguments_valid(self):
        """ЛЕГИТИМНЫЙ JSON: data с name и arguments — НЕ tool call.

        Типичный ответ: агент вызывает tool, tool возвращает данные,
        агент оборачивает их в JSON для пользователя.
        """
        content = json.dumps(
            {
                "name": "Студент: Иванов Иван",
                "arguments": "группа ИВТ-21, средний балл 4.5",
            },
            ensure_ascii=False,
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            f"\n\n❌ TDD FAIL: Safety Net ложно сработал на данные студента.\n"
            f"Answer: {content}\n"
            f"Это легитимный ответ с данными, не tool call."
        )


class TestSafetyNetCorrectPositive:
    """_looks_like_raw_json_tool_calls ДОЛЖЕН блокировать реальные tool calls."""

    def test_real_tool_call_format_ndjson(self):
        """NDJSON строка tool call: {"name": "get_product", "arguments": {"id": 1}}.

        Это НАСТОЯЩИЙ tool call, который safety net должен поймать.
        """
        content = '{"name": "get_product", "arguments": {"id": 1}}'
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert result, (
            f"\n\n❌ TDD REVERSE FAIL: Safety Net НЕ поймал реальный tool call.\n"
            f"Content: {content}\n"
            f"Должен быть True (это tool call).\n"
            f"Проверьте что json.loads корректно парсит аргументы."
        )

    def test_real_tool_call_with_function(self):
        """OpenAI-style tool call: {"function": {"name": "x", "arguments": "{}"}}."""
        content = json.dumps(
            {"function": {"name": "search_products", "arguments": '{"query": "test"}'}}
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert result, "Safety Net должен поймать OpenAI-style tool call"


class TestSafetyNetEdgeCases:
    """Пограничные случаи — не должны ложно срабатывать."""

    def test_plain_text_with_json_words(self):
        """Обычный текст со словами name и arguments — НЕ tool call."""
        content = 'Пользователь name="test" передал arguments в вызове функции.'
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            "Safety Net не должен блокировать plain text с JSON-подобными словами"
        )

    def test_json_array_with_non_tool_names(self):
        """JSON-массив с name но не tool call — не должен блокировать.

        {"items": [{"name": "A", "price": 1}, {"name": "B", "price": 2}]}
        """
        content = json.dumps(
            {"items": [{"name": "A", "price": 1}, {"name": "B", "price": 2}]}
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            "Safety Net не должен блокировать JSON-массив с name-полями товаров"
        )

    def test_json_api_response_with_name_and_args(self):
        """JSON-ответ API с "name" и "args" ключами — не tool call.

        {"status": "ok", "endpoint": {"name": "users", "args_count": 3}}
        """
        content = json.dumps(
            {"status": "ok", "endpoint": {"name": "users", "args_count": 3}}
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, "Safety Net не должен блокировать JSON ответ API"

    def test_domain_specific_name_and_args(self):
        """Доменные данные с name и args — не tool call.

        {"document": {"name": "contract.pdf", "args": ["signed", "notarized"]}}
        """
        content = json.dumps(
            {"document": {"name": "contract.pdf", "args": ["signed", "notarized"]}}
        )
        result = _looks_like_raw_json_tool_calls(content)
        print(f"\nInput: {content}")
        print(f"_looks_like_raw_json_tool_calls returned: {result}")
        assert not result, (
            "Safety Net не должен блокировать доменные данные с 'name' и 'args'"
        )
