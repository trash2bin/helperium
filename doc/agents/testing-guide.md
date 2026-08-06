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
uv run pytest src/api_service/tests/unit/agent/test_stages.py -v                    # 25 Stage-тестов
uv run pytest src/api_service/tests/unit/agent/test_middlewares.py -v                # 8 Middleware-тестов
uv run pytest src/api_service/tests/unit/agent/test_error_flow.py -v                # 18 Error-flow тестов
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_e2e.py -v           # 5 интеграционных (+TestLLMAgentWithProtocolProvider)
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_fixes.py -v         # 7 регрессионных
uv run pytest src/api_service/tests/unit/agent/test_tool_parser_extensive.py -v      # 48 парсинг-тестов (unit + pipeline E2E)
uv run pytest src/api_service/tests/unit/agent/test_tool_parser.py -v                # 1 уникальный unit-тест
```

Все **198 тестов** зелёные, работают без запущенных сервисов. Основная масса — тесты на парсинг
JSON-тулов из LLM ответа (`test_tool_parser_extensive.py`): ToolCallParser unit, Safety Net,
pipeline E2E, token leak, iteration budget.

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
# Нативный прогон (нужны поднятые сервисы: ./scripts/dev.sh start)
./scripts/dev.sh e2e            # полный прогон tests/e2e/

# Или напрямую
uv run pytest tests/e2e/ -v --tb=short

# Docker (colima/docker desktop, profile test — всё поднимает сам)
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose --profile test up e2e --abort-on-container-exit --exit-code-from e2e
```

**124 теста** (без скипов). Все зелёные — включая проверку атомарной записи конфига
(`test_config_write_is_atomic` — temp+rename, без `.bak`).

### 3.0 Расширяемая архитектура — `TestTenant` + fixture-фабрики

Слои:

```
tests/e2e/
├── helpers.py        # чистые блоки БЕЗ pytest: TestTenant, make_tenant, parse_sse_stream
├── conftest.py       # pytest-мост: factory-fixtures tenant() / tenants()
└── test_*.py         # тонкие тесты на фабриках
```

Добавить тест = 10 строк:

```python
def test_x(tenant):
    t = tenant("clinic")            # seed + register + авто-cleanup
    assert t.tools()                  # mcp-тулы готовы (db_*/filter_*)

def test_isolation(tenants):
    a = tenants("sqlite-testseed")
    b = tenants("sqlite-testseed", prefix="other")
```

- `tenant("auto-shop")` — сценарии с `create_db.py` авто-перегенерируют БД
  (`ensure_scenario_db`), не нужны ручные bash-команды
- `make_tenant` сам решает: config.json+seed.json → `seed_database`;
  только create_db.py → `create_scenario_db` + авто-rewrite
- Сценарии без config.json (auto-shop/clinic) регистрируются через rewrite
  (introspection генерит entities/endpoints/tools)
- `TestTenant.tools()` читает `mcp_tools` из manifest

Бенчи могут переиспользовать `helpers.py` напрямую (чистые функции без pytest).

### 3a. Search Strategies E2E — `tests/e2e/test_search_strategies.py`

Проверяет grep/filter/schema стратегии с авто-генерированным конфигом.
Использует сценарии `auto-shop` и `clinic` (`services/data-service/testdata/scenarios/`).

**31 тест — 2 класса (v5):**

| Класс | Тестов | Описание |
|---|---|---|
| `TestAutoShopStrategies` | 16 | grep/filter/schema/count на авто-магазине (v5: `db_search`/`filter_*`) |
| `TestClinicStrategies` | 15 | grep/filter/schema/count на клинике (v5) |

> `TestLLMImplicitIntent` переехал в `tests/e2e-llm/test_implicit_intent.py` (opt-in, требует LLM-ключ).

