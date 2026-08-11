# Core Benchmark

Детерминированный бенчмарк core-логики агента Helperium (harness, модель, конфигурация БД) **без LLM-судьи**.

Меряет:
- **Retrieval** — нашёл ли агент нужные данные (в tool_results есть ground-truth атом)
- **Answer delivery** — ответил ли корректно в final_text
- **Галлюцинации** — нет ли в ответе чисел/SKU, не подтверждённых тулами
- **Отказ** — корректный отказ на отсутствующие данные (absence-кейсы)
- **Стоимость/скорость** — токены, duration, p95, стоимость (из backlog `turn_end`)
- **Чтение схемы** — использует ли агент правильные имена сущностей
- **Устойчивость** — доходит ли до ответа после ошибок тулов (recovery)

Добавлены **явный verdict** (CORRECT/PARTIAL/WRONG/ERROR) и
**таксономия ошибок** (ErrorClass) — контракт бенча как продукта. Теперь по
каждому кейсу видно не только «прошёл/не прошёл», а класс дефекта:
LOST_TOTAL (знал 40, сказал «много»), HALLUCINATED_SKU (выдуманный артикул),
FALSE_UNCERTAINTY («скорее всего» при точных данных), TOOL_OVERUSE (бюджет),
TOOL_LOOP, SCHEMA_ENTITY_ERROR, INFRA_ERROR (error payload ≠ данные) и др.

## Структура

```
agent_db/bench/
├── cases/autoparts.json   # 49 кейсов (lookup/filter/count/absence/status/search)
├── runner.py              # ходит в POST /api/chat/{agent} (SSE), читает backlog, пишет bench-лог
├── backlog_parser.py      # backlog JSONL → BacklogData (turn_end)
├── evaluator.py           # детерминированные проверки (без LLM-судьи)
├── report.py              # агрегация метрик + печать + JSON
├── cli.py                 # typer CLI (python -m agent_db.bench run <cases>)
├── __main__.py            # точка входа (дропает "run" для typer-совместимости)
├── models.py              # dataclasses (TestCase, RunResult, BacklogData, EvalResult...)
├── smoke_scripted.py      # dev-смоук: поднимает api-service со ScriptedLLMProvider
└── README.md              # этот файл
```

## Запуск

```bash
# 1. Стек поднят (./scripts/dev.sh restart) + tenant autoparts зарегистрирован + агент создан
# 2. Прогон (реальная LLM через polza/ollama):
uv run --package agent-db python -m agent_db.bench run \
    agent_db/bench/cases/autoparts.json \
    --agent-name autoparts-assistant \
    --tenant-id autoparts \
    --api-url http://127.0.0.1:8081 \
    --admin-token secret \
    --backlog-dir ./backlog \
    --bench-log-dir ./bench-backlog \
    --output benchmark_report.json \
    --delay 2.5          # пауза между кейсами (rate limit api-service: 30/мин)
```

### Смоук без LLM (ScriptedLLMProvider)

```bash
uv run --package agent-db python -m agent_db.bench.smoke_scripted
```

Поднимает отдельный api-service с `USE_SCRIPTED_LLM=1` на свободном порту,
создаёт агента, гоняет 3 кейса (lookup / count / absence) через реальный SSE+backlog.
Не тратит денег, детерминирован.

- `lookup`-кейс проходит (скрипт отвечает по артикулу EXT-01392 → 2751).
- `count`-кейс падает — **ожидаемо** (скрипт жёстко про артикул, не делает count;
  см. `SMOKE FAIL` в выводе).
- `absence`-кейс проходит (отдельный скрипт-отказ).

Требует поднятых data-service (:8084) и mcp-gateway (:8083) с tenant `autoparts`
и детерминированной БД (seed_fixture.py, seed=42).

## Формат кейсов

```json
{
  "id": "product-lookup-article-001",
  "question": "Сколько стоит артикул EXT-01392?",
  "category": "lookup",
  "ground_truth": {"type": "exact_value", "query": "SELECT ...", "expected": {"price": 3064}},
  "expected_tool": {"must_call_any": ["filter_catalog_product", "db_get"], "must_not_call": ["db_map"]},
  "tags": ["product", "price"],
  "expect_refusal": false,
  "status_synonyms": {"shipped": ["отправлен", "в пути"]}
}
```

**Типы ground_truth:**
- `exact_value` — точное значение (`{"price": 3064}`), проверяется в tool_results и final_text
- `count` — количество (`{"count": 74}`), слово-граничное число
- `not_found` — данных нет; `expect_refusal: true` → проверяется отказ + отсутствие выдумки
- `list_ids` — `{"min_count": 1}` — в tool_results хотя бы N уникальных строк (dedupe по id/article)

