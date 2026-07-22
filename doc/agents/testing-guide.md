# Регрессионное тестирование

> **Правило:** Сначала unit, потом интеграционные, потом e2e без LLM,
> потом e2e с LLM. LLM-тесты — только в конце, они дорогие по токенам.

## 1. Python Unit/Integration

```bash
uv run pytest rag/tests/                            # RAG — 108 тестов
uv run pytest api-service/src/api_service/tests/    # API — ~260 тестов
uv run pytest demo/web/tests/                       # Web — 73 теста
uv run pytest demo/tests/                           # Settings — 18 тестов
uv run pytest helperium-sdk/tests/                  # SDK — 83 теста
```

### 1b. Agent Pipeline Unit Tests (без LLM/MCP/data-service)

Вся новая функциональность агента тестируется через моки Protocol'ов.

```bash
cd api-service
uv run pytest src/api_service/tests/unit/agent/test_stages.py -v         # 22 Stage-теста
uv run pytest src/api_service/tests/unit/agent/test_middlewares.py -v     # 8 Middleware-тестов
uv run pytest src/api_service/tests/unit/agent/test_error_flow.py -v     # 18 Error-flow тестов
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_e2e.py -v  # 3 интеграционных
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_fixes.py -v  # 7 регрессионных
```

Все 151 тест зелёные, работают без запущенных сервисов.

## 2. Go Unit/Integration

```bash
go test ./data-service/... ./helperium-go/...   # 690 тестов, 18 пакетов
go test ./mcp-gateway/...                      # ~80 тестов
```

### 2b. Embed Widget

```bash
cd api-service/embed && npm test           # 59 тестов (vitest)
cd api-service/embed && bash build.sh      # typecheck + esbuild
```

> ⚠️ После пересборки виджета: `./scripts/dev.sh restart api`

## 3. E2E (без LLM, pytest)

```bash
uv run pytest tests/e2e/test_search_strategies.py -v -k "TestAutoShopStrategies or TestClinicStrategies"
```

### 3a. Search Strategies E2E — `tests/e2e/test_search_strategies.py`

Проверяет grep/filter/schema стратегии с авто-генерированным конфигом.
Использует сценарии `auto-shop` и `clinic` (`data-service/testdata/scenarios/`).

**31 тест — 3 класса:**

| Класс | Тестов | Описание |
|---|---|---|
| `TestAutoShopStrategies` | 13 | grep/filter/schema/count на авто-магазине |
| `TestClinicStrategies` | 13 | grep/filter/schema/count на клинике |
| `TestLLMImplicitIntent` | 5 | LLM чат с неявным интентом (требует `OPENAI_API_KEY`) |

Проверяет: `/entity/grep`, `/entity/filter`, `/entity/schema`, `/entity/count`,
`/entity/distinct` эндпоинты, MCP manifest (наличие grep/filter/schema,
отсутствие search). **Никаких search_* тулов.** `find_*` и `list_*` — легитимны
как backward compat для не-strategy entity.

**Зависимости:** все сервисы запущены, `ADMIN_TOKEN` задан.

### 3b. MCP Validation — `tests/e2e/test_mcp_validation.py`

Проверяет что MCP-гейтвей и data-service отклоняют пустые/невалидные вызовы.

**9 тестов — 4 класса:**

| Класс | Тестов | Описание |
|---|---|---|
| `TestGetWithRequired` | 3 | `get_*({})` → isError; с id → OK; несколько тулов |
| `TestGrepWithRequired` | 4 | `grep_*({})` → isError; с pattern → OK; несколько тулов; длинный regex → isError |
| `TestAllToolsHaveRequiredGuard` | 1 | Каждый tool (кроме count_*) имеет `required` параметр |
| `TestLimitHasMaxBound` | 2 | limit в схеме; limit=9999999 → isError |

### 3c. Scripted LLM — `tests/e2e/test_scripted_llm.py`

Pipeline с `ScriptedLLMProvider` — без живой модели. Проверяет
tool_call SSE события (имена не пустые), пустые вызовы блокируются,
финальный ответ доходит.

## 4. E2E с LLM (дорогие, только в конце)

```bash
uv run pytest tests/e2e/llm/test_search_e2e.py -v -s
```

### 4a. LLM E2E — `tests/e2e/llm/test_search_e2e.py`

**⚠️ Дорогой тест.** Каждый вызов LLM тратит ~12К prompt tokens + ~100 completion tokens.
4 теста ≈ 50К токенов за прогон. Запускать только перед коммитом/PR, не в CI на каждый push.

**Что делает:**
1. Создаёт SQLite БД из seed-сценария (`auto-shop`)
2. Регистрирует tenant на data-service + rewrite (introspect → generate config)
3. Создаёт/пересоздаёт агента с этим tenant'ом
4. Проверяет что MCP manifest содержит grep/filter/schema — без search
5. Отправляет 4 вопроса с **рандомными session_id**:

| Тест | Вопрос | Ожидание |
|---|---|---|
| `test_discovery_first_then_search` | "Какие есть запчасти для BMW?" | `schema_*` first → `grep_*`/`filter_*` |
| `test_search_by_text` | "Найди глушители" | `grep_auto_parts(pattern="глушит")` |
| `test_filter_by_category` | "Категория тормозная система" | `filter_auto_parts(category=...)` |
| `test_multiturn_conversation` | "Сколько запчастей + дороже 10000" | `count` + `filter` |