Проверяет: `/entity/grep`, `/entity/filter`, `/entity/schema`, `/entity/count`,
`/entity/distinct` эндпоинты, MCP manifest (v5: `db_map`/`db_describe`/`db_search`/`db_get`/
`db_related`/`filter_{entity}`). Никаких `grep_*`/`schema_*`/`get_*`/`count_*` per-entity тулов.

**Зависимости:** все сервисы запущены, `ADMIN_TOKEN` задан.

### 3b. MCP Validation — `tests/e2e/test_mcp_validation.py`

Проверяет что MCP-гейтвей и data-service отклоняют пустые/невалидные вызовы.

**Тесты (v5):**

| Класс | Описание |
|---|---|
| `TestDBSearchWithRequired` | `db_search({})` → isError; с pattern → OK; длинный regex → isError |
| `TestDBGetWithRequired` | `db_get({})` → isError; с id → OK |
| `TestAllToolsHaveRequiredGuard` | Каждый tool имеет `required` параметр |
| `TestLimitHasMaxBound` | limit в схеме; limit=9999999 → isError |
| `TestToolComposition` | 5 `db_*` + `filter_{entity}`; нет per-entity `grep_*`/`schema_*`; displayName |

### 3c. Scripted LLM — `tests/e2e/test_scripted_llm.py`

Pipeline с `ScriptedLLMProvider` — **без живой модели** (мок LLM, читает JSONL-скрипт).
Поднимает api-service как subprocess с `USE_SCRIPTED_LLM=1`, гоняет тулы через
реальный SSE endpoint. Не тратит деньги, детерминированно.

**11 тестов (v5):**

| Тест | Проверяет |
|---|---|
| `test_basic_pipeline` | тулы вызываются, имена не пустые, доходит до финала |
| `test_empty_call_blocked` | пустые вызовы блокируются |
| `test_tool_name_not_empty_in_sse` | SSE tool_call/tool_result имеют непустые имена |
| `test_v5_tool_chain` | v5: `db_map`/`filter_auto_parts` доступны через MCP |
| `test_v5_related_and_map` | `db_map` + `db_related` работают |
| `test_v5_no_legacy_grep_tools` | per-entity `grep_*`/`schema_*` не существуют (v5) |
| `test_error_recovery` | ошибка тула не валит pipeline |
| `test_exhausted_script_guard` | скрипт закончился — pipeline не вечный цикл |
| `test_empty_call_rejected_at_mcp` | `db_search({})` отклонён валидацией (required) |
| `test_empty_llm_round_guard` | пустой LLM round → guard |
| `test_recording_mode` | record_to пишет JSONL |

Запуск: `USE_SCRIPTED_LLM=1 SCRIPTED_LLM_PATH=script.jsonl` (dev-режим api-service)
или `./scripts/dev.sh e2e -k scripted`.

### 3d. Остальные файлы `tests/e2e/`

| Файл | Что проверяет |
|---|---|
| `test_admin_lifecycle.py` | регистрация/листинг/удаление tenant'ов через admin API |
| `test_config_persistence.py` | конфиг tenant'а пишется в `.data/tenants/{id}.json`; атомарная запись (temp+rename, без `.bak`) |
| `test_data_isolation.py` | tenant A не видит данные tenant B |
| `test_mcp_dynamic.py` | v5 тулы (`db_map`/`db_search`/`db_get`/`filter_*`) через MCP |
| `test_mcp_composite.py` | composite mode (`X-Tenant-ID: a,b` → префикс `{tenantID}__`) |
| `test_sse_session.py` | SSE-сессия mcp-gateway (endpoint, JSON-RPC initialize/tools_list) |

## 4. E2E с LLM (opt-in, `tests/e2e-llm/`)

**Не в CI.** Требуют реальный LLM API-ключ (OPENAI/LLM_API_KEY) и денег.
Без ключа — все тесты скипаются (skipif), не падают.

```bash
uv run pytest tests/e2e-llm/ -v
```

