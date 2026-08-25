# Core Benchmark — операционная документация

> Реализация дизайна из [doc/benchmark/README.md](README.md). Живой код — в
> `services/agent-db/agent_db/bench/` (см. его [README](../../services/agent-db/agent_db/bench/README.md)).

## Что это

Детерминированный бенчмарк агента Helperium **без LLM-судьи** для core-метрик:
retrieval, answer delivery, галлюцинации, отказ, стоимость/скорость, чтение схемы,
устойчивость к ошибкам модели.

**Статус:** реализован, стабилен и проверен end-to-end на живом стеке (2026-08-05 → 2026-08-24):
- смоук без LLM (ScriptedLLMProvider) — 3 кейса, детерминированно
- реальные прогоны: polza/deepseek-v4-flash (3 кейса PASS, cost ≈ $0.18/кейс)
- полный прогон 49 кейсов на NIM Nemotron-3.5-lightning-30b: **plateau 83.7%**
  (40 CORRECT / 1 PARTIAL / 2 WRONG / 6 ERROR, два последовательных прогона)
- tool surface: 6 db_* tools (~4.8KB manifest), per-entity filter_* не используется
  при `strategy=schema`; `db_filter` покрывает все 35 filter-кейсов через field-reference
  operator syntax (`field__op=value`, `field__gt_field=other_field`)

## Быстрый старт

```bash
# 1. Стек
./scripts/dev.sh restart

# 2. Tenant autoparts (PG на :5434, demo/autoparts-store) + rewrite
curl -X POST http://127.0.0.1:8084/admin/tenants \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"id":"autoparts","config":{"data_source":{"driver":"postgres","dsn":"postgres://USER:PASSWORD@127.0.0.1:5434/autoparts","read_only":true}}}'
curl -X POST http://127.0.0.1:8084/admin/config/rewrite -H 'X-Tenant-ID: autoparts' \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 3. Агент (модель без лимита — polza/deepseek-v4-flash)
curl -X POST http://127.0.0.1:8081/api/agents -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"autoparts-assistant","tenant_ids":["autoparts"],
    "llm_config":{"model":"openai/deepseek-v4-flash","provider":"openai",
      "api_key":"<polza-key>","api_base":"https://polza.ai/api/v1",
      "system_prompt":"Ты — консультант автозапчастей. Отвечай на русском, используй инструменты. Не выдумывай цены и артикулы — бери из результатов инструментов."}}'

# 4. Применить versioned `autoparts-benchmark-v1` policy через Agent API,
#    а не редактировать runtime .data. Меняется только system_prompt.
uv run --package agent-db python -m agent_db.bench sync-agent-policy \
    --agent-name autoparts-assistant --api-url http://127.0.0.1:8081 \
    --admin-token "$ADMIN_TOKEN"

# 5. Прогон
uv run --package agent-db python -m agent_db.bench run \
    agent_db/bench/cases/autoparts.json \
    --agent-name autoparts-assistant --tenant-id autoparts \
    --api-url http://127.0.0.1:8081 --admin-token "$ADMIN_TOKEN" \
    --backlog-dir ./backlog --bench-log-dir ./bench-backlog \
    --output benchmark_report.json --delay 2.5
```

## Что мерить и как

### Preflight и читаемый вывод

Перед первым кейсом CLI проверяет `GET /health` и конфигурацию указанного
агента. Если API остановлен, порт закрыт или агент не найден, benchmark
завершается до отправки кейсов с явной причиной и exit code `2`; это не
считается результатом качества модели.

В консольном отчёте каждый кейс печатается в формате:

```text
[CORRECT] case-id
    Вопрос: ...
    Ответ:  ...
    Метрики: ...
```

Для `PARTIAL`/`WRONG`/`ERROR` дополнительно печатается причина. Полный ответ
также сохраняется в `benchmark_report.json` и в отдельном `.bench.jsonl` trace.

### Уровень 1 — архитектурные метрики (из backlog `turn_end`)
Уже в backlog: `duration_ms, total_tokens, total_cost, llm_calls, tool_calls,
tool_errors, empty_results, empty_rounds, iterations, outcome`. Бенч агрегирует
(avg, p95). **Готово.**

### Уровень 2 — фактическая точность (детерминированно) ← текущий бенч
Ground-truth `{question, expected}` против tool_results + final_text:
- retrieval (атом в данных, которые видел агент)
- answer (атом в ответе)
- hallucination (число в ответе без подтверждения в tool_results)
- refusal (absence-кейсы)
- entity_name_accuracy (правильные имена сущностей)
- recovery_rate (дошёл до final несмотря на tool_errors)

