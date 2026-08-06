# Tool Call Safety Layers — защита от утечки сырого JSON пользователю

## Контекст

Некоторые LLM (MiniMax, локальные Ollama-модели, Llama) **не умеют возвращать структурированные tool_calls**. Они пишут JSON-тулы как обычный текст в `content`. Без защиты этот JSON улетает пользователю:

```
{"name": "get_catalog_product", "arguments":{"id": 1059}}
{"name": "get_catalog_product", "arguments":{"id": 1060}}
```

Это **security issue**: модель показывает внутреннее состояние, пользователь видит сырые данные. Три уровня защиты предотвращают это.

## Архитектура (3 слоя)

```mermaid
flowchart LR
    subgraph MODEL[LLM Response]
        A[response.tool_calls<br/>структурированные]
        B[content с JSON-текстом<br/>тулов]
    end

    subgraph L1[LAYER 1 — LiteLLM]
        C{msg.tool_calls<br/>не пуст?}
        C -->|да| D[✅ Исполняем тулы<br/>content_tokens НЕ стримятся]
    end

    subgraph L2[LAYER 2 — ToolCallParser]
        C -->|нет| E{extract_tool_calls<br/>нашёл?}
        E -->|да| F[✅ Исполняем тулы<br/>content_tokens НЕ стримятся]
    end

    subgraph L3[LAYER 3 — Safety Net]
        E -->|нет| G{_looks_like_raw_json<br/>tool_calls?}
        G -->|да| H[❌ Error: JSON blocked<br/>content_tokens НЕ стримятся]
        G -->|нет| I[✅ Final answer<br/>content_tokens СТРИМЯТСЯ]
    end

    MODEL --> L1
    L1 --> L2
    L2 --> L3
```

### Layer 1 — LiteLLM `add_function_to_prompt`

**Где:** `services/api-service/src/api_service/agent/litellm_provider.py`

**Что делает:** глобальный флаг `litellm.add_function_to_prompt = True` включает конвертацию `tools/{'function':{...}}` в текстовый промпт для моделей без нативной поддержки. LiteLLM сама инжектит описание тулов в system prompt и **парсит ответ обратно** в `msg.tool_calls`.

**Когда срабатывает:** для любых моделей где `litellm.supports_function_calling(model)` → `False`.

**Лог:** `[LITELLM_PROVIDER] Model returned N tool_calls via native/add_function_to_prompt path`

### Layer 2 — ToolCallParser (fallback)

**Где:** `services/api-service/src/api_service/agent/tool_parser.py`, вызов в `stages.py`

**Что делает:** если LiteLLM не распарсила тулы (вернула JSON текстом в `content`), `ToolCallParser.extract_tool_calls()` парсит его вручную. Поддержка форматов:

| Формат | Пример |
|---|---|
| JSON-массив | `[{"name": "x", "arguments": {"id": 1}}]` |
| NDJSON (построчно) | `{"name": "x"}\n{"name": "y"}` |
| OpenAI-стиль | `[{"function": {"name": "x", "arguments": "{}"}}]` |
| Markdown code block | `` ```json [{...}] ``` `` |
| Tool Calls: префикс | `Tool Calls: [{...}]` |
| <invoke> теги | `<invoke name="x">...</invoke>` |
| Обёртка | `{"tool_calls": [{...}]}` |

**Когда срабатывает:** когда `response.tool_calls` пуст, но `response.content` содержит JSON тула.

**Лог:** `[LLM_STAGE][TOOL_PARSER] Extracted N tool calls from JSON text (LiteLLM didn't parse them, fallback parser caught them)`

### Layer 3 — Safety Net (`_looks_like_raw_json_tool_calls`)

**Где:** `services/api-service/src/api_service/agent/stages.py`, функция `_looks_like_raw_json_tool_calls()`

**Что делает:** эвристическая проверка контента на наличие `{"name": ... "arguments": ...}` или `[...{"name":...}]`. Если контент похож на JSON тула — **не пускаем `final`**, вместо этого шлём `error`. Это последняя линия.