| Файл | Что проверяет |
|---|---|
| `test_implicit_intent.py` | LLM сам догадывается вызвать `db_search`/`filter_*` (неявный интент) |
| `test_llm_chat.py` | SSE chat через HTTP, agent endpoint, tool call + response |
| `test_search_e2e.py` | discovery → search → filter → multiturn диалог |
| `test_search_strategy.py` | grep/filter/schema через MCP (diagnostic) |

Подробнее: `tests/e2e-llm/README.md`.

### 4b. Логирование LLM E2E

Каждый тест выводит через `-s`:

#### SSE-лог (поток событий)
```
📊 Session: e2e-llm-abc123
🔄 Iterations: 4
📋 Status flow: iteration=0 tool_calls, iteration=1 tool_calls, ...

🛠️  Tool calls (4):
  [0] db_describe({})
  [1] filter_auto_parts({"category": "Тормозная система"})
  [2] db_get({"entity": "auto_parts", "id": "16"})
  [3] db_get({"entity": "auto_parts", "id": "17"})

🧠 Reasoning: (мысли модели, если есть в SSE)
💬 Final answer: ... (текст ответа модели)
```

#### Backlog (файл на диске, `backlog/agent_e2e-llm-test_*.jsonl`)
```
=== agent_e2e-llm-test_e2e-llm-abc123.jsonl ===
  🟢 START: Покажи запчасти из категории тормозная система
  🤖 LLM  iter=0 tokens=12150+85 dur=9741.54ms
  🛠️  CALL iter=0 db_describe({})
  📦 RESULT db_describe
  🤖 LLM  iter=1 tokens=12967+118 dur=28944.72ms
  🛠️  CALL iter=1 filter_auto_parts({"category": "Тормозная система"})
```

Backlog пишется в `backlog/` (управляется `BACKLOG_DIR`, `BACKLOG_MODE` env vars).
По умолчанию `BACKLOG_MODE=full` — пишется всё. Для production `BACKLOG_MODE=errors`.

### 4c. Известные грабли (из опыта)

| Проблема | Симптом | Решение |
|---|---|---|
| **Tenant не существует** | LLM отвечает текстом, `tool_calls=[]` | e2e-llm тесты создают tenant сами через `setup_module()` |
| **v4 тулы всё ещё в конфиге** | LLM вызывает `grep_auto_parts` (легаси) | Проверить что конфиг перегенерирован (v5: `db_*` + `filter_*`); `configgen` версия 4 |
| **LiteLLM routing на неверный provider** | LLM зависает на десятки секунд | Убедиться что `provider_priority` и `llm_config.provider` совпадают |
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
    tools = _check_mcp_accessible(tid)    # ← assert v5: db_* + filter_* есть
    assert not any(n.startswith(("grep_", "schema_")) for n in tools)

    # 3. Отправить вопрос (уникальный session_id!)
    result = _chat(agent_name, tid, "вопрос")

    # 4. Записать лог
    _log_result(result)                   # ← SSE + backlog

    # 5. Assert'ы
    tool_names = [tc["name"] for tc in result["tool_calls"]]
    assert any(n.startswith(("db_", "filter_")) for n in tool_names)
    assert not any(n.startswith(("grep_", "schema_")) for n in tool_names)
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
uv run pytest tests/e2e-llm/ -v -s --benchmark-only
```

Планируемые метрики:
- **MCP handshake**: время от POST /api/chat до ToolDiscovery (schema injected)
- **Tool execution**: latency первого tool call
- **Iterations per query**: сколько раундов LLM нужно для ответа
- **Token efficiency**: prompt_tokens / completion_tokens ratio

## 6. Mutation testing
```bash
./scripts/run_mutmut.sh --build    # сборка Docker (1 раз)
./scripts/run_mutmut.sh --docker   # запуск
```
Score: ~65% (8100+ KILLED / 2681 SURVIVED).

**Go (go-mutesting, ~5 мин):**
```bash
./scripts/run_mutmut.sh --go
```
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)
