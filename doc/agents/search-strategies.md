# Search Strategies — data-service

Поисковый движок data-service: стратегии grep/filter/schema поверх Expression AST → SQL.

## Архитектура

```
LLM tool_call (MCP) → api-service → mcp-gateway → HTTP GET /{entity}/{strategy}
  → runtime/handlers/strategy_handler.go NewStrategyHandler
    → search.Strategy.ParseRequest() → query.QueryPlan
      → query.Engine.Build() → SQL+args
        → datasource.ReadOnlyConn → DB
```

Schema-стратегия идёт отдельным путём: `runtime/handlers/schema_handler.go` работает напрямую с БД, без Engine.

## Пакеты

```
internal/search/              стратегии
  strategy.go                 Strategy interface (:16), Adapter (:46), NewAdapter (:67)
  strategy_common.go          parseLimitParam (:101), parseOffset (:121), parseFormat (:137),
                              parseOrder (:153), selectClause (:191), tokenize (:76), parseBoolParam (:85)
  grep.go                     GrepStrategy (:22)
  filter.go                   FilterStrategy (:33)
  schema.go                   SchemaStrategy (:25)

internal/query/               Expression AST → SQL
  expression.go               QueryPlan (:10), Condition (:55), Operator (:75), OrderClause (:90)
  builder.go                  Engine.Build (:43), BuildCount (:50), RenderConditions (:124)
  format.go                   SearchResult (:4), FormatRows (:32)

internal/runtime/handlers/    HTTP-обработчики
  strategy_handler.go         NewStrategyHandler (:31), collectEmptyHint (:238), insertTenantBeforeLimit (:306)
  schema_handler.go           NewStrategySchemaHandler (:23), distinctValues (:103), fieldStats (:135)
  row_filter.go               tenantFilter (:22)
```

## Strategy interface (`search/strategy.go`)

```go
type Strategy interface {
    Name() string
    ToolName(entity config.Entity) string
    ToolDescription(entity config.Entity) string
    ToolParams(entity config.Entity) []config.EndpointParam
    ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error)
    EntityIDCol() string
    EntityNameCol() string
}
```

## QueryPlan и Condition (`query/expression.go`)

```go
type QueryPlan struct {
    Select    SelectClause     // колонки
    From      string           // квотированная таблица
    Where     []Condition      // AND-условия (filter)
    RawWhere  string           // сырое WHERE (grep multi-token AND)
    RawWhereArgs []any
    Order     []OrderClause
    Limit     int              // cap 100
    Offset    int
    Format    ResponseFormat   // compact | full | count
}

type Condition struct {
    Field    string
    Operator Operator
    Value    any
    Values   []any      // для IN / Between
    Not      bool
}
```

Операторы (`internal/query/expression.go`, рендер в `builder.go`):

| Operator | SQL (обычный) | SQL при `Not: true` |
|---|---|---|
| `OpEq` | `col = ?` | `col <> ?` (инверсия, а не невалидный `NOT =`) |
| `OpNeq` | `col != ?` | `col = ?` (двойное отрицание) |
| `OpLt` | `col < ?` | `col >= ?` |
| `OpGt` | `col > ?` | `col <= ?` |
| `OpLte` | `col <= ?` | `col > ?` |
| `OpGte` | `col >= ?` | `col < ?` |
| `OpLike` | `col LIKE ? ESCAPE '\\'` | `col NOT LIKE ? ESCAPE '\\'` |
| `OpILike` | `ILIKE` (PG) / `LIKE COLLATE NOCASE` (SQLite) | `NOT ILIKE` / `NOT LIKE` |
| `OpNotLike` | `col NOT LIKE ? ESCAPE '\\'` | — (сам по себе отрицание) |
| `OpRegex` | `REGEXP` (SQLite) / `~` (PG) | `NOT REGEXP` (SQLite) / `!~` (PG) |
| `OpIn` | `col IN (?, ?, ?)` | `col NOT IN (?, ?, ?)` |
| `OpBetween` | `col BETWEEN ? AND ?` | `col NOT BETWEEN ? AND ?` |