**Расширения (опциональные):**

```json
{
  "budget": {"max_tool_calls": 5, "max_db_get": 3, "max_llm_calls": 4, "max_tokens": 12000, "max_cost_usd": 0.08},
  "ground_truth": {
    ...
    "answer_rules": {"expect_total_mentioned": true},
    "check_skus": true,
    "any_of_skus": ["EXT-01392", "BRK-01004"]
  }
}
```

- `budget.max_*` — нарушение → `TOOL_OVERUSE` (verdict PARTIAL)
- `answer_rules.expect_total_mentioned` — `total:N` в тулах, в ответе нет N + vague-маркер → `LOST_TOTAL` (PARTIAL)
- `check_skus` / `any_of_skus` — выдуманный артикул в ответе → `HALLUCINATED_SKU` (WRONG)

## Verdict и таксономия ошибок

Каждый кейс получает `verdict: CORRECT | PARTIAL | WRONG | ERROR` и список
`error_classes` (стабильные коды из `models.ErrorClass`):

| Verdict | Когда | Примеры error_classes |
|---|---|---|
| **CORRECT** | всё чисто | — |
| **PARTIAL** | major/minor без critical | LOST_TOTAL, FALSE_UNCERTAINTY, TOOL_OVERUSE, TOOL_LOOP, SCHEMA_ENTITY_ERROR, FORBIDDEN_TOOL |
| **WRONG** | critical: галлюцинация/неверный факт/отказ | HALLUCINATED_SKU, HALLUCINATED_NUMBER, WRONG_FACT, WRONG_AVAILABILITY, WRONG_STATUS, ANSWER_MISS, RETRIEVAL_MISS, REFUSAL_MISSING |
| **ERROR** | infra/tool/bench сбой | INFRA_ERROR (error payload/timeout/HTTP), BENCH_ERROR |

`error_source` разделяет источник: `agent` (плохой ответ) vs `tool`/`infra`
(упал сервис — не вина агента). Раньше `{"error": "timeout"}` считался
данными (баг) — теперь это INFRA_ERROR.

## Метрики

| Метрика | Откуда |
|---|---|
| success_rate | retrieval AND answer AND NOT hallucination (legacy) |
| **verdicts** (CORRECT/PARTIAL/WRONG/ERROR доли) | `_compute_verdict` |
| **error_classes** (histogram по классам) | ErrorClass |
| retrieval_success_rate | атом найден в tool_results |
| answer_delivery_rate | атом в final_text (с синонимами статусов) |
| hallucination_rate | число/SKU в ответе, не подтверждённое тулами |
| groundedness_rate | 1 - hallucination_rate |
| refusal_correct_rate | absence-кейсы с корректным отказом |
| tool_error_rate | % кейсов с tool_errors > 0 (backlog) |
| **entity_name_accuracy** | % кейсов, где агент использует правильные имена сущностей (catalog_order, не Order) |
| **recovery_rate** | % кейсов с tool_errors, где агент всё равно дошёл до final (устойчивость к мусорным аргументам) |
| avg_* / p50 / p95 | из backlog turn_end (tokens, duration, cost, tool_calls, llm_calls) |
| avg_repeated_tool_calls / avg_unique_tool_calls / avg_db_get | fanout/перебор |

## Отдельное логгирование бенча

Каждый прогон пишется в **отдельный каталог** `bench-backlog/` (не смешивается с общим `backlog/` api-service):

- `{session_id}.bench.jsonl` — **полный trace**: question, final_text (полный!), все SSE-события (tool_call/tool_result), метрики backlog. Один прогон = одна строка.
- `agent_{agent}_{session}.jsonl` — копия исходного backlog-файла api-service.

Каталог задаётся `--bench-log-dir` (default: `./bench-backlog`). В `final_text` добавлен в backlog `turn_end` (api-service) — теперь ретро-анализ без SSE возможен (обрезается до 2000 символов).

## Детерминированная база (seed=42)

Foreign `seed_data.py` не фиксирует random → база меняется при каждом seed, ломая
ground truth. Для бенча используется helperium-owned скрипт
`demo/autoparts-store/seed_fixture.py` (фиксирует `random.seed(42)` + `Faker.seed(42)`):

```bash
cd demo/autoparts-store
DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell < seed_fixture.py
```

Даёт **детерминированную базу**: 30 брендов, 117 категорий, 407 товаров, 6 заказов.
**Внимание:** id меняются между прогонами (sequence не сбрасывается), но артикулы,
цены, статусы, счётчики — стабильны. Кейсы используют артикулы/значения, не id.

## Важные нюансы (узнано на проде)

