# Search Strategies — Поисковый движок data-service

## Архитектура

```
HTTP Request
    │
    ▼
handler (strategy_handler.go) — тонкий, парсит entity, tenant filter
    │
    ▼
Strategy.ParseRequest(r, entity, adapter) → QueryPlan
    │  search: grep+filter комбо (pattern + field__op, security limits)
    │  grep: tokenize, multi-field OR, regex/ILIKE, invert
    │  filter: field__gt/lt/like/in, type-aware value parsing
    │  simple: backward compat (LIKE на search_field)
    ▼
Engine.Build(plan) → SQL + args
    │  Expression AST → нативные placeholder'ы ($? / $1)
    │  QuoteIdentifier(), QuoteString() через адаптер
    ▼
DB.QueryContext(sql, args...)
    │
    ▼
MapRows → FormatRows(rows, total, format) → SearchResult JSON
    │  compact: {total, returned, preview: [{id, name}]}
    │  full: все колонки
    │  count: {entity, count}
```

## Пакеты

```
data-service/internal/query/           — Expression engine (DB-agnostic)
├── expression.go                       QueryPlan, Condition, Operator (12 видов)
├── builder.go                          Engine.Build() / BuildCount()
└── format.go                           SearchResult, CompactRow, FormatRows()

data-service/internal/search/           — Search strategies
├── strategy.go                         Strategy interface + Adapter wrapper
├── search.go                           SearchStrategy (grep+filter combined)
├── grep.go                             GrepStrategy
├── filter.go                           FilterStrategy
├── simple.go                           SimpleStrategy
└── results.go                          Re-exports
```

## Query Engine (`data-service/internal/query/`)

### QueryPlan — полное описание SELECT-запроса

```go
type QueryPlan struct {
    Select    SelectClause    // какие колонки
    From      string          // квотированное имя таблицы
    Where     []Condition     // AND-список условий
    RawWhere  string          // сырое WHERE (для сложных OR/AND)
    Order     []OrderClause   // сортировка
    Limit     int
    Offset    int
    Format    ResponseFormat  // compact | full | count
}
```

### Condition — одно условие WHERE

```go
type Condition struct {
    Field    string      // квотированное имя колонки
    Operator Operator    // Eq, Neq, Lt, Gt, Lte, Gte, Like, ILike, NotLike, Regex, In, Between
    Value    any         // скалярное значение
    Values   []any       // для IN / Between
    Not      bool        // NOT-флаг (invert)
    RawValue bool        // true = уже экранирован, не вызывать QuoteString
}
```

### Операторы и их SQL

| Operator | SQLite | PostgreSQL |
|---|---|---|
| `OpEq` | `col = ?` | `col = $1` |
| `OpNeq` | `col != ?` | `col != $1` |
| `OpLt` | `col < ?` | `col < $1` |
| `OpGt` | `col > ?` | `col > $1` |
| `OpLike` | `col LIKE ?` | `col LIKE $1` |
| `OpILike` | `col LIKE ?` (уже CI) | `col ILIKE $1` |
| `OpRegex` | `col REGEXP ?` | `col ~ $1` |
| `OpIn` | `col IN (?, ?, ?)` | `col IN ($1, $2, $3)` |
| `OpBetween` | `col BETWEEN ? AND ?` | `col BETWEEN $1 AND $2` |

### QuoteString — экранирование LIKE

Экранирует `%` → `\%`, `_` → `\_`. Реализация DB-agnostic в `query.AdapterSubset.QuoteString()`.

## Search Strategies (`data-service/internal/search/`)

### Strategy interface

```go
type Strategy interface {
    Name() string                         // "grep", "filter", "simple", "search"
    ParseRequest(r, entity, adapter)      // HTTP → QueryPlan
    ToolName(entity) string               // "grep_products" / "search_products"
    ToolDescription(entity) string         // LLM-friendly описание
    ToolParams(entity) []EndpointParam     // MCP параметры
    EntityIDCol() string                   // для compact format
    EntityNameCol() string                 // для compact format
}
```

### GrepStrategy — grep_{entity}

Аналог GNU grep для БД. LLM-friendly, token search.

**MCP параметры:**

| Параметр | Тип | Описание |
|---|---|---|
| `pattern` | string | Поисковый запрос. Multi-word = AND токенов |
| `ignore_case` | bool | CI поиск (default: true) |
| `fields` | string | Поля через запятую (default: все string) |
| `invert` | bool | NOT (исключить совпадения) |
| `regex` | bool | Regex-режим (default: false) |
| `limit` | int | Max результатов (1-1000, default: 10) |
| `offset` | int | Пагинация |
| `format` | string | "compact" или "full" (default: compact) |
| `sort_by` | string | Поле сортировки, "-field" для DESC |

