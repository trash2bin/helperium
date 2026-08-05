# Core Benchmark

Детерминированный бенчмарк core-логики агента Helperium (harness, модель, конфигурация БД) **без LLM-судьи**.

Меряет:
- **Retrieval** — нашёл ли агент нужные данные (в tool_results есть ground-truth атом)
- **Answer delivery** — ответил ли корректно в final_text
- **Галлюцинации** — нет ли в ответе чисел/фактов, не подтверждённых тулами
- **Отказ** — корректный отказ на отсутствующие данные (absence-кейсы)
- **Стоимость/скорость** — токены, duration, p95, стоимость (из backlog `turn_end`)
- **Чтение схемы** — использует ли агент правильные имена сущностей
- **Устойчивость** — доходит ли до ответа после ошибок тулов (recovery)

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
Не тратит денег, детерминирован. `count`-кейс в смоуке падает — это ожидаемо
(скрипт жёстко про артикул, не делает count).

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
- `list_ids` — `{"min_count": 1}` — в tool_results хотя бы N строк

## Метрики

| Метрика | Откуда |
|---|---|
| success_rate | retrieval AND answer AND NOT hallucination |
| retrieval_success_rate | атом найден в tool_results |
| answer_delivery_rate | атом в final_text (с синонимами статусов) |
| hallucination_rate | число в ответе, не подтверждённое тулами |
| groundedness_rate | 1 - hallucination_rate |
| refusal_correct_rate | absence-кейсы с корректным отказом |
| tool_error_rate | % кейсов с tool_errors > 0 (backlog) |
| **entity_name_accuracy** | % кейсов, где агент использует правильные имена сущностей (catalog_order, не Order) |
| **recovery_rate** | % кейсов с tool_errors, где агент всё равно дошёл до final (устойчивость к мусорным аргументам) |
| avg_* / p95 | из backlog turn_end (tokens, duration, cost, calls) |

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

## Тесты

```bash
uv run --package agent-db pytest tests/test_bench_core.py -q
# 26 тестов: evaluator (tool/retrieval/answer/hallucination/refusal/entity/recovery),
# backlog parser, report aggregation, SSE parsing — без LLM, без сети.
```

## Ограничения / вне скоупа

- Один прогон = один вопрос (no multi-turn).
- Без LLM-судьи (v2).
- JSONB-поиск (car_applicability) — не поддерживается тулами (см. [search-strategies.md](../../doc/agents/search-strategies.md)).
- `final_text` в backlog обрезается до 2000 символов (для полного — SSE или bench-лог).
