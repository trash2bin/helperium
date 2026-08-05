# LLM E2E тесты (tests/e2e-llm/) — opt-in, требуют реальный LLM

Тесты, которые гоняют **реальный LLM** (Polza/DeepSeek/OpenAI) через
api-service. **Не входят в CI** — требуют API ключ и денег.

## Запуск

```bash
# Требуют LLM API ключ из .env (OPENAI_API_KEY или LLM_API_KEY)
uv run pytest tests/e2e-llm/ -v

# С конкретным ключом
OPENAI_API_KEY=... uv run pytest tests/e2e-llm/ -v
```

Без ключа — все тесты **скипаются** (skipif), не падают.

## Структура

| Файл | Что проверяет |
|---|---|
| `test_implicit_intent.py` | LLM сам догадывается вызвать `db_search`/`filter_*` по неявному запросу («нужен глушитель на BMW X5») |
| `test_llm_chat.py` | SSE chat через HTTP, agent endpoint, tool call + response |
| `test_search_e2e.py` | discovery → search → filter → multiturn диалог |
| `test_search_strategy.py` | grep/filter/schema через MCP (diagnostic) |

## Правила

1. **Не добавляй сюда тесты**, которые можно проверить без LLM — им место в `tests/e2e/`.
2. Тесты **скипаются** без ключа — не падают в CI.
3. Для детерминированных проверок pipeline используй `test_scripted_llm.py` в `tests/e2e/`
   (ScriptedLLMProvider — мок, не тратит деньги).