**Token search (multi-word):**
```
pattern="глушители авто"
  → tokens: ["глушител", "авто"]
  → WHERE (name ILIKE '%глушител%' AND name ILIKE '%авто%')
     OR (description ILIKE '%глушител%' AND description ILIKE '%авто%')
```

**Примеры для LLM:**
```
grep_products(pattern="глушит")                     # basic
grep_products(pattern="глушит", ignore_case=false)  # case-sensitive
grep_products(pattern="^спорт", regex=true)          # regex: starts with
grep_products(pattern="брак", invert=true)           # exclude
grep_products(pattern="колодк", fields="name,desc")  # 2 поля
# pattern="" now returns an error — use list_ or grep without pattern
# is not allowed. Always specify a search term.
```

### FilterStrategy — filter_{entity}

Фильтрация с компараторами, аналог Django ORM.

**MCP параметры:**

| Параметр | Тип | Описание |
|---|---|---|
| `{field}` | varies | Exact match |
| `{field}__gt` | number | Больше |
| `{field}__gte` | number | Больше или равно |
| `{field}__lt` | number | Меньше |
| `{field}__lte` | number | Меньше или равно |
| `{field}__like` | string | LIKE с `%` wildcard |
| `{field}__in` | comma-list | IN (a, b, c) |
| `limit` | int | Max результатов (default: 10) |
| `offset` | int | Пагинация |
| `sort_by` | string | Сортировка, "-field" для DESC |
| `format` | string | "compact" или "full" |

**Примеры:**
```
filter_orders(status=shipped)                              # exact
filter_orders(total__gt=5000, status=shipped)              # comparison + exact
filter_orders(created_at__gte=2024-01-01, format=compact)  # date filter
filter_orders(name__like=%muffler%)                         # custom LIKE
filter_orders(status__in=new,processing,shipped)            # IN
```

### SearchStrategy — search_{entity} (рекомендуемый)

**Появился:** v3. Объединяет grep (text search) + filter (field filtering) в один
инструмент search_{entity}. Рекомендуется как основной entry point для LLM.

**MCP параметры:**

| Параметр | Тип | Описание |
|---|---|---|
| `pattern` | string | **REQUIRED** (по факту — LLM должна всегда передавать). Multi-token AND |
| `{field}` | varies | Exact match |
| `{field}__gt` | number | Больше |
| `{field}__gte` | number | Больше или равно |
| `{field}__lt` | number | Меньше |
| `{field}__lte` | number | Меньше или равно |
| `{field}__like` | string | LIKE с `%` wildcard |
| `{field}__in` | comma-list | IN (a, b, c) |
| `{field}__neq` | varies | Not equal |
| `limit` | int | Max результатов (default: 10) |

**Логика парсинга:**
- Есть pattern → grep-like multi-token AND + multi-field OR по строковым полям
- Есть field__op фильтры → filter-like точная фильтрация
- Есть и то и другое → AND комбинация
- Нет ни того, ни другого → 400 ошибка "at least one parameter required"

**Security limits:**
- `maxFilters` = **15** — макс field__op фильтров
- `maxTotalConditions` = **25** — макс всего условий (tokens + field фильтры)
- `maxRegexLen` = 200, `maxTokens` = 10, `maxFields` = 20
- `maxPatternLen` = 2000, `maxFilterValueLen` = 1000, `maxInValues` = 100

**Примеры:**
```
search_products(pattern="тормозные колодки")            # text search
search_products(category="Тормозная система")            # field filter only
search_products(pattern="Brembo", category="Тормозная система")  # combo
search_products(pattern="колодк", limit=5)               # limit
```

### SimpleStrategy — simple_{entity}

Backward compat. Копирует поведение старого `find_{entity}`:
- LIKE на search_field (name/title)
- Exact match на остальных полях
- Возвращает `format=full` (все колонки)
- Default limit=100

### ⚠️ ToolParams explosion warning

SearchStrategy.ToolParams() генерирует по 5–7 параметров на каждое поле entity
(exact match + __gt/__gte/__lt/__lte на numeric, __like на string, __neq на все,
__in на все). Для entity с 20+ полями это может превысить **128-параметровый лимит
LLM-модели** (например, Claude 3.5 Sonnet).

**Последствия:** LLM не видит инструмент (tool dropped), ломается discovery.

**Рекомендация:** Для entity с более чем 15–18 полями не использовать SearchStrategy,
а оставить grep + filter как отдельные стратегии. Либо ограничить ToolParams только
ключевыми полями через паттерн-параметры.