**ESCAPE `'\\'` обязателен** для всех LIKE-операторов: `QuoteString` экранирует `%`/`_` обратным слэшем, и без ESCAPE-клаузы экранирование не работает — в SQLite `\` трактуется как литерал, `%` остаётся wildcard'ом, данные с `%`/`_` в значении не находятся (и DoS-защита неэффективна). `RawValue: true` (filter `__like`) — значение без экранирования, wildcard'ы пользователя работают как есть.

Конструкторы: `Eq/Neq/Lt/Lte/Gt/Gte/Like/ILike/Regex/NotLike/In/Between`.

**RawWhere используется только в grep**: multi-token AND по полям собирается строкой `(col1 LIKE ? AND col1 LIKE ?) OR (col2 LIKE ? ...)`, т.к. это не выражается через []Condition. Filter — полностью Condition-based.

**Count для RawWhere+tenant строится из оригинального плана**: `SELECT COUNT(*) FROM t WHERE (RawWhere) AND tenant` — не через `countQueryWithArgs` от tenant-обёрнутого SQL. Inner-подзапрос для tenant-фильтра включает `tenant_id` в проекцию, иначе внешний `WHERE` не видит колонку и SQLite молча возвращает 0 строк. Регресс: `tenant_count_regression_test.go`.

## GrepStrategy (`search/grep.go`)

Multi-token AND внутри поля, OR между полями. Лимиты — см. сводно в разделе [Лимиты](#лимиты-сводно).

`ToolParams()` — `pattern` (required), `limit` (1-100, default 10), `fields`.
`ParseRequest()` — проверка длины `pattern` и `regex`.

HTTP-параметры (не все в JSON Schema): `pattern`, `ignore_case`, `fields`, `invert`, `regex`, `limit`, `offset`, `format`, `sort_by`.

Tenant isolation: `tenant_id` нельзя искать.

## FilterStrategy (`search/filter.go`)

Конструктор: `NewFilterStrategy(idCol, nameCol, filterableRules ...config.FieldRule)`.

Лимиты — см. сводно в разделе [Лимиты](#лимиты-сводно).

`ToolParams()` — поля через `config.IsFilterableField()` (см. FieldRules в configgen/README.md).

`ParseRequest()` :165. Операторы (HTTP-параметр `{field}__op`):

| Параметр | Поведение |
|---|---|
| `{field}` | exact (eq) |
| `{field}__neq` | not equal |
| `{field}__gt/__gte/__lt/__lte` | сравнения |
| `{field}__like` | LIKE с `%` (wildcard'ы пользователя; `\` — escape-символ: `50\%` = literal `50%`) |
| `{field}__in` | IN (comma-list, max 50) |
| `limit`/`offset`/`sort_by`/`format` | пагинация |

Проверки: длина значения, maxFilters, maxInValues. `tenant_id` недоступен для фильтрации.

## SchemaStrategy (`search/schema.go`)

- `ToolParams()` :48 — nil (без параметров).
- `ParseRequest()` :54 — nil (не использует Engine).
- `FieldInfo()` :58 — поля для schema-ответа.

Обработка: `schema_handler.go`. Ответ:

```json
{
  "entity": "auto_parts",
  "total": 35,
  "fields": {
    "brand_id": {"type": "int", "min": 1, "max": 5, "avg": 2.75},
    "category": {"type": "string", "distinct": ["Выхлопная система", "Фильтры"]},
    "price": {"type": "float", "min": 380, "max": 45000, "avg": 7466}
  }
}
```

- `distinctValues()` — до 20 значений (`LIMIT 20`).
- `fieldStats()` — min/max/avg.
- Tenant-фильтр передаётся в оба.
- Distinct-значения типизированы: `columnFieldType` берёт тип колонки из `entity.Fields` (fallback `"string"`), сканирование в `any` + `runtime.CoerceNative(raw, fieldType)` — числа/булы возвращаются как числа/булы, а не строки (старый код сканировал в `sql.NullString` и терял тип).

## EmptyHint — подсказка LLM при пустом результате

`collectEmptyHint` (`strategy_handler.go`) — при `total==0` собирает до 5 distinct-значений на string-поле и возвращает:

```json
{
  "suggested_action": "Try schema_auto_parts() to discover available values, then retry with exact values.",
  "available_values": {"category": ["Выхлопная система", "Фильтры", "Электрика"]}
}
```

## Tenant isolation

- `tenantFilter()` — вставляет WHERE из `auth.RowFilters` по entity, `:tenant_id` → нативный плейсхолдер.
- Для Condition-based (filter): `strategy_handler.go` добавляет ` AND tenant...` через `insertTenantBeforeLimit` (:306).
- Для RawWhere (grep): `SELECT * FROM (raw) WHERE tenant...` (subquery wrap, tenant_id добавляется в проекцию inner-подзапроса).
- `tenant_id` исключён из LLM-параметров (grep и filter).
- **Count:** `tenant_id` исключён из фильтров и системных параметров. Fail-closed: запрос `count?tenant_id=<чужой>` отклоняется.
- **/stats:** tenant-фильтр применяется к каждому counter. Fail-closed: без row_filter запрос не выполняется (403).
- **Fail-closed (P0-1):** `tenantFilter` возвращает `denyReason != tenantDenyNone` когда header-auth настроен, но изоляция невозможна: пустой `X-Tenant-ID` → `tenantDenyMissingTenantID` (400, ошибка запроса), отсутствие `row_filter` для entity → `tenantDenyMissingRowFilter` (403, ошибка конфига). Регресс: `row_filter_security_test.go`.
- **Валидация конфига:** `Validate()` требует row_filter для КАЖДОЙ entity при любой не-none auth-стратегии (`types.go`) — fail at onboarding, а не 403 в проде. Rewrite (`tenant_admin.go`) тоже валидирует до записи.
- Ошибки count-запроса логируются, а не глотаются: `runCountQuery` возвращает `-1` только при реальной ошибке SQL/scan.

## Лимиты (сводно)

| Limit | Значение | Где |
|---|---|---|
| maxLimit | 100 | strategy_common.go:101 |
| maxPatternLen | 500 | grep.go:31 |
| maxRegexLen | 200 | grep.go:25 |
| maxTokens | 10 | grep.go:27 |
| maxFields | 20 | grep.go:29 |
| maxFilters | 15 | filter.go:44 |
| maxInValues | 50 | filter.go:28 |
| maxFilterValueLen | 200 | filter.go:26 |
| distinct в schema | 20 | schema_handler.go:103 |
| distinct в distinct-эндпоинте | 50 | distinct.go:58 (`LIMIT 50`) |
| distinct в EmptyHint | 5 | strategy_handler.go:266 (`LIMIT 5`) |
| query timeout | 30s | endpoint_builder.go (QUERY_TIMEOUT_SECONDS) |

## MCP-тулы

Параметры тулов генерируют стратегии через `ToolParams()` (`search/strategy.go`), не из дискового конфига. Манифест генерируется runtime из `cfg.Endpoints` (`runtime/handlers/mcp_manifest.go`). Дисковый `cfg.MCPTools` не используется.

**Поверхность (Фаза 2/2.5 — LLM-first):** N пер-энтити `filter_{entity}` + 6 консолидированных `db_*` через `/q/*` диспетчер. Полный список и fallback-поведение: [services/data-service/README.md#mcp-тулы](services/data-service/README.md#mcp-тулы). Implementation: `runtime/handlers/q_dispatch.go`.

| Strategy | MCP tool | Параметры |
|---|---|---|
| grep | `db_search` (`/q/search?entity=&pattern=`) | entity (string, не enum), pattern (required), limit, fields |
| filter | `filter_{entity}` (пер-энтити, `/{entity}/filter`) | поля по IsFilterableField + операторы (`price__gt`, `__like`, `__in`), limit |
| schema | `db_describe` (`/q/describe?entity=`) | entity |
| map | `db_map` (`/q/map`) | — |
| get | `db_get` (`/q/get?entity=&id=`) | entity, id |
| related | `db_related` (`/q/related?entity=&id=&relation=`) | entity, id, relation |

- **Почему filter пер-энтити, а остальные консолидированы:** слабая модель не вытаскивает имена полей из db_map; `filter_{entity}` кладёт их топ-левел в схему тула → `filter_products?price__gt=100` с первого раза (валидировано живой моделью Ollama). grep/schema/get/related не требуют имён полей в схеме — консолидированы.
- **`get_*`/`count_*`/`distinct_*` не эмитятся** (default false) — opt-in через `config.LLMToolPolicy`. Анти-перебор: db_get только с id из результата поиска.
- **entity — обычный string**, валидируется `EntityResolver` (whitelist, неизвестный → 404). Допустимые имена — из `db_map`.
- WorkflowHints в `db_map` ссылаются только на реальные тулы (`db_map`/`db_describe`/`db_search`/`db_filter`/`db_get`/`filter_<entity>`), без доменных слов.
- REST-эндпоинты `/{entity}/grep|filter|schema|{id}|count|distinct` **сохранены** — деконсолидация касается только MCP-манифеста.

## Тесты

```bash
go test ./data-service/internal/query/...     # engine
go test ./data-service/internal/search/...    # стратегии
go test ./data-service/internal/runtime/handlers/ -run TestTenantFilter -v   # tenant isolation
```

## Related

- `services/data-service/README.md` — обзор сервиса
- `services/data-service/internal/configgen/README.md` — FieldRules, генерация тулов
- `doc/api-flow.md` — HTTP-матрица

---
**Last verified:** 2026-08-24 (working tree following `0add4ea`) — documentation restructure: removed historical prose from tenant isolation section.