**Assert'ы:**
- Должен быть хотя бы один `grep_*` или `filter_*` вызов
- **Ни одного** `search_*`, `simple_*`, `find_*`, `list_*`
- Пустые вызовы (`grep_product({})`) — автоматически rejected на уровне MCP gateway

### 4b. Логирование LLM E2E

Каждый тест выводит через `-s`:

#### SSE-лог (поток событий)
```
📊 Session: e2e-llm-abc123
🔄 Iterations: 4
📋 Status flow: iteration=0 tool_calls, iteration=1 tool_calls, ...

🛠️  Tool calls (4):
  [0] schema_auto_parts({})
  [1] filter_auto_parts({"category": "Тормозная система"})
  [2] get_auto_parts({"id": "16"})
  [3] get_auto_parts({"id": "17"})

🧠 Reasoning: (мысли модели, если есть в SSE)
💬 Final answer: ... (текст ответа модели)
```

#### Backlog (файл на диске, `backlog/agent_e2e-llm-test_*.jsonl`)
```
=== agent_e2e-llm-test_e2e-llm-abc123.jsonl ===
  🟢 START: Покажи запчасти из категории тормозная система
  🤖 LLM  iter=0 tokens=12150+85 dur=9741.54ms
  🛠️  CALL iter=0 schema_auto_parts({})
  📦 RESULT schema_auto_parts
  🤖 LLM  iter=1 tokens=12967+118 dur=28944.72ms
  🛠️  CALL iter=1 filter_auto_parts({"category": "Тормозная система"})
```

Backlog пишется в `backlog/` (управляется `BACKLOG_DIR`, `BACKLOG_MODE` env vars).
По умолчанию `BACKLOG_MODE=full` — пишется всё. Для production `BACKLOG_MODE=errors`.

### 4c. Известные грабли (из опыта)

| Проблема | Симптом | Решение |
|---|---|---|
| **Tenant не существует** | LLM отвечает текстом, `tool_calls=[]` | `test_search_e2e.py` создаёт tenant сам через `setup_module()` |
| **search_* тулы всё ещё есть** | LLM вызывает `search_auto_parts` | Проверить что конфиг перегенерирован и `types.go` знает все стратегии |
| **LiteLLM routing на неверный provider** | LLM зависает на десятки секунд | Убедиться что `provider_priority: ["polza"]` и `llm_config.provider: "polza"` |
| **Схема большая (>10K chars)** | LLM "забывает" первые сущности | `_build_schema_message` — проверять длину в логах: `Injected schema ... (8707 chars)` — OK |
| **Нет User-Agent заголовка** | `Request blocked: Blocked User-Agent` | Добавить `User-Agent: Mozilla/5.0 (compatible; HelperiumE2E/1.0)` |
| **session_id повторяется** | Backlog дописывается, asserts по tool_calls неверные | Каждый тест генерирует **уникальный** session_id |

## 5. Написание LLM E2E тестов — рекомендации

### Структура теста

```python
def test_my_scenario(self):
    # 1. Создать локальный tenant с изолированной БД
    tid = _register_tenant(db_path)     # ← создаёт + rewrite
    _ensure_agent(agent_name, tid)        # ← создаёт/обновляет агента

    # 2. Проверить что MCP жив
    tools = _check_mcp_accessible(tid)    # ← assert grep/filter/schema есть
    assert not any("search_" in t for t in tools)

    # 3. Отправить вопрос (уникальный session_id!)
    result = _chat(agent_name, tid, "вопрос")

    # 4. Записать лог
    _log_result(result)                   # ← SSE + backlog

    # 5. Assert'ы
    tool_names = [tc["name"] for tc in result["tool_calls"]]
    assert any(n.startswith("grep_") for n in tool_names)
    assert not any(n.startswith("search_") for n in tool_names)
```

### Чего НЕ делать

- ❌ Не использовать хардкоженные tenant'ы (`_TENANT = "autoparts"`) — они не live после рестарта
- ❌ Не проверять **текст** ответа LLM — только инструменты
- ❌ Не гонять LLM тесты в цикле (50K токенов за 4 теста)
- ❌ Не писать assert'ов на `reasoning_content` — модель может не вернуть
- ❌ Не использовать один `session_id` на несколько тестов — история накапливается

### Бенчмарки (будущее)

Для нагрузочного тестирования pipeline (без LLM):

```bash
# Замерить время ToolDiscoveryStage + MCP handshake
uv run pytest tests/e2e/llm/test_search_e2e.py -v -s --benchmark-only
```

Планируемые метрики:
- **MCP handshake**: время от POST /api/chat до ToolDiscovery (schema injected)
- **Tool execution**: latency первого tool call
- **Iterations per query**: сколько раундов LLM нужно для ответа
- **Token efficiency**: prompt_tokens / completion_tokens ratio

## 6. Mutation testing

**Python (mutmut, ~30 мин):**
```bash
./scripts/run_mutmut.sh --build    # сборка Docker (1 раз)
./scripts/run_mutmut.sh --docker   # запуск
```
Score: ~65% (8100+ KILLED / 2681 SURVIVED).

**Go (go-mutesting, ~5 мин):**
```bash
./scripts/run_mutmut.sh --go
```
