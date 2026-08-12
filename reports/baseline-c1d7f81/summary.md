# Baseline — autoparts (49 кейсов, реальный агент)

**Дата:** 2026-08-12
**Коммит evaluator:** рабочая ветка (после P0 + review-фиксы + triage-фиксы)
**Агент:** autoparts-assistant (polza/deepseek-v4-flash, temp=0)
**Тенант:** autoparts (детерминированная БД seed=42)
**Отчёт:** `report.json` (этот каталог)
**Raw backlog:** `backlog/` (49 файлов, JSONL)

---

## Результат (финальный baseline)

| Verdict | Кол-во | Доля |
|---|---|---|
| CORRECT | 39 | 80% |
| PARTIAL | 8 | 16% |
| WRONG | 2 | 4% |
| ERROR | 0 | 0% |

| Метрика | Значение |
|---|---|
| success_rate | 95.9% |
| retrieval_success_rate | 100% |
| answer_delivery_rate | 100% |
| hallucination_rate | 4.1% |
| refusal_correct_rate | 100% |
| entity_name_accuracy | 100% |
| tool_error_rate | 0% |
| p50 tokens | 30.8k |
| p50 duration | 23.3s |
| p50 cost | $0.149 |
| total cost (49 кейсов) | $7.60 |

---

## Что найдено в triage (этап C)

### Починенные false positives бенча (8 фиксов)

1. **Bool-matching**: JSON `is_available=true` + `is_bestseller=false` — глобальный поиск `false` ломал `available=true`. Фикс: матчинг по ключу `"is_available": true`.
2. **«Возможно»**: «Возможно, вы перепутали артикул» — вежливый оборот ≠ хеджирование. Убран из UNCERTAINTY_MARKERS.
3. **db_map в must_not_call**: discovery-тул легитимен на старте сессии. Убран из кейса (остался db_describe).
4. **LOST_TOTAL total=407 (db_map) вместо 74**: брал первый total. Фикс: total == expected.count.
5. **Табличные № строк (10..26) = галлюцинация**: регрессия — фикс был потерян. `_extract_row_numbers` (1..50).
6. **Отказ «Нет, ... в базе нет»**: маркер «базе нет» (покрывает «в нашей базе нет»).
7. **Breakdown-числа** (свечи 12, помпы 5...): сумма ≤ подтверждённый total (исключая сам total) → не галлюцинация.
8. **Метрики backlog (p50/p95) не агрегировались** — старые процессы api-service ломали запись backlog. Чистый рестарт.

### Подтверждённые дефекты агента (не бенча)

1. **Галлюцинация цен в таблицах**: агент заполнял таблицу «товары >5000» выдуманными ценами (6200, 7750...), которых не было в tool_results → WRONG. **Это реальный дефект, который бенч теперь ловит.**
2. **Ответ из знаний без БД** (brand-lookup: «Bosch основан в 1886») — иногда отвечает из памяти, не заземляя. В финальном baseline не проявился (CORRECT), но риск есть.
3. **LOST_TOTAL** (5 кейсов, PARTIAL): агент получает total:N, но иногда не называет его в ответе (говорит breakdown или «вот товары») — системная слабость.

### Инфраструктурные находки

- **Spending limit**: tenant autoparts имел budget $50/мес — был исчерпан после 2 прогонов (~$15). Поднят до $200. Для регулярных baseline нужен запас.
- **DSML-разметка deepseek-v4-flash**: периодически модель возвращает `<｜DSML｜tool_calls>` вместо OpenAI JSON tool_calls — api-service не парсит DSML. Проявлялось при исчерпанном бюджете/перегрузке; на чистом стеке не повторялось. **Open issue** для api-service.
- **5 зомби-процессов uvicorn**: от прошлых smoke-запусков, ломали запись backlog (Permission denied) и могли отвечать на :8081. Убиты, запущен чистый.

---

## Как воспроизвести

```bash
# 1. Стек: data-service :8084, mcp-gateway :8083, api-service :8081 (real LLM)
# 2. Агент autoparts-assistant (tenant autoparts, polza/deepseek-v4-flash, temp=0)
# 3. Прогон:
uv run --package agent-db python -m agent_db.bench run \
    services/agent-db/agent_db/bench/cases/autoparts.json \
    --agent-name autoparts-assistant --tenant-id autoparts \
    --api-url http://127.0.0.1:8081 --admin-token "$ADMIN_TOKEN" \
    --backlog-dir ./backlog --bench-log-dir ./reports/baseline/backlog \
    --output ./reports/baseline/report.json --timeout 90 --delay 2.5
```

Стоимость: ~$7.5-8 за 49 кейсов, ~25-30 мин.

---

## Известные ограничения этого baseline

- **Один прогон**: LLM вариативен (даже temp=0). 2 кейса из 49 (Свечи, price>5000) в другом прогоне могут быть CORRECT (проверено вручную). Для регрессий нужны 2-3 повтора или допуски.
- **breakdown-фикс**: если агент выдумал breakdown с суммой ≤ total, он не ловится (принятый компромисс — ложное срабатывание хуже).
- **Метрики p50/p95** — из backlog; если backlog не пишется (права/процессы), падают в 0.
