# Рой-аудит data-service по итогам бенча (2026-08-05)

> **Статус: ВСЕ фиксы применены (2026-08-06)** — TDD, e2e 124 passed,
> бенч **46/49 = 93.9%** (было 81.6%). Подробности: AGENTS.md §Verification.

Рой из 5 скаутов (fresh context) разбирал FAIL-кейсы бенча вглубь проекта.
Каждый скаут сначала читал доки (search-strategies, mcp-gateway, data-service README),
потом код data-service / mcp-gateway / configgen, и запускал тесты для проверки гипотез.
Отчёты скаутов: `.pi-subagents/artifacts/fb5143e6_reviewer_{0,1,3,4}_output.md`.

## Сводка вердиктов

| # | Тема | Вердикт | Кто виноват | Файл:строка |
|---|---|---|---|---|
| 1 | entity 'Brand'/'Product' → 404 | **Контрактная дыра**: db_map показывает display-имена первыми («Brand (catalog_brand)»), canonical ToolPrefix скрыт `json:"-"`; /q/* резолвит только по точному `catalog_*`. Система говорит «Entity name (from db_map)» и отвергает то, что показывает. | data-service + configgen | `configgen/llm.go:175-176,304`, `endpoint_builder.go:170-173`, `runtime/entity_resolver.go:38-41` |
| 2 | preview показывает артикул вместо названия | **Неосознанный компромисс**: `FirstStringFieldColumn()` берёт первую строковую колонку по порядку схемы; у Django article стоит раньше name. Плюс `selectClause` compact дублирует логику БЕЗ skip строкового PK → `[id,id]` для string-id сущностей (скрытый баг). | data-service (низкий риск фикса) | `helperium-go/config/types.go:338-346`, `search/strategy_common.go:202-209`, `query/format.go:65-76` |
| 3 | `category_id__in:"255,341"` → 400 | **Баг mcp-gateway**: `ArrayOf` мёртвое поле — не сериализуется в JSON Schema и не валидируется; gateway валидирует `__in` как скаляр (ParamTypeInt), модель шлёт CSV-строку по описанию «Comma-separated values» → 400. data-service сам отлично парсит CSV. | mcp-gateway | `mcp-gateway/internal/tools/tools.go:575-594`, `data-service/internal/search/filter.go:143-147,270-295` |
| 4 | db_describe total / db_map 9KB | `db_describe` total = честный COUNT(*) (не distinct). Запрет в кейсе — осознанный контракт «count через data-tools». **db_map 9KB против токен-бюджета 8K** — схема уже авто-инжектится в system prompt, db_map избыточен; реальный лимит — max_turn_tokens=8000. | бенч (кейс) + data-service (db_map размер) | `schema_handler.go:57-75`, `configgen/llm.go:83-320`, `api-service/.../middlewares.py:70-102`, `helperium-sdk/settings.py:106-108` |
| 5 | данные seed=42 (country 70% Германия, oem/supplier пустые) | Не «весь seed кривой»: origin противоречит стране бренда у 36/407 (жёстко зашитые страны в seed_data.py:282 колодки, :414 свечи); oem_number НЕ заполняется вообще; supplier только у колодок; «Масляные фильтры=0» — коллизия имён в dict categories (дубликаты перезаписывают). Реализм бенча не искажён (кейсы не зависят от oem/supplier). | foreign seed (не трогаем) → чинить seed_fixture.py | `demo/autoparts-store/catalog/management/commands/seed_data.py:282,314,382,414` |

## Ключевые находки (по каждому)

### 1. Entity names (критично для бенча — 4 из 9 FAIL)
- `db_map` name = `"Brand (catalog_brand)"` — display первым, canonical в скобках (`llm.go:175-176`).
- `LLMEntity.ToolPrefix` (canonical) имеет `json:"-"` — **модель НИКОГДА не видит canonical name как значение**, только display.
- FK-связи в db_map тоже используют display (`ReferencedEntity: "Cart"`).
- `/q/*` — точный whitelist lookup (`entityMap[name]`), нет aliases, нет case-fold.
- **177 вхождений unknown_entity в bench-логах** — систематика.
- Фикс: (а) server-side принимать display-имена (resolve через displayPrefixes/CustomShortNames), (б) показывать canonical первым, (в) примеры в описаниях db_*.

### 2. Preview = артикул
- `FirstStringFieldColumn()` = первая не-PK строковая колонка по схеме. Django: `article` перед `name` (миграция 0001).
- НО глубже: compact preview = только `id` + 1 строка. **Цены/наличие в preview нет вообще** — модель ОБЯЗАНА звать db_get для «сколько стоит».
- Скрытый баг: `selectClause` compact не skip'ает строковый PK → `[id,id]` (студент/группа в testseed).
- `format=full` существует в data-service, но НЕ в JSON Schema тула — модель не знает.
- Фикс: name-preference в `FirstStringFieldColumn` + единый helper для selectClause. Все тесты проходят (проверено скаутом-1).

### 3. `__in` CSV → 400
- filter.go: `__in` = `Type: pt, ArrayOf: pt`, desc «Comma-separated values».
- mcp-gateway `validateArgs`: только скалярные ParamTypeInt/Float/String/Bool — **ArrayOf игнорируется**, CSV-строка для числового `__in` режется «expected numeric type, got string».
- data-service `ParseRequest` сам сплитит CSV (`strings.Split(val, ",")`) — готов принять.
- Тесты gateway на ArrayOf/CSV: **нет**.
- Фикс: (а) в validateArgs принимать строку и сплитить для ArrayOf, или (б) JSON Schema `type: array, items` + gateway валидировать массив; или (в) сделать `__in` ParamTypeString (самое простое — data-service сам сплитит).

### 4. db_describe / db_map
- `db_describe total` = `SELECT COUNT(*)` (tenant-filtered) — реальный count, безопасен.
- Запрет db_describe в brand-count-001 — осознанный (discovery ≠ counting), НЕ перестраховка.
- db_map 9KB: схема уже в system prompt (`GET /mcp/schema` → tool_discovery.py инжектит), db_map — избыточная копия; реальный лимит 8K токенов на turn — db_map его съедает.
- Фикс: сжать db_map до 2-3KB (имя+фильтруемые поля+relations+1 hint), убрать per-field прозу/FK-прозу.

### 5. Данные seed=42
- origin ≠ brand.country: 36/407 (колодки TRW/Brembo «Германия» — хардкод seed_data.py:282; свечи Bosch «Япония» — :414). Диски/масла/extra — правильно из brand.country.
- oem_number: **0 присваиваний в seed** (ни seed_data, ни seed_massive) — все пустые по построению.
- supplier: только колодки (`{brand} GmbH`, 60 шт), остальные 347 пустые.
- «Масляные фильтры=0»: dict categories перезаписывает дубликаты имён («Фильтры» побеждает «Двигатель»), товары только в ветке «Фильтры».
- Реализм: кейсы не зависят от oem/supplier (переписаны в ревизии), origin-кейсы используют Bosch/Febi (Германия — останется).
- Фикс (helperium-owned seed_fixture.py): выровнять origin по brand.country, заполнить supplier/oem_number детерминированно (seeded random), assert на дубликаты категорий.

## Приоритет фиксов

| Приоритет | Фикс | Эффект на бенч |
|---|---|---|
| **HIGH** | entity display-name resolution в /q/* (или canonical first в db_map) | убирает ~4 FAIL (entity 404) |
| **HIGH** | `__in` ArrayOf: принимать CSV в validateArgs | убирает 400 на category_id__in |
| **MED** | name-preference preview + единый helper | модель видит названия, не артикулы |
| **MED** | сжать db_map до 2-3KB | модель не теряет turn на манифест |
| **LOW** | seed_fixture.py: origin/supplier/oem + assert | данные реалистичнее, новые кейсы |

## Метод

- 5 скаутов fresh context, каждый: доки → код → запуск `go test`/pytest для проверки гипотез.
- Скаут-2 (__in) падал 2× из-за инфраструктуры («Сервис временно недоступен»); разобран вручную оркестратором.
- Все вердикты подтверждены кодом (цитаты выше) и тестами (не ломают существующие).