### Уровень 3 — LLM-судья (v2, вне скоупа)
Только для свободных/открытых вопросов. Не основа — дополнение.

## Что реально вскрыл бенч

### Модель/транспорт (2026-08-05)
1. **minimax-m3:cloud — сессионный лимит** → 45/49 кейсов `error` с
   `RateLimitError`. Не подходит для полных прогонов; перешли на NIM.
2. **deepseek-v4-flash игнорирует `total`** — на count-кейсе «Сколько Bosch» сделал
   9× `db_get` (fan-out) вместо доверия `total: 74`. 11 tool_calls, 26.5s.
3. **minimax шлёт мусорные аргументы** → 400, recovery срабатывает, но расходует turn.

### Структурные фиксы, поднявшие па Vancouver 30% → 83.7%
4. **Пер-entity `filter_{entity}` не existed** → модель не могла фильтровать по цене.
   Добавлен консолидированный `db_filter` (1831B, params: `entity` + `limit`),
   description объясняет `field__op` operators и "do NOT wrap fields".
   All 35 filter-кейсов переведены на `db_filter` в `must_call_any`.
5. **Display-name mismatch** → модель копировала имена из `db_map` (`is active`,
   `brand ID`) → `filter.go` теперь маппит и snake_case и display names.
   Невалидные поля → parse_error с списком валидных filterable полей.
6. **JSON-wrapped filter args** → слабые модели шлют `filters=[{"field":...,"operator":...}]`
   или `{"filter":{"field":...}}`. `filter.go:unwrapFilterObject` разворачивает оба
   варианта в query conditions; operator mapping `=`→eq, `!=`→neq, `>`→gt и т.д.
7. **Numeric string validation** → `limit="1"` (string) отклонялся gateway'ом.
   `tools.go` теперь принимает parseable numeric strings для всех numeric params.
8. **Hyphen normalisation** → evaluator `extract_numbers` не видел `АП‑100004` (U+2011)
   как артикул (другой hyphen в question vs tool_result). Added regex normalization
   для U+2010–U+2015, U+2011, U+2212, U+00AD.
9. **Stale DB reseed** → fixture была пере-сидирована с другим seed (139→144,
   74→83). Reseeded back to `seed=42`; ground truth matches fixture.
10. **`is_promo` rejected** → в live DB все продукты `is_promo=false`; promo ground truth
    использует `label IN ('sale','promo')`. Добавление `is_promo` в filterable fields
    дало бы ложные 0-result ответы — revertнутo; error message явно указывает на `label`.

### Remaining ceiling (plateau 83.7%)
- **AP↔АП transliteration** (`filter_catalog_order({order_number:"AP-100005"})`):
  модель систематически переводит кириллицу `АП` в латиницу `AP` → exact miss.
- **`is_promo=true`** вместо `label IN ('sale','promo')` в 2 promo-кейсах.
- **Волатильные per-case ошибки** (oil filter wrong category, Bosch pads over-search,
  ZZ-000-NOPE missing refusal marker, EXT-01401 FALSE_UNCERTAINTY).

## Кейсы: 49 active / 51 с историей

| Категория | Кол-во | Что проверяет |
|---|---|---|
| lookup | 17 | цена/бренд/страна/гарантия/наличие по артикулу, включая статусы заказов |
| filter | 9 | список товаров по категории/цене/бренду/наличию/метке, комбинированный фильтр. Вместо per-entity `filter_catalog_product` используется универсальный `db_filter` с field-reference operators (`price__gt`, `category ID`, `old_price__gt_field=price`); cases `must_call_any` принимают любой из трёх filter-способов. |
| aggregation | 17 | количество по бренду/категории/цене/статусу, включая два явных сигнала скидки |
| count | 1 | отдельный count-сценарий |
| absence | 4 | несуществующий артикул/заказ/бренд → отказ |
| search | 1 | db_search по частичному имени (→ db_get) |

Статусы заказов проверяются как теги и expected fields lookup-кейсов, а не как отдельная category. Распределение выше соответствует фактическому `cases/autoparts.json` и суммируется до 49 active cases. В fixture также сохранены два deprecated historical cases (всего 51), которые загрузчик исключает из scoring по умолчанию. Вместо неоднозначного «товар со скидкой» active set различает `old_price > price` (72 товаров) и `label IN ('sale', 'promo')` (49 товаров).

