# Инцидент: ответ агента про Camry — расследование

**Файл:** `backlog/agent_autoparts_55618253-a2cd-4596-b710-c08aba45214c.jsonl`
**Вопрос:** «привет у вас есть запчасти для машины камри?»
**Дата:** 2026-08-03T22:25:16Z
**Модель:** `openai/deepseek-v4-flash`

## Полный трейс

| # | Событие | Детали |
|---|---|---|
| 0 | `turn_start` | user_message |
| 1 | `llm_call` | 5372 prompt / 116 completion, 3.49s, $0.0546 |
| 2 | `tool_call` | `db_map({})` |
| 3 | `tool_result` | схема: catalog_product, поля... |
| 4 | `llm_call` | 8180 prompt / 245 completion, 3.7s, $0.0446 |
| 5 | `tool_call` | `db_search({"entity":"catalog_product","pattern":"Camry","limit":20})` |
| 6 | `tool_result` | **`total: 40`**, preview 20 (id/артикул) |
| 7 | `llm_call` | 8674 prompt / 266 completion, 4.1s, $0.0578 |
| 8–25 | `tool_call`/`tool_result` | **9× `db_get`** (789, 411, 423, 435, 447, 459, 471, 477, 483) |
| 27 | `turn_end` | final, 22.19s, 4 LLM, 11 tools, 34k токенов, $0.206 |

## Что видел агент в контексте (точно)

- `db_search` вернул: `{"preview":[20 артикулов с id и name-артикулом], "returned":20, "total":40}`.
  Через `ToolExecutionStage` (`llm_content = tool_result.tool_content`) попало в `ctx.turn.messages` **без обрезки** (обрезка до 10k только для backlog-записи). `LLMStage` шлёт `messages=ctx.turn.messages` целиком.
- Каждый `db_get` вернул полный объект: `article, name, price, quantity, brand_id, car_applicability, characteristics, country_of_origin, description, is_available...`.
  Для BRK-01004 (id 411): `name="Колодки тормозные передние для Toyota Camry V40"`, `car_applicability=["Toyota Camry V40", "Toyota Camry V40 (до 2010)", "Toyota Camry V40 (рестайлинг)"]`.

## Что в ответе агента (final)

> Найдены: EXT-01382 (Лямбда-зонд NGK, 3839₽, 3 шт), BRK-01004 (2200₽), DISK-01064, FLT-01112.
> «и ещё много других позиций...»
> «Какая модель Camry (например, V40, V50, V70)?»
> «BRK-01004 (скорее всего тормозные колодки)»

## Верификация по БД

| Проверка | Результат |
|---|---|
| V50/V70 существуют? | ✅ Да: `count(*) WHERE car_applicability ILIKE '%v50%' = 8`, V70 тоже |
| `total: 40` в backlog? | ✅ Да, в `tool_result` db_search |
| BRK-01004 — колодки? | ✅ Да: `name="Колодки тормозные передние для Toyota Camry V40"` |
| 40 товаров для Camry? | ✅ Да: `count(*) WHERE car_applicability ILIKE '%camry%' = 40` |

## Вердикт

**Агент НЕ галлюцинировал.** V50/V70 реальны, цены/артикулы верны, категории верны.

**Реальные дефекты:**
1. **Перебор тулов:** 11 tool_calls (9× db_get) на простой вопрос → 22s, 34k токенов, $0.21.
2. **Упущенный факт:** знал `total: 40` (в контексте), сказал «много позиций».
3. **Ложная неуверенность:** «скорее всего тормозные колодки» при точном `name` в контексте.

## Вывод для бенча

Все три дефекта **детерминируются** (tool_calls count, regex по артикулам, `total:N` vs ответ, маркеры «скорее всего»). LLM-судья для этого не нужен. Судья — только для открытой семантики (v2).
