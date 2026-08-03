# Search Strategies — data-service

Поисковый движок data-service: стратегии grep/filter/schema поверх Expression AST → SQL.

## Архитектура

```
LLM tool_call (MCP) → api-service → mcp-gateway → HTTP GET /{entity}/{strategy}
  → runtime/handlers/strategy_handler.go:31 NewStrategyHandler
    → search.Strategy.ParseRequest() → query.QueryPlan
      → query.Engine.Build() → SQL+args
        → datasource.ReadOnlyConn → DB
```

Schema-стратегия идёт отдельным путём: `runtime/handlers/schema_handler.go:23` работает напрямую с БД, без Engine.

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

## Strategy interface (`search/strategy.go:16`)

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

Операторы (`expression.go:75-86`, рендер — `builder.go:156-300 renderCondition`):

| Operator | SQL (обычный) | SQL при `Not: true` |
|---|---|---|
| `OpEq` | `col = ?` | `col <> ?` (инверсия, а не невалидный `NOT =`; builder.go:161-164) |
| `OpNeq` | `col != ?` | `col = ?` (двойное отрицание; builder.go:171-174) |
| `OpLt` | `col < ?` | `col >= ?` |
| `OpGt` | `col > ?` | `col <= ?` |
| `OpLte` | `col <= ?` | `col > ?` |
| `OpGte` | `col >= ?` | `col < ?` |
| `OpLike` | `col LIKE ? ESCAPE '\\'` | `col NOT LIKE ? ESCAPE '\\'` |
| `OpILike` | `ILIKE` (PG) / `LIKE COLLATE NOCASE` (SQLite) | `NOT ILIKE` / `NOT LIKE` |
| `OpNotLike` | `col NOT LIKE ? ESCAPE '\\'` | — (сам по себе отрицание) |
| `OpRegex` | `REGEXP` (SQLite) / `~` (PG) | `NOT REGEXP` (SQLite) / `!~` (PG) (builder.go:288-300) |
| `OpIn` | `col IN (?, ?, ?)` | `col NOT IN (?, ?, ?)` |
| `OpBetween` | `col BETWEEN ? AND ?` | `col NOT BETWEEN ? AND ?` |

