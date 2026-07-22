# Регрессионное тестирование

Перед коммитом/пушем:

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

Все 58 тестов зелёные, работают без запущенных сервисов.

**Правила написания тестов для Stage'ов:**

```python
from .helpers import TestLLMProvider, TestMCPProvider, llm_response, make_pipeline_ctx

async def test_my_stage():
    llm = TestLLMProvider()
    llm.queue(llm_response.final("answer"))

    mcp = TestMCPProvider()
    mcp.add_tool("find", {"ok": True, "data": {"id": "1"}})

    ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)

    stage = MyStage()
    events = await collect_events(stage.run(ctx))
    assert any(t == "final" for t, _ in events)
```

**Тестирование Pipeline целиком:** использовать `Pipeline(stages=[LLMStage()], finalizer_stages=[...])` — не забыть параметр `finalizer_stages` если тестится финализация.

```python
from .helpers import make_pipeline_ctx
from api_service.agent.pipeline import Pipeline

ctx = await make_pipeline_ctx()
pipeline = Pipeline(stages=[...], finalizer_stages=[...])
```

## 2. Go Unit/Integration

```bash
go test ./data-service/... ./mcp-gateway/...   # ~585 тестов
```

### 2b. Embed Widget

```bash
cd api-service/embed && npm test           # 59 тестов (vitest)
cd api-service/embed && bash build.sh      # typecheck + esbuild
```

> ⚠️ После пересборки виджета: `./scripts/dev.sh restart api`

## 3. E2E (pytest, рекомендуется)

```bash
uv run pytest tests/e2e/ -v                  # 96 тестов, без LLM
uv run pytest tests/e2e/test_data_isolation.py -v
uv run pytest tests/e2e/agents -v
uv run pytest tests/e2e/ -v --llm-key        # 101 тест с LLM (5 LLM integration)
uv run pytest tests/e2e/llm/ -v              # только LLM (#MISTRAL_API_KEY)
uv run pytest tests/e2e/test_search_strategies.py -v    # 31 тест: 26 стратегий + 5 LLM
uv run pytest tests/e2e/test_mcp_validation.py -v       # 9 тестов: валидация required
```

### 3a. Search Strategies E2E — `tests/e2e/test_search_strategies.py`

Проверяет новые search strategies (search) с авто-генерированным конфигом. Использует сценарии `auto-shop` и `clinic` (`tests/scenarios/`).

**31 тест — 3 класса:**

| Класс | Тестов | Описание |
|---|---|---|
| `TestAutoShopStrategies` | 13 | grep/filter/count на авто-магазине (35 запчастей, 10 категорий, заказы, клиенты) |
| `TestClinicStrategies` | 13 | grep/filter/count на клинике (10 врачей, 42 приёма, пациенты, назначения) |
| `TestLLMImplicitIntent` | 5 | LLM чат с неявным интентом (требует `OPENAI_API_KEY` или `LLM_API_KEY`) |

**Что проверяет:**

- **grep** — `test_grep_glushiteli`, `test_grep_multi_token`, `test_grep_not_found`, `test_grep_doctor_by_specialization`, `test_grep_appointments_by_medication_in_notes`, `test_grep_prescriptions_by_medication`, `test_customers_grep_by_name`, `test_grep_with_limit_and_format_full`
- **filter** — `test_filter_by_category`, `test_filter_price_gt`, `test_filter_price_lte`, `test_filter_in_stock`, `test_filter_appointments_by_status`, `test_filter_appointments_by_reason_like`, `test_filter_doctors_by_experience_gt`, `test_filter_doctors_by_rating_gte`, `test_filter_patients_by_city`, `test_filter_appointments_date_range`, `test_orders_filter_by_status`
- **count** — `test_auto_parts_count`, `test_count_doctors`, `test_count_appointments`
- **manifest** — `test_manifest_has_search_tools`, `test_manifest_has_clinic_tools`
- **rewrite** — `test_rewrite_generated_entities`, `test_rewrite_generated`
- **LLM implicit intent** — `test_ask_for_muffler`, `test_ask_for_cheap_brakes`, `test_ask_for_all_available`, `test_ask_for_bmw_parts`, `test_ask_for_engine_oil`

**Зависимости:**
- Запущенные сервисы api-service (:8081), data-service (:8084), mcp-gateway (:8083)
- `ADMIN_TOKEN` для tenant lifecycle
- `OPENAI_API_KEY` / `LLM_API_KEY` для LLM тестов

### 3b. MCP Validation — `tests/e2e/test_mcp_validation.py`

Проверяет что MCP-гейтвей и data-service отклоняют пустые/невалидные вызовы инструментов.

**9 тестов — 4 класса:**

| Класс | Тестов | Описание |
|---|---|---|
| `TestGetWithRequired` | 3 | `get_*({})` → isError (требует id); с id → OK; несколько get_* тулов |
| `TestSearchWithRequired` | 3 | `search_*({})` → isError (требует pattern); с pattern → OK; несколько search_* тулов |
| `TestAllToolsHaveRequiredGuard` | 1 | Каждый tool (кроме count_*) имеет `required` параметр |
| `TestLimitHasMaxBound` | 2 | limit в схеме тула; limit=9999999 → isError |

**Проблема (regression guard):** LLM шлёт `get_catalog_product({})` и `search_catalog_product({})` с пустыми аргументами. Тесты проверяют что:
- Пустой вызов `get_*({})` → `isError` c сообщением о required `id`
- Пустой вызов `search_*({})` → `isError` c сообщением о required `pattern`
- Чрезмерный `limit` (9999999) → `isError`
- Все тулы кроме `count_*` имеют хотя бы один required параметр

**Зависимости:**
- Запущенные сервисы data-service (:8084), mcp-gateway (:8083)
- `ADMIN_TOKEN` — читает конфиг tenant'а `autoparts`
- Tenant `autoparts` должен быть зарегистрирован (см. `tests/scenarios/`)

## 4. Mutation testing

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