1. **`db_search`/`filter_*` возвращают `{id, name}` превью + `total`/`returned`** — полные поля видны только через `db_get`. Для lookup-по-цене агент должен: `filter` → `db_get(id)`. Кейсы это отражают (`must_call_any` включает `db_get`).
2. **Пустой результат = `{"empty_hint": {...}}`** (не `{"preview":[]}`) — evaluator распознаёт `empty_hint` как «пусто».
3. **Backlog-файл** называется `agent:{agent}:{client_session}.jsonl` — парсер ищет по подстроке `session_id` в имени.
4. **Числа-коды** (`EXT-01392`, `АП-100005`) НЕ считаются галлюцинацией (извлекаются только standalone-числа 2+ цифр).
5. **Проценты** («скидка ~20%») — вычисленные моделью, НЕ галлюцинация (исключаются из проверки).
6. **Rate limit api-service** — `CHAT_RATE_LIMIT` (default 30/мин). Бенч добавляет `--delay 2.5` + retry на 429.
7. **`order_number` фильтруется** — `filter_catalog_order({order_number:"АП-100005"})` работает (добавлено в дефолтные filterable-правила).
8. **Модель** — для прогона нужна без лимита (local ollama или polza/deepseek). Облачные `:cloud` могут иметь сессионный лимит.

### evaluator-фиксы (по рой-аудиту, 2026-08-06)

9. **Bool-значения** (`available: true`) матчатся семантически: «в наличии/доступен» ↔ `True`, «закончился/нет» ↔ `False` (не literal `"true"`). Негативные маркеры проверяются первыми («нет в наличии» больше не матчится как True); слабые маркеры (`да/есть/нет/1/0`) убраны.
10. **Морфология стран** (`country: Германия`) — предпочтительны явные `country_aliases` из ground truth; fallback — корень слова (≥4 буквы).
11. **Производные числа** — суммы/произведения подтверждённых данных НЕ галлюцинация: `677×3=2031` (regex), «плюс ещё 5», «итого N». Произвольные произведения всех чисел больше не легализуются (только при арифметическом контексте).
12. **Discovery-тулы** (`db_map`, `db_describe`) НЕ считаются данными в absence-кейсах — модель зовёт их, получает схему (непусто), но retrieval остаётся пустым.
13. **Табличные № строк** (1..10 в markdown-таблице) — одиночные цифры, не факты (уже фильтровались; закреплено тестом).

### Фиксы (2026-08)

14. **Error payload ≠ данные** — `{"error": "timeout"}` больше не считается строкой данных (был баг: INFRA_ERROR детектился как retrieval). Теперь → `INFRA_ERROR`, `error_source="tool"`.
15. **Dedupe в min_count** — retrieval completeness по уникальным сущностям `(entity, id)`/`article`, а не по сырым строкам (20 preview + 9 db_get тех же товаров ≠ 29).
16. **SKU-проверка** — выдуманный артикул → `HALLUCINATED_SKU` (включается через `check_skus`/`any_of_skus`; SKU из вопроса и арифметического контекста не считаются).
17. **LOST_TOTAL** — `total:N` в тулах, в ответе vague «много» → `LOST_TOTAL` (PARTIAL, не WRONG).
18. **FALSE_UNCERTAINTY** — «скорее всего» при подтверждённом факте → `FALSE_UNCERTAINTY` (PARTIAL).
19. **TOOL_OVERUSE / TOOL_LOOP** — бюджет (`budget.max_*`) и loop_warnings из backlog проброшены в EvalResult.

## Тесты

```bash
uv run --package agent-db pytest tests/test_bench_core.py -q
# 64 теста: evaluator (tool/retrieval/answer/hallucination/refusal/entity/recovery,
# verdict, error classes, SKU, LOST_TOTAL, FALSE_UNCERTAINTY, budget, dedupe, error payload,
# derived numbers), backlog parser, report aggregation + percentiles, SSE parsing — без LLM, без сети.
```

## Ограничения / вне скоупа

- Один прогон = один вопрос (no multi-turn).
- Без LLM-судьи (v2).
- JSONB-поиск (car_applicability) — не поддерживается тулами (см. [search-strategies.md](../../../../doc/agents/search-strategies.md)).
- `final_text` в backlog обрезается до 2000 символов (для полного — SSE или bench-лог).

---
**Last verified:** 2026-08-11 (рабочая ветка) — verdict (CORRECT/PARTIAL/WRONG/ERROR), таксономия ErrorClass, новые проверки (SKU вкл. кириллицу, LOST_TOTAL, FALSE_UNCERTAINTY, budget, loop, dedupe, error payload, derived), отчёт (verdicts/percentiles/run_metadata). 64 теста.