## MCP Tool Generation — Filtering (hasStrategy skip)

В `configgen/mcp.go:GenerateMCPTools()` реализована логика ``hasStrategy``:

```go
// Build set of entities that have strategy-based endpoints
hasStrategy := make(map[string]bool)
for _, ep := range endpoints {
    if ep.Strategy != "" && ep.Entity != "" {
        hasStrategy[ep.Entity] = true
    }
}
```

Для entity, у которых есть strategy-эндпоинт (grep/filter/search), следующие
tool types **скипаются** (continue):

| Tool type | Условие | Причина |
|---|---|---|
| `OpFind` (find_{entity}) | `if hasStrategy[ep.Entity] { continue }` | search strategy handles text search |
| `OpList` (list_{entity}) | `if hasStrategy[ep.Entity] { continue }` | search strategy handles listing |
| `OpCustomQuery` (relationship) | `if hasStrategy[ep.Entity] { continue }` | relationship tools confuse LLM |

**Итог:** LLM видит только `search_{entity}`, `get_{entity}`, `count_{entity}`,
`distinct_{entity}` — вместо 5+ тулов на entity.

## Response Format

Все search endpoint'ы (grep, filter) возвращают **compact по умолчанию**:

```json
// GET /products/grep?pattern=глушит&limit=2
{
  "total": 47,
  "returned": 2,
  "preview": [
    {"id": 1, "name": "Глушитель прямой"},
    {"id": 2, "name": "Глушитель спортивный"}
  ]
}
```

С `format=full`:
```json
{
  "total": 47,
  "returned": 2,
  "data": [
    {"id": 1, "name": "Глушитель прямой", "price": 5000, "status": "active"},
    {"id": 2, "name": "Глушитель спортивный", "price": 15000, "status": "active"}
  ]
}
```

## Как добавить новую стратегию

1. Создать `data-service/internal/search/xxx.go` — реализовать `Strategy` interface
2. Зарегистрировать в `endpoint_builder.go` в switch `ep.Strategy`
3. Добавить генерацию endpoint'а в `configgen/configgen.go` → `buildCRUDEndpoints()`
4. Стратегия сама генерирует MCP tool params/description — `configgen/mcp.go` не трогать

Никакие handlers, builder'ы или adapter'ы не меняются.

## Связь с другими модулями

| Модуль | Что даёт | Что получает |
|---|---|---|
| `query.Engine` | SQL+args из QueryPlan | QueryPlan от стратегии |
| `query.AdapterSubset` | QuoteIdentifier, TranslatePlaceholder, QuoteString | — |
| `search.Adapter` | обёртка над query.AdapterSubset + IsPostgres() | — |
| `handlers.NewStrategyHandler` | HTTP handler | Strategy + entity |
| `configgen.GenerateMCPTools` | MCPTool[] | Strategy.ToolName/Params/Description |
| `server.NewRouterFromConfig` | chi.Router | strategy-эндпоинты из config |
| `runtime.Builder` | MapRows/MapRow (пока) | сырые *sql.Rows → []map |

## Тестирование

```bash
go test ./data-service/internal/query/...     # 37 тестов engine
go test ./data-service/internal/search/...    # 82 теста стратегий
go test ./data-service/...                    # 572 теста всего
```

### SearchStrategy unit tests (search_test.go)

- 30 тестов (TestSearchStrategy_*) — охватывают pattern-only, filter-only, combined,
  custom fields, limit/offset/sort, format, postgres placeholders, tool params/description.
- +5 security tests (TestSearchSecurity_*) — ReDoS, token flood, field limit, pattern length, filter value length, IN values cap.

### GrepStrategy unit tests (grep_test.go)

- 26 тестов — всё то же самое для grep (включая empty-pattern error).

### FilterStrategy unit tests (filter_test.go)

- 26 тестов — filter logic (exact, comparison, like, in, skipPK, limit default/custom, format, sort, tool params).

### Config generation tests (configgen/)

- **configgen_test.go** — 9 unit-тестов генерации конфигов (entities, endpoints, tools, filter params, FK relations, skip rules).
- **integration_test.go** — 3 E2E-теста на реальной PostgreSQL autoparts БД (introspect, generate, tool count sanity).

### E2E files

| File | Tests | Описание |
|---|---|---|
| `configgen/integration_test.go` | 3 | Реальный PG autoparts — Introspect, Generate, ToolCount |
| `configgen/configgen_test.go` | 9 | Unit-тесты генерации конфигов и MCP-тулов |