## Детерминированная база (seed=42, canonical)

Foreign `seed_data.py` не фиксирует random → данные меняются при каждом seed, ломая
ground truth. Решение — helperium-owned `demo/autoparts-store/seed_fixture.py`
(`random.seed(42)` + `Faker.seed(42)` перед `seed_data`):

```bash
cd demo/autoparts-store && DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell < seed_fixture.py
```

Даёт 30 брендов / 117 категорий / 407 товаров / 6 заказов — детерминированно. Fixture также идемпотентно задаёт PostgreSQL comments для `old_price` и `label`; после tenant rewrite configgen переносит их в descriptions параметров filter-инструмента.
**id меняются между прогонами** (sequence не сбрасывается), но артикулы/цены/статусы/счётчики стабильны — кейсы используют их, не id.
### Целостность fixture (регрессии)
- `order-count-total-001` допускает `stats`: этот tool возвращает авторитетный total для неотфильтрованного количества заказов.
- `product-lookup-hit-001` опирается на стабильный артикул `EXT-01401`; предпосылка о метке «ХИТ» удалена (label=`none`).


## Логгирование

- **Общий backlog api-service** — `backlog/agent_{agent}_{session}.jsonl` (все трассы).
- **Отдельный bench-лог** — `bench-backlog/{session}.bench.jsonl`: question, полный
  final_text, все SSE-события (tool_call/tool_result), метрики. Изолирован от
  общего backlog (не путает).
- **Логи сервисов** — `.data/logs/{api,mcp,data}.log`.

## Verdict и таксономия ошибок

Бенч теперь выдаёт по каждому кейсу не только boolean-флаги, а явный
**verdict** (`CORRECT / PARTIAL / WRONG / ERROR`) и стабильные коды ошибок
(`ErrorClass`). Цель README — «процент корректных ответов и среднее число ошибок
по классам» — теперь достижима агрегацией `verdicts` и `error_classes`.

- `CORRECT` — всё чисто.
- `PARTIAL` — major/minor дефекты без critical: `LOST_TOTAL` (знал 40, сказал
  «много»), `FALSE_UNCERTAINTY` («скорее всего» при точных данных),
  `TOOL_OVERUSE` (бюджет), `TOOL_LOOP`, `SCHEMA_ENTITY_ERROR`, `FORBIDDEN_TOOL`.
- `WRONG` — critical: `HALLUCINATED_SKU` (выдуманный артикул), `HALLUCINATED_NUMBER`,
  `WRONG_FACT`, `WRONG_AVAILABILITY`, `WRONG_STATUS`, `ANSWER_MISS`, `RETRIEVAL_MISS`,
  `REFUSAL_MISSING`.
- `ERROR` — `INFRA_ERROR` (error payload/timeout/HTTP), `BENCH_ERROR`.

`error_source` отделяет вину агента (`agent`) от сбоя сервиса (`tool`/`infra`).
Timeout errors (`{"error": "timeout"}` или request-level timeout в `run.errors`) классифицируются как `INFRA_ERROR` — не наказывают агента и учитываются в `infra_error_rate`.

**Camry-кейс** (архив: incident-camry, удалён) теперь разложился бы так:

```text
TOOL_OVERUSE       # 11 tool_calls, 9 db_get
LOST_TOTAL         # total=40, сказал «много»
FALSE_UNCERTAINTY  # «скорее всего тормозные колодки»
verdict = PARTIAL
```

Новые метрики в отчёте: `verdicts` (доли), `error_classes` (histogram),
`p50/p95` по tokens/duration/cost/tool_calls, `avg_repeated_tool_calls`/
`avg_unique_tool_calls`/`avg_db_get` (fanout).

## Стоимость

- polza/deepseek-v4-flash: **≈ $0.18-0.19/кейс** (25-38k токенов, 8-26s).
- Полный прогон 49 кейсов ≈ **$8-9** — реалистично для CI/smoke.
- local ollama: $0, но слабее/медленнее.

## Связанные документы

- [README.md](README.md) — дизайн бенча (для ревью)
- plan-for-review (архив, удалён) — план замещён docs restructure
- incident-camry (архив, удалён) — кейс, мотивировавший детерминизм
- [agent_db/bench/README.md](../../services/agent-db/agent_db/bench/README.md) — код, метрики, тесты



---
**Last verified:** 2026-08-24 (working tree following `0add4ea`) — artifact cleanup: removed revision section, Last verified, simplified case descriptions.
