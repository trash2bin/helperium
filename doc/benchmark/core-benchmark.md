# Core Benchmark — операционная документация

> Реализация дизайна из [doc/benchmark/README.md](README.md). Живой код — в
> `agent-db/agent_db/bench/` (см. его [README](../../agent-db/agent_db/bench/README.md)).

## Что это

Детерминированный бенчмарк агента Helperium **без LLM-судьи** для core-метрик:
retrieval, answer delivery, галлюцинации, отказ, стоимость/скорость, чтение схемы,
устойчивость к ошибкам модели.

**Статус:** реализован и проверен end-to-end на живом стеке (2026-08-05):
- смоук без LLM (ScriptedLLMProvider) — 3 кейса, детерминированно
- реальные прогоны: polza/deepseek-v4-flash (3 кейса PASS, cost ≈ $0.18/кейс)
- полный прогон 49 кейсов на ollama/minimax-m3:cloud — упёрся в лимит модели (45 error)

## Быстрый старт

```bash
# 1. Стек
./scripts/dev.sh restart

# 2. Tenant autoparts (PG на :5434, demo/autoparts-store) + rewrite
curl -X POST http://127.0.0.1:8084/admin/tenants \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"id":"autoparts","config":{"data_source":{"driver":"postgres","dsn":"postgres://autoparts:autoparts_secret_2024@127.0.0.1:5434/autoparts","read_only":true}}}'
curl -X POST http://127.0.0.1:8084/admin/config/rewrite -H 'X-Tenant-ID: autoparts' \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 3. Агент (модель без лимита — polza/deepseek-v4-flash)
curl -X POST http://127.0.0.1:8081/api/agents -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"autoparts-assistant","tenant_ids":["autoparts"],
    "llm_config":{"model":"openai/deepseek-v4-flash","provider":"openai",
      "api_key":"<polza-key>","api_base":"https://polza.ai/api/v1",
      "system_prompt":"Ты — консультант автозапчастей. Отвечай на русском, используй инструменты. Не выдумывай цены и артикулы — бери из результатов инструментов."}}'

# 4. Прогон
uv run --package agent-db python -m agent_db.bench run \
    agent_db/bench/cases/autoparts.json \
    --agent-name autoparts-assistant --tenant-id autoparts \
    --api-url http://127.0.0.1:8081 --admin-token "$ADMIN_TOKEN" \
    --backlog-dir ./backlog --bench-log-dir ./bench-backlog \
    --output benchmark_report.json --delay 2.5
```

## Что мерить и как

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

## Что реально вскрыл бенч (2026-08-05)

1. **minimax-m3:cloud — сессионный лимит** → 45/49 кейсов `error` с
   `RateLimitError: you have reached your session usage limit`. Модель облачная,
   для прогонов нужна без лимита.
2. **deepseek-v4-flash игнорирует `total` в filter-результате** — на count-кейсе
   «Сколько Bosch» сделал 9× `db_get` (перебор) вместо того, чтобы поверить
   `total: 74`. 11 tool_calls, 26.5s, $0.19 вместо ~2 тулов.
3. **minimax шлёт мусорные аргументы** — `{"article=EXT-01392]<]minimax[":...}` →
   400. Агент корректно восстанавливается (recovery работает).
4. **`order_number` не фильтровался** — добавлен в `DefaultFilterableFieldRules`
   (продуктовый фикс): теперь `filter_catalog_order({order_number})` работает.
5. **Проценты-скидки** («~20%») — вычисляются моделью, изначально ловились как
   «галлюцинация» → исключены из проверки.

## Кейсы (49) по категориям

| Категория | Кол-во | Что проверяет |
|---|---|---|
| lookup | 19 | цена/бренд/страна/гарантия/наличие по артикулу, заказ по номеру (статус/доставка/оплата) |
| filter | 11 | список товаров по категории/цене/бренду/наличию/метке/скидке, комбинированный (колодки Bosch в наличии) |
| count/aggregation | 17 | количество по бренду/категории/цене/статусу/скидке |
| absence | 4 | несуществующий артикул/заказ/бренд → отказ |
| status | 5 | статус заказа с синонимами (shipped→«отправлен» и т.п.) |
| search | 1 | db_search по частичному имени (→ db_get) |

## Детерминированная база (seed=42)

Foreign `seed_data.py` не фиксирует random → данные меняются при каждом seed, ломая
ground truth. Решение — helperium-owned `demo/autoparts-store/seed_fixture.py`
(`random.seed(42)` + `Faker.seed(42)` перед `seed_data`):

```bash
cd demo/autoparts-store && DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell < seed_fixture.py
```

Даёт 30 брендов / 117 категорий / 407 товаров / 6 заказов — детерминированно.
**id меняются между прогонами** (sequence не сбрасывается), но артикулы/цены/статусы/
счётчики стабильны — кейсы используют их, не id.

## Ревизия кейсов (2026-08-05, независимое ревью)

Ревьюер с fresh context проверил кейсы против живого стека. Исправлено:
- **oem_number пуст у всех товаров** → `product-filter-oem-001` переписан в `product-search-partial-name-001` (db_search по имени), `product-lookup-oem-001` → `product-lookup-origin-001` (страна происхождения)
- **supplier пуст** → `product-filter-supplier-001` → `product-filter-combined-001` (колодки Bosch в наличии)
- **quantity EXT-01367 дрейф** (4→5) → исправлен
- **дубль** `order-lookup-number-001` (идентичен status-001) → удалён
- **добавлены**: скидочные (count/filter old_price>0 → 76), комбинированный фильтр, db_search по частичному имени

Ground truth сверен с data-service (источник правды): цены 3064/2122/3351, счётчики 74/145/73/60/36/24/32/20/407/30/117/6/3/76 — все совпадают.

## Логгирование

- **Общий backlog api-service** — `backlog/agent_{agent}_{session}.jsonl` (все трассы).
- **Отдельный bench-лог** — `bench-backlog/{session}.bench.jsonl`: question, полный
  final_text, все SSE-события (tool_call/tool_result), метрики. Изолирован от
  общего backlog (не путает).
- **Логи сервисов** — `.data/logs/{api,mcp,data}.log`.

## Стоимость

- polza/deepseek-v4-flash: **≈ $0.18-0.19/кейс** (25-38k токенов, 8-26s).
- Полный прогон 49 кейсов ≈ **$8-9** — реалистично для CI/smoke.
- local ollama: $0, но слабее/медленнее.

## Связанные документы

- [README.md](README.md) — дизайн бенча (для ревью)
- [plan-for-review.md](plan-for-review.md) — план и известные gaps логгирования
- [incident-camry.md](incident-camry.md) — кейс, мотивировавший детерминизм
- [agent_db/bench/README.md](../../agent-db/agent_db/bench/README.md) — код, метрики, тесты