**Когда срабатывает:** когда LAYER 1 и LAYER 2 не справились, но контент содержит:
- `{"name": "...", "arguments": {...}}` (NDJSON)
- `[{...несколько тулов...}]` (массив)
- `Tool Calls: [...]`
- `{"tool_calls": [...]}`

**Лог:** `[LLM_STAGE][SAFETY_NET] BLOCKED final: content looks like raw JSON tool calls (LiteLLM+ToolParser both failed).`

**Что блокирует НЕ ЛОВИТ** (не должно): обычный текст, `{"status": "ok"}`, `curl -d '{"name":"test"}'`.

### Token streaming guard

**Где:** `stages.py`, весь outcome-блок в `LLMStage.run()`

Критическое правило: `content_tokens` **стримятся ТОЛЬКО в ветке `final`** — после того как все три слоя подтвердили что это настоящий ответ, не JSON тула.

```python
# НЕПРАВИЛЬНО (было — стримилось ДО проверки):
for token in response.content_tokens:
    yield AgentEvent("token", {"data": token})

# ПРАВИЛЬНО (стало — только после safe_final):
if response.tool_calls:       # LAYER 1 → не стримим
elif response.content:
    if parsed:                # LAYER 2 → не стримим
    if _looks_like...:        # LAYER 3 → не стримим, error
    # ТОЛЬКО ТУТ:
    for token in ...:         # true final → стримим
```

### Iteration не расходуется на tool-round

**Где:** `services/api-service/src/api_service/agent/pipeline.py`

```python
if not ctx.had_tool_calls_this_iteration:
    ctx.turn.iteration += 1
ctx.had_tool_calls_this_iteration = False
```

Если в раунде были tool_calls (любой из 3 слоёв), iteration не инкрементируется. Это предотвращает ситуацию когда последние тулы срезаются лимитом и JSON остаётся непонятым.

## Добавление нового слоя/формата

1. **Новый формат в LAYER 2** — расширить `ToolCallParser._extract_json_tool_calls()`:
   - Добавить regex/парсинг для нового формата
   - Добавить тест в `test_tool_parser_extensive.py`
   - Добавить фабрику в `llm_response` если формата нет

2. **Новый эвристик в LAYER 3** — расширить `_looks_like_raw_json_tool_calls()`:
   - Добавить проверку на новый паттерн
   - Добавить unit-тест в `TestSafetyNet`

3. **Напрямую через LiteLLM** — если модель научили писать тулы в другом формате, проверить
   `litellm.supports_function_calling(model)` — может модель уже поддерживает нативно.

## Известные проблемы (wontfix / low prio)

- Модель может написать текст + JSON тула **в одном ответе**. Парсер найдёт тулы и выполнит их, текст будет добавлен в history но не показан пользователю. Wontfix — это ближе к правильному поведению чем JSON в чате.
- Safety net может **ложно сработать** на `curl -d '{"name":"test"}'`. На практике не было — обёрни в тест если появится.

## Тесты

Тесты в `test_tool_parser_extensive.py` (48 тестов) + `test_orchestrator_e2e.py`
(`TestLLMAgentWithProtocolProvider`, 2 теста) покрывают:

| Категория | Класс | Тестов | Что проверяет |
|-----------|-------|--------|---------------|
| Unit: ToolCallParser | `TestToolParser` | ~23 | NDJSON, JSON array, dict, OpenAI wrapper, _from_native_tool_calls, edge cases |
| Unit: Safety Net | `TestSafetyNet` | 12 | catch/allows — false positive prevention |
| Pipeline E2E | `TestE2EPipeline` | 6 | NDJSON/array/OpenAI → pipeline → tool executes → final |
| Token leak | `TestTokenLeak` | 3 | content_tokens не утекают с сырым JSON |
| Iteration budget | `TestIterationBudget` | 1 | tool-раунды не расходуют iteration |
| Real-world formats | `TestRealWorldFormats` | 4 | NDJSON, mixed, OpenAI, wrapper object |

**Ключевые регрессии:**
- Double-encoding fix: `test_layer1_arguments_not_double_encoded`
- Safety net pipeline: `test_safety_net_blocks_unparseable_json`
- Full orchestrator LAYER 1/LAYER 2: `TestLLMAgentWithProtocolProvider` (`test_orchestrator_e2e.py`)
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)