**ESCAPE `'\\'` обязателен** для всех LIKE-операторов (builder.go:232, :251, :261, :274): `QuoteString` экранирует `%`/`_` обратным слэшем, и без ESCAPE-клаузы экранирование не работает — в SQLite `\` трактуется как литерал, `%` остаётся wildcard'ом, данные с `%`/`_` в значении не находятся (и DoS-защита неэффективна). `RawValue: true` (filter `__like`) — значение без экранирования, wildcard'ы пользователя работают как есть.

Конструкторы: `expression.go:102-159` (`Eq/Neq/Lt/Lte/Gt/Gte/Like/ILike/Regex/NotLike/In/Between`).

**RawWhere используется только в grep** (`grep.go:243-244`): multi-token AND по полям собирается строкой `(col1 LIKE ? AND col1 LIKE ?) OR (col2 LIKE ? ...)`, т.к. это не выражается через []Condition. Filter — полностью Condition-based.

**Count для RawWhere+tenant строится из оригинального плана** (`strategy_handler.go:175-199`): `SELECT COUNT(*) FROM t WHERE (RawWhere) AND tenant`, а не через `countQueryWithArgs` от tenant-обёрнутого SQL. Старый путь ломал SQL (`strings.Index(" FROM ")` брал внутреннее FROM подзапроса, `LastIndex(" LIMIT ")` резал всё после LIMIT включая `) AS _t WHERE ...` → незакрытая скобка → total=-1 у каждого grep в multi-tenant). Тот же блок: inner-подзапрос для tenant-фильтра включает `tenant_id` в проекцию (`ensureColumn`), иначе внешний `WHERE` не видит колонку и SQLite молча возвращает 0 строк. Регресс: `tenant_count_regression_test.go`.

## GrepStrategy (`search/grep.go:22`)

Multi-token AND внутри поля, OR между полями. Лимиты в `NewGrepStrategy()` :48-63:

| Лимит | Значение |
|---|---|
| maxRegexLen | 200 (ReDoS) |
| maxTokens | 10 |
| maxPatternLen | 500 |

`ToolParams()` :75 — `pattern` (required), `limit` (1-100, default 10), `fields`.
`ParseRequest()` :91 — проверка длины pattern :99, regex длины :109.

HTTP-параметры (не все в JSON Schema): `pattern`, `ignore_case`, `fields`, `invert`, `regex`, `limit`, `offset`, `format`, `sort_by`.

Tenant isolation: `tenant_id` нельзя искать (grep.go:148, :169-172).

## FilterStrategy (`search/filter.go:33`)

`NewFilterStrategy(idCol, nameCol, filterableRules ...config.FieldRule)` :33.

Лимиты: maxFilterValueLen=200 (:26), maxInValues=50 (:28), maxFilters=15 (:44).

`ToolParams()` :86 — поля через `config.IsFilterableField()` :99 (см. FieldRules в configgen/README.md).

`ParseRequest()` :165. Операторы (HTTP-параметр `{field}__op`):

| Параметр | Поведение |
|---|---|
| `{field}` | exact (eq) |
| `{field}__neq` | not equal |
| `{field}__gt/__gte/__lt/__lte` | сравнения |
| `{field}__like` | LIKE с `%` (wildcard'ы пользователя; `\` — escape-символ: `50\%` = literal `50%`) |
| `{field}__in` | IN (comma-list, max 50) |
| `limit`/`offset`/`sort_by`/`format` | пагинация |

Проверки: длина значения :217-253, maxFilters :299, maxInValues :269. `tenant_id` недоступен (:194, :211).

## SchemaStrategy (`search/schema.go:25`)

- `ToolParams()` :48 — nil (без параметров).
- `ParseRequest()` :54 — nil (не использует Engine).
- `FieldInfo()` :58 — поля для schema-ответа.

Обработка: `schema_handler.go:23`. Ответ:

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

- `distinctValues()` :103 — до 20 значений (`LIMIT 20`).
- `fieldStats()` :135 — min/max/avg.
- Tenant-фильтр передаётся в оба.
- Distinct-значения типизированы: `distinct.go:13 columnFieldType` берёт тип колонки из `entity.Fields` (fallback `"string"`), сканирование в `any` + `runtime.CoerceNative(raw, fieldType)` (distinct.go:95) — числа/булы возвращаются как числа/булы, а не строки (старый код сканировал в `sql.NullString` и терял тип).

## EmptyHint — подсказка LLM при пустом результате

`strategy_handler.go:238 collectEmptyHint` — при `total==0` собирает до 5 distinct-значений на string-поле (`LIMIT 5`, :266) и возвращает:

```json
{
  "suggested_action": "Try schema_auto_parts() to discover available values, then retry with exact values.",
  "available_values": {"category": ["Выхлопная система", "Фильтры", "Электрика"]}
}
```

## Tenant isolation

- `tenantFilter()` (`row_filter.go:22`) — вставляет WHERE из `auth.RowFilters` по entity, `:tenant_id` → нативный плейсхолдер.
- Для Condition-based (filter): `strategy_handler.go` добавляет ` AND tenant...` через `insertTenantBeforeLimit` (:306).
- Для RawWhere (grep): `SELECT * FROM (raw) WHERE tenant...` (subquery wrap, tenant_id добавляется в проекцию inner-подзапроса).
- `tenant_id` исключён из LLM-параметров (grep.go:148, :172; filter.go:194, :211).
- **Count:** `tenant_id` исключён из фильтров — `count.go:37-42` (HIGH-15-fix: раньше `count?tenant_id=<чужой>` давал посчитать записи чужого тенанта; `tenant_id` добавлен в skip системных параметров count.go:53).
- **/stats:** tenant-фильтр применяется к каждому counter — `stats.go:36-42` (раньше `GET /stats` отдавал глобальные счётчики по всем тенантам).
- **Fail-closed (P0-1):** `tenantFilter` возвращает `denyReason != tenantDenyNone` когда header-auth настроен, но изоляция невозможна: пустой `X-Tenant-ID` → `tenantDenyMissingTenantID` (400, ошибка запроса), отсутствие `row_filter` для entity → `tenantDenyMissingRowFilter` (403, ошибка конфига). Раньше это был fail-open: `("", nil)` → SQL без WHERE → тенант видел чужие строки. Регресс: `row_filter_security_test.go`.
- **Валидация конфига:** `Validate()` требует row_filter для КАЖДОЙ entity при любой не-none auth-стратегии (`types.go`) — fail at onboarding, а не 403 в проде. Rewrite (`tenant_admin.go`) тоже валидирует до записи.
- Ошибки count-запроса логируются, а не глотаются: `runCountQuery` возвращает `-1` только при реальной ошибке SQL/scan (pagination.go:104-115).

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

Параметры тулов генерируют стратегии через `ToolParams()` (search/strategy.go:32), не из дискового конфига. Манифест — `runtime/handlers/mcp_manifest.go:20` (runtime из cfg.Endpoints).

| Strategy | MCP tool | Параметры |
|---|---|---|
| grep | `grep_{entity}` | pattern (required), limit, fields |
| filter | `filter_{entity}` | поля по IsFilterableField + операторы |
| schema | `schema_{entity}` | нет |

## Тесты

```bash
go test ./data-service/internal/query/...     # engine
go test ./data-service/internal/search/...    # стратегии
go test ./data-service/internal/runtime/handlers/ -run TestTenantFilter -v   # tenant isolation
```

## Related

- `data-service/README.md` — обзор сервиса
- `data-service/internal/configgen/README.md` — FieldRules, генерация тулов
- `doc/api-flow.md` — HTTP-матрица
