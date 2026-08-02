# Аудит data-service после 4-дневного рефакторинга (2026-08-01)

Рой из 4 reviewer-субагентов (fresh context) + верификация вручную. База: `go build` ✓, `go vet` ✓, `go test ./...` 706 passed ✓, `go test -race ./...` 706 passed ✓. Ревьюеры прочитали все изменённые файлы, прогнали тесты и эмпирические проверки (SQLite v1.37.1, PG-логика статически).

**Легенда:** ✅ подтверждено как баг · ⚠️ подтверждено как некорректно (дизайн) · ℹ️ не подтверждено / ок

---

## 🔴 CRITICAL

### C1 — Deadlock: вложенный RLock в `ServeHTTP` (tenant.go:289 → endpoint_builder.go:87 / swagger.go:56) ✅

`ServeHTTP` держит `ts.mu.RLock` на **весь** запрос (tenant.go:289-300), чтобы защитить `inst.Router` от ReloadTenant. Но tenant-роутер внутри вызывает `ts.resolveTenant(r)` для `/mcp/schema` (endpoint_builder.go:87) и `/openapi.json` (swagger.go:56) — это **повторный `RLock` из-под уже удерживаемого RLock**.

Go RWMutex (1.26): если writer (`ReloadTenant`/`RemoveTenant`/`AddTenant`) встал в очередь между внешним и внутренним RLock, новые читатели **блокируются**, пока writer не получит и не отпустит Lock. Writer ждёт внешний RLock (его держит запрос) → запрос ждёт внутренний RLock (блокирован writer'ом) → **классический deadlock**.

- Триггер: любой запрос `/mcp/schema` или `/openapi.json` конкурентно с `POST /admin/tenants/{id}/config/rewrite`, `DELETE /admin/tenants/{id}`, hot-reload.
- Это реальный production-путь: mcp-gateway проксирует `/mcp/schema` (httpclient.go:270).
- Существующие тесты (`TestServeHTTP_HoldsReadLockDuringRequest`) проверяют только «writer ждёт», но **не** вложенный read-путь.

**Фикс:** в `ServeHTTP` резолвить tenant БЕЗ лока (или через уже-резолвнутый instance), а RLock брать только для проверки `removing` — либо передавать `inst` вниз в хендлеры `/mcp/schema` и `/openapi.json`, чтобы они не звали `resolveTenant` повторно.

---

## 🟠 HIGH

### C2 — PG: tenant-плейсхолдер всегда `$1`, коллизия с WHERE-аргументами ✅

`strategy_handler.go:56`, `distinct.go:65`, `schema_handler.go:33`, `stats.go:36` вызывают `tenantFilter(..., 0, translate)` — `existingArgCount` захардкожен в `0`.

`tenantFilter` (row_filter.go:26) делает `translate(existingArgCount+1)`. На **PostgreSQL** grep/filter генерируют `$1..$n` через `search.Adapter`; tenant-клауза получает `$1` → коллизия:

```sql
WHERE "category" = $1 AND "tenant_id" = $1 LIMIT $2 OFFSET $3
args: [cat, tenantID, limit, offset]
```

→ `tenant_id` сравнивается с `cat`, LIMIT получает tenantID и т.д. На SQLite (`?`) коллизии нет — поэтому тесты зелёные и маскируют баг.

**Эталон правильный:** `count.go:115` и `get_by_id.go:35` передают `len(args)`.

**Фикс:** передавать реальное число уже сгенерированных args в `tenantFilter` во всех 4 местах (и в count-ветке strategy_handler.go:133-134 тоже).

### C3 — strategy_handler: `format=count` + tenant-фильтр → 500 ✅

`strategy_handler.go:62-72`: для `plan.Format == FormatCount` `sqlStr` — это `SELECT COUNT(*) FROM t WHERE ...` (агрегат). Обёртка:

```sql
SELECT COUNT(*) FROM (SELECT COUNT(*) FROM t WHERE ...) AS _cnt WHERE "tenant_id" = ?
```

→ `no such column: tenant_id` (в агрегате колонки нет) → 500 на любой grep/filter с `format=count` при включённых row_filters. Плюс на PG — коллизия `$1` из C2.

**Фикс:** не оборачивать агрегат; применять tenant-условие через AND к внутреннему WHERE (как в count.go), или добавлять tenant_id в проекцию подзапроса (ensureColumn), как в RawWhere-ветке.

### C4 — insertTenantBeforeLimit: tenant-клауза ПОСЛЕ ORDER BY → SQL-ошибка ✅

`strategy_handler.go:235-272`: вставка перед `LIMIT`, т.е. **после** `ORDER BY`. При `sort_by`:

```sql
WHERE "category" = ? ORDER BY "created_at" DESC AND "tenant_id" = ? LIMIT ? OFFSET ?
```

Эмпирически на SQLite: `SQL logic error: near "AND"`. Тест-харнесс ревьюера: `no-orderby` → OK (rows=2), `with-orderby` → ERROR. На PG — та же синтаксическая ошибка.

**Фикс:** вставлять перед первым же `ORDER BY`/`LIMIT`/`OFFSET` (минимальный индекс), а не только перед `LIMIT`.

### C5 — migration: legacy `list` → `strategy:"filter"` = постоянный 400 ✅

`helperium-go/config/migration.go:42-47` конвертит `op="list"` → `op="strategy", strategy="filter"`. Но `FilterStrategy.ParseRequest` (filter.go:303-305) возвращает ошибку **"at least one filter parameter is required"** при нулевых условиях.

Старый `list` (list.go, удалён в 1ea1a03) возвращал **все записи без фильтров** («Returns all %s. Use filters to narrow results»). Мигрированный `GET /products` (без параметров) теперь → **400** вместо списка. Любой v2-конфиг с `list` молча ломается.

**Фикс:** либо оставить list-all (fallback «no filters → вернуть всё» в filter-стратегии для мигрированных эндпоинтов), либо мигрировать `list` на что-то толерантное к пустым параметрам, либо не мигрировать, а валидировать + требовать rewrite.

### C6 — stats.go: tenant-фильтр на несуществующую колонку роняет весь `/stats` ✅

`stats.go:31-58`: `tenantFilter` возвращает WHERE-фрагмент из `auth.RowFilters` **без проверки существования колонки**. При `RowFilter.Where = "tenant_id = :tenant_id"` и таблице без колонки `tenant_id` → `no such column` → `RespondError(500)` — **весь /stats падает** (включая здоровые counter'ы), ошибка не логируется.

`Config.Validate()` (types.go:844-848) проверяет только `counter.Filter`, а `RowFilters.Where` вообще не валидируется.

**Фикс:** (1) валидировать `RowFilter.Where` в `Config.Validate`; (2) в stats.go не ронять весь эндпоинт — логировать ошибку и пропускать проблемный counter.

---

## 🟡 MEDIUM

### M1 — grep invert нарушает законы Де Моргана (multi-token / multi-field) ✅

`grep.go:181-247`: non-invert строит `(f1 LIKE t1 AND f1 LIKE t2) OR (f2 LIKE t1 AND f2 LIKE t2)`. Invert заменяет только оператор, не инвертируя AND/OR:

- LIKE: `(f1 NOT LIKE t1 AND f1 NOT LIKE t2) OR (...)` — корректно `(f1 NOT LIKE t1 OR f1 NOT LIKE t2) AND (...)`.
- Regex: `(f1 !~ p OR f2 !~ p)` — корректно `(f1 !~ p AND f2 !~ p)`.

Корректен только single-token + single-field. Тест `TestGrepStrategy_Invert` использует один токен → баг не покрыт.

**Фикс:** при invert менять AND↔OR и применять NOT к каждому листу, либо строить `NOT ( <исходное> )`.

### M2 — BuildFilter (runtime): escape есть, ESCAPE-клаузы нет (SQLite) ✅

`runtime/query_builder.go:177-184`: `escapeReplacer` экранирует `%`→`\%`, но SQL генерится **без `ESCAPE '\'`**. В SQLite `\` — литерал → `%` остаётся wildcard'ом: значение `100%_off` находит «всё, что содержит 100», точные данные с `%` не находятся, DoS-защита неэффективна. Достижимо: `CountHandler` → `BuildFilter` с `op="like"`. На PG дефолтный escape — backslash, поэтому работает — расхождение бэкендов.

**Фикс:** добавить ` ESCAPE '\'` (как в BuildFind:110).

### M3 — filterable/searchable FieldRules не enforced в runtime ✅

- `hasSearchableFields` (columns.go:14-40) исключает блокированные поля при **генерации** grep-эндпоинта, но `stringFields()` в grep.go (runtime, дефолтный набор + `fields` param) **не применяет** searchable-правила → grep ищет по image/seo полям, которые админ заблокировал.
- `FilterStrategy.ToolParams` использует `IsFilterableField`, но `ParseRequest` (filter.go:150-205) принимает фильтр по **любому** полю, кроме PK/tenant_id/ExcludeFromSearch — `FilterableRules` (в т.ч. `block_names` для PII) **не enforced** на runtime. Ложное чувство безопасности.

**Фикс:** применить resolved rules в `stringFields()` и в `ParseRequest`'s field resolution.

### M4 — BuildCustomQuery: JSONB-операторы `?`, `?|`, `?&` вне кавычек ✅

`runtime/query_builder.go:302-347`: парсер отслеживает только `'...'`. Для PG `data ? 'key'` (вне кавычек оператор ` ? `) → `data $1 'key'` → битый SQL. **Не регресс** (доказано git-историей: старый код заменял все `?`, включая литералы — новый парсер строго лучше, чинит `'what?'`). Отказ громкий (500 / placeholder mismatch), SQL пишет админ — не атака. Плюс комментарии `--`/`/* */` и `$$...$$` с `?` тоже заменяются.

**Фикс:** трекать JSONB-операторы / комментарии / dollar-строки, либо документировать запрет.

### M5 — sqlite: Exec-fallback перебивает явные `_pragma` на conn2 ✅

`sqlite_adapter.go:73-107`: если DSN содержит `_pragma=` (напр. `foreign_keys(0)`), `ensurePragmaParams` не трогает DSN, но `Connect` безусловно выполняет Exec-fallback (`PRAGMA foreign_keys=ON` и др.) на первом коннекте. Эмпирически (v1.37.1): conn1 → `foreign_keys=1` (перебил явный DSN), conn2 → `foreign_keys=0` (прагмы не применились) → **разное поведение FK/busy_timeout внутри одного пула** (SetMaxOpenConns(2)).

**Фикс:** если DSN уже имеет `_pragma=`, Exec-fallback либо пропускать, либо выполнять только для прагм, отсутствующих в DSN. `synchronous=NORMAL` также не дублируется в DSN-параметрах.

### M6 — normalizeDateTime: миллисекунды/таймзона не парсятся ✅

`response_mapper.go:79-98`: парс-форматы ровно 4 (RFC3339, `"2006-01-02 15:04:05"`, `"2006-01-02T15:04:05"`, `"2006-01-02"`). `"2024-01-02 15:04:05.123"` (sqlite `strftime('%Y-%m-%d %H:%M:%f')`) → **ok=false** → возвращается as-is, не RFC3339. То же для `"…05+00:00"`.

**Фикс:** добавить layout'ы с fractional seconds и опциональной таймзоной.

### M7 — disabled_default_*_rules: Reason-prefix matching, нет стабильного ID ✅

`configgen.go:121-141` (`resolveFieldRules`): отключение дефолтных правил матчится `strings.HasPrefix(rule.Reason, prefix)` — по **человекочитаемой строке** ("Common filterable", "Image/SEO", "Columns that typically"). Проблемы:
1. Правка Reason-текста (i18n, перефразировка) тихо ломает отключение → дефолт вернётся.
2. `Generate` персистит resolved-список в `result.FilterableRules`; `ExtractIntent` round-trip'ит его; следующий `Hydrate` передаёт его как `custom` → при drift'е отключённое правило добавляется **поверх самого себя**: rewrite 1 → default×2, rewrite 2 → default×3 (неограниченный рост).
3. Нет валидации, что disabled-префикс вообще матчит известное правило (typo тихо сохраняется).

**Фикс:** стабильный `ID` в FieldRule + точный match + валидация; Reason — только описательный.

---

## 🟢 LOW (не блокеры, зафиксировано)

- **L1** — `parseOffset` без cap (strategy_common.go:89-98, readPagination pagination.go:20-38): offset=9e18 проходит; на PG большие таблицы → полный скан/sort. Cap 100k разумен. Плюс doc-рассинхрон: filter ToolDescription обещает limit «1-1000», реальный cap 100.
- **L2** — `ensureColumn`: tenant_id попадает в ответ (strategy_handler.go:104,274-278) — своя колонка, утечки нет, но ответ загрязняется.
- **L3** — `SaveTenantSchema` не-атомарный (tenant.go:186-190) — крэш mid-write = битый cache; `PersistTenantConfig` имеет fallback, приемлемо.
- **L4** — HealthCheck: ping может пойти на закрытый RemoveTenant'ом Conn → транзиентный "unhealthy" (tenant_health.go:55,72-88). Приемлемо.
- **L5** — `QuoteIdentifier` (PG): `split(".")` + удвоение — имена из конфига с точкой/кавычками квотируются дважды. Для интроспекции (schema.table) — ок. Контракт надо задокументировать.
- **L6** — `ReadOnlyDB` (readonly.go:14-36) — мёртвый код (runtime не использует).
- **L7** — DSN с `?` в имени файла — ограничение modernc, не документировано.
- **L8** — `PersistTenantConfig` (tenant.go:225-238) — имя провоцирует misuse (регенерит из intent); сейчас только rewrite, но лучше переименовать.
- **L9** — OpenAPI: query-параметры grep/filter не в спеки (openapigen.go:575-577).
- **L10** — filter `__like` RawValue + ESCAPE: пользовательский `\` меняет семантику (документировать).

## ℹ️ Подтверждено КОРРЕКТНЫМ (не баг)

- Not-инверсии в renderCondition (OpNeq+Not→`=`, LIKE→NOT LIKE, regex→`!~`/NOT REGEXP) — верны; `OpNeq+Not` мёртвый путь, но математически корректен.
- ESCAPE-клауза консистентна во всех остальных LIKE-путях (query/builder.go:208-277, grep.go, BuildFind) + QuoteString экранирует `%`/`_`/`\`.
- countQueryWithArgs: для текущих вызовов (один внешний LIMIT) корректен; квотированные колонки `"limit"` не матчат `" LIMIT "`. Хрупкость — только к будущим подзапросам.
- get_by_id — prepared statement, инъекции нет; tenant-фильтр стыкуется через `len(args)`.
- data-хендлеры не имеют доступа к write-Conn (ReadOnlyConn гарантирует); write-пути только в admin (отдельный conn).
- RawWhere-tenant-wrap + ensureColumn работают по замыслу (проверено tenant_count_regression_test).
- CustomShortNames round-trip идемпотентен; displayName не пустой для grep/filter/schema.
- schemaMu инициализируется во всех production-путях (buildTenantInstance + RegisterTenantInstance backfill). Риск только в тестах с прямым `TenantInstance{}`.
- RemoveTenant двухфазный drain корректен; гонка bounded через `removing`.
- Header > query в tenantIDFromRequest — детерминировано, не конфликт.
- Hydrate round-trip: explicit CustomQueries (с SQL-сравнением коллизий) и Stats-фильтрация корректны (кроме F5 — потеря counters).

---

## Рекомендуемый порядок фиксов (TDD)

| Приоритет | Баг | Файлы |
|---|---|---|
| P0 | C1 deadlock | tenant.go, endpoint_builder.go, swagger.go |
| P0 | C2 PG placeholder offset | strategy_handler.go, distinct.go, schema_handler.go, stats.go (+ tests) |
| P0 | C3 format=count + tenant | strategy_handler.go |
| P0 | C4 tenant после ORDER BY | strategy_handler.go (insertTenantBeforeLimit) |
| P0 | C5 list→filter 400 | migration.go, filter.go |
| P0 | C6 stats 500 | stats.go, Config.Validate |
| P1 | M1 grep invert (Де Морган) | grep.go |
| P1 | M2 BuildFilter ESCAPE | runtime/query_builder.go |
| P1 | M3 rules не enforced | grep.go stringFields, filter.go ParseRequest |
| P1 | M4 JSONB `?` | runtime/query_builder.go |
| P1 | M5 sqlite прагмы | sqlite_adapter.go |
| P1 | M6 normalizeDateTime | response_mapper.go |
| P1 | M7 FieldRule ID | helperium-go/config, configgen.go |
| P2 | L1-L10 | по остатку |

Все фиксы — TDD: сначала падающий тест (для C1 — тест на вложенный RLock; для C2-C4 — PG-адаптер в тесте), потом код.

---

## ✅ Статус на 2026-08-01 (все фиксы реализованы)

| Баг | Severity | Статус | Тесты |
|---|---|---|---|
| C1 deadlock | CRITICAL | ✅ | 2 (tenant_deadlock_test.go) |
| C2 PG placeholder offset | HIGH | ✅ | 1 (tenant_placeholder_regression_test.go) |
| C3 format=count + tenant | HIGH | ✅ | 1 (тот же файл) |
| C4 tenant после ORDER BY | HIGH | ✅ | 1 (тот же файл) |
| C5 legacy list/find → v4 | HIGH | ✅ | 2 (migration_test.go) |
| C6 /stats 500 | HIGH | ✅ | 3 (stats_test.go, filter_validation_test.go) |
| M1 grep invert (Де Морган) | MEDIUM | ✅ | 3 (grep_test.go) |
| M2 BuildFilter ESCAPE | MEDIUM | ✅ | 1 (query_builder_test.go) |
| M3 rules в runtime | MEDIUM | ✅ | 3 (grep_test.go, filter_test.go) |
| M4 JSONB/комментарии/dollar | MEDIUM | ✅ | 3 (query_builder_test.go) |
| M5 sqlite прагмы | MEDIUM | ✅ | 1 (pragma_test.go) |
| M6 normalizeDateTime | MEDIUM | ✅ | 1 (response_mapper_internal_test.go) |
| M7 FieldRule.ID + миграция | MEDIUM | ✅ | 2 (configgen_test.go) |

Итого: +28 тестов, `go test ./...` 734 passed (data-service) + 114 (helperium-go), `-race` чистый.
Ключевые решения: C1 — inst в контексте (tenantInstanceKey) + fallback; C2-C4 — tenant-плейсхолдер
нумеруется по позиции, PG-хвост перенумеровывается; C5 — версия конфига v4, legacy удалён;
M7 — стабильный ID + миграция Reason→ID в normalizeV3ToV4.

### LOW-фиксы (2026-08-01, воркеры с toolBudget-block на read)
| Баг | Статус |
|---|---|
| L1 offset cap (100k) | ✅ strategy_common.go + pagination.go |
| L2 tenant_id в ответе | ✅ outerCols без tenant_id (RawWhere) |
| L3 SaveTenantSchema атомарность | ✅ temp+rename |
| L5 QuoteIdentifier контракт | ✅ документация |
| L6 ReadOnlyDB мёртвый | ✅ Deprecated-пометка |
| L7 DSN с ? | ✅ документация |
| L10 filter __like \ + ESCAPE | ✅ задокументирован (filter.go, search-strategies.md) |
| L4 HealthCheck closed conn | ⏳ приемлемо (транзиентный unhealthy) |
| L8 PersistTenantConfig → RegenerateAndPersistTenantConfig | ✅ |
| L9 OpenAPI query-params (grep/filter/distinct) | ✅ |

Итого: 748 (data-service) + 114 (helperium-go) = 862 теста, `-race` чистый.
Осталось на потом: L4 (HealthCheck closed conn — приемлемо). L10 (filter __like \ + ESCAPE) — задокументирован в filter.go + search-strategies.md.


---

## Финальный review-рой (2026-08-01, после всех фиксов)

4 reviewer'а (configgen упал по таймауту ×2, перепроверен вручную) по зонам diff.

### Новые баги, найденные review-роем (исправлены TDD)

| # | Severity | Баг | Фикс |
|---|---|---|---|
| R1 | CRITICAL | grep `regex=true` на SQLite падает: modernc не регистрирует `regexp()` → "no such function: REGEXP". Тесты проверяли только SQL-структуру, не живой прогон | `registerSQLiteRegexp()` в sqlite_adapter.go (RegisterScalarFunction + sync.Once) + `TestConnect_RegexpFunction` |
| R2 | CRITICAL | `mode=ro`/`immutable=1` DSN ломается: ensurePragmaParams добавляет `journal_mode(WAL)` (write) → "attempt to write a readonly database". readonly_dsn тенанты не стартуют | readOnlyDSNRe → только busy_timeout для read-only DSN + `TestConnect_ReadOnlyDSN_NoWAL` |
| R3 | HIGH | OpenAPI filter рекламирует `sort_dir` (не существует — только sort_by с `-`), filter без `format` | убрать sort_dir, добавить format, sort_by doc с `-` |
| R4 | MEDIUM | admin-хендлеры пишут inst.Config/Router/ApprovedTools БЕЗ ts.mu — гонка с ReloadTenant (data race) | approve-tool persistFn: снапшот под RLock, публикация под Lock; ConfigPath записи под Lock (механика удалена позже — write-tools выпилены) |
| R5 | MEDIUM | ReloadTenant игнорирует изменение DSN (валидирует dry-run, но не переподключается) | ReloadTenant: если DSN/Driver изменился → buildTenantInstance + подмена inst + close старых conn + `TestReloadTenant_DSNChanged_Reconnects` |
| R6 | MEDIUM | M7 дрейф: resolveFieldRules НЕ дедуплицировал custom-правила с ID дефолтов → rewrite дублировал defaults (default×2, ×3) | dedup custom по ID против defaults + `TestResolveFieldRules_CustomDuplicateOfDefault_NoDrift` |
| R7 | LOW | L10 контракт `\`: grep auto-escapes, filter raw — расхождение не задокументировано | cross-reference в adapter.go QuoteString |
| R8 | LOW | distinct column doc не упоминает public name | doc в queryParams |
| R9 | LOW | grep invert семантика не объяснена LLM | doc-note в GrepStrategy |

### Проверено и признано OK
- C1 (context tenantInstanceKey, fallback resolveTenant) — TOCTOU закрыт, двойного лока нет
- L3 (temp+rename) — cleanup на ошибках полный; tmp-файлы при SIGKILL инертны (restore skip)
- L4 (health 2s window) — bounded, "unhealthy" не паника
- C6 (stats fail-soft) — битый counter никогда не 500
- L8 (переименование) — ноль старых вызовов

### Итог: 754 (data-service) + 114 (helperium-go) = 868 тестов, -race чистый.
