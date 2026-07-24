# Search Strategies — Поисковый движок data-service (v4)

## Архитектура

```
LLM Tool Call (MCP / HTTP)
       │
       ▼
DataSource interface              ← единый слой абстракции
  ├── Search(ctx, q)              — grep_{entity}: text search
  ├── Filter(ctx, q)              — filter_{entity}: field filters
  ├── GetByID(ctx, entity, id)    — get_{entity}
  ├── Count(ctx, q)               — count_{entity}
  ├── Distinct(ctx, entity, col)  — distinct_{entity}
  └── Schema(ctx, entity)         — schema_{entity}: metadata discovery
       │
       ▼
SQLDataSource (sql.go)            ← реализация через query.Engine
       │
       ▼
query.Engine (builder.go)         ← Expression AST → SQL
       │
       ▼
ReadOnlyDB                        ← урезанный *sql.DB (только SELECT)
       │
       ▼
DB.QueryContext(sql, args...)
```

### Ключевые изменения против v3

| Было | Стало | Причина |
|---|---|---|
| RawWhere (строковая конкатенация) | **Condition-based** (Expression AST) | Безопасность, tenant isolation |
| `search_{entity}` (grep+filter) | **Нет** — только grep / filter отдельно | LLM проще понять отдельные тулы |
| Strategy interface как entry | **DataSource interface** | Подготовка к не-SQL бэкендам |
| RawWhere tenant isolation (костыль) | **Condition-based tenant filter** | Гарантированная изоляция |
| Ошибки БД наружу | **Generic error + structured log** | Безопасность |

## Пакеты

```
data-service/internal/
├── datasource/                   — DataSource abstraction
│   ├── datasource.go              DataSource interface, Query, Result, SchemaInfo
│   ├── sql.go                     SQLDataSource — реализация через query.Engine
│   ├── readonly.go                ReadOnlyDB — безопасная обёртка *sql.DB
│   └── audit.go                   AuditRecorder no-op interface
│
├── query/                        — Expression-based SQL engine
│   ├── expression.go              QueryPlan, Condition, Operator, EmptyHint
│   ├── builder.go                 Engine.Build() / BuildCount()
│   └── format.go                  Response formatting
│
├── search/                       — Search strategies (HTTP-level)
│   ├── strategy.go                Strategy interface + Adapter wrapper
│   ├── strategy_common.go         Общие утилиты (parse*, tokenize, findColumn...)
│   ├── grep.go                    GrepStrategy — grep_{entity}
│   ├── filter.go                  FilterStrategy — filter_{entity}
│   └── schema.go                  SchemaStrategy — schema_{entity}
│
├── runtime/handlers/             — HTTP handlers
│   ├── strategy_handler.go        Generic handler for grep/filter strategies (legacy Strategy interface)
│   ├── schema_handler.go          Handler for schema_{entity} via legacy SchemaStrategy (StrategySchemaHandler)
│   └── datasource_handler.go      Handler for schema_{entity} via DataSource interface (SchemaHandler)
│
└── server/                       — Router + middleware
    └── endpoint_builder.go        Routes from config → handlers
```

## Query Engine (`data-service/internal/query/`)

### QueryPlan — полное описание SELECT-запроса

```go
type QueryPlan struct {
    Select    SelectClause     // какие колонки
    From      string           // квотированное имя таблицы
    Where     []Condition      // AND-список условий (Condition-based, без RawWhere)
    Order     []OrderClause    // сортировка
    Limit     int              // hard cap 100
    Offset    int
    Format    ResponseFormat   // compact | full | count
}
```

> **RawWhere больше нет.** Все условия проходят через Condition-based Engine.
> Это гарантирует единый SQL pipeline и корректную tenant isolation.

### Condition — одно условие WHERE

```go
type Condition struct {
    Field    string      // квотированное имя колонки
    Operator Operator    // Eq, Neq, Lt, Gt, Lte, Gte, Like, ILike, NotLike, Regex, In, Between
    Value    any         // скалярное значение
    Values   []any       // для IN / Between
    Not      bool        // NOT-флаг (invert)
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
| `OpILike` | `col LIKE ?` (SQLite LIKE ASCII-only CI) / `col COLLATE NOCASE` | `col ILIKE $1` |
| `OpRegex` | `col REGEXP ?` | `col ~ $1` |
| `OpIn` | `col IN (?, ?, ?)` | `col IN ($1, $2, $3)` |
| `OpBetween` | `col BETWEEN ? AND ?` | `col BETWEEN $1 AND $2` |

### EmptyHint — подсказка LLM при пустом результате

```go
type EmptyHint struct {
    SuggestedAction string              // "Use schema_{entity}() to discover values"
    AvailableValues map[string][]string // {brand: [Brembo, Bosch], category: [Brakes]}
}
```

Возвращается в JSON-ответе **только когда total == 0**, чтобы LLM не зацикливалась.

## Инструменты, которые видит LLM

| Инструмент | Назначение | LLM-friendly name |
|---|---|---|
| grep_{entity} | Текстовый поиск (multi-token AND, multi-field OR) | `grep_products` |
| filter_{entity} | Точная фильтрация (field__gt/lt/like/in) | `filter_orders` |
| get_{entity} | Получение записи по ID | `get_product` |
| count_{entity} | Количество записей с фильтрами | `count_products` |
| distinct_{entity} | Уникальные значения колонки | `distinct_brands` |
| schema_{entity} | Discovery: мета-информация о сущности | `schema_products` |

### Рекомендуемый workflow для LLM

```
Шаг 1: schema_{entity}()
  → {total: 35, fields: {brand: ["Brembo", "Bosch"], category: ["Brakes"]}}
  ✓ 1 запрос, <5ms, узнаёт структуру

Шаг 2: distinct_{entity}(column="brand")
  → ["Brembo", "Bosch", "TRW"]
  ✓ Быстрый discovery значений

Шаг 3: grep_{entity}(pattern="Brembo", limit=10)
  → Результаты или total=0 с подсказкой

[Если пусто]:
  → empty_hint: "Try schema_{entity}() to discover available values"
```

## GrepStrategy — grep_{entity}

Аналог GNU grep для БД. Multi-token AND, multi-field OR.

**MCP параметры:**

| Параметр | Тип | Описание |
|---|---|---|
| `pattern` | string | **REQUIRED.** Поисковый запрос. Multi-word = AND токенов |
| `ignore_case` | bool | CI поиск (default: true) |
| `fields` | string | Поля через запятую (default: все string кроме tenant_id/excluded) |
| `invert` | bool | NOT (исключить совпадения) |
| `regex` | bool | Regex-режим (default: false) |
| `limit` | int | Max результатов (1-100, default: 10) |
| `offset` | int | Пагинация |
| `format` | string | "compact" или "full" (default: compact) |
| `sort_by` | string | Поле сортировки, "-field" для DESC |

**Token search (multi-word):**
```
grep_products(pattern="глушители авто")
  → tokens: ["глушител", "авто"]
  → WHERE (name ILIKE '%глушител%' AND name ILIKE '%авто%')
     OR (description ILIKE '%глушител%' AND description ILIKE '%авто%')
```

### Пустой результат

```json
{
  "total": 0,
  "empty_hint": {
    "suggested_action": "Try schema_products() to discover available values, then retry.",
    "available_values": {"brand": ["Brembo", "Bosch"], "category": ["Brakes"]}
  }
}
```

## FilterStrategy — filter_{entity}

Фильтрация с компараторами (Django ORM-like).

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
| `{field}__neq` | varies | Not equal |
| `limit` | int | Max результатов (1-100, default: 10) |
| `offset` | int | Пагинация |
| `sort_by` | string | Сортировка, "-field" для DESC |
| `format` | string | "compact" или "full" |

**Примеры:**
```
filter_orders(status=shipped)                              # exact
filter_orders(total__gt=5000, status=shipped)              # comparison + exact
filter_orders(status__in=new,processing,shipped)            # IN
```

## SchemaStrategy — schema_{entity}

Один вызов — вся мета-информация о сущности. Заменяет несколько distinct + count.

**MCP параметры:** нет (всегда полный ответ)

**Ответ:**
```json
{
  "entity": "product",
  "total": 35,
  "fields": {
    "brand": {"type": "string", "distinct": ["Brembo", "Bosch", "TRW"]},
    "category": {"type": "string", "distinct": ["Brakes", "Engine"]},
    "price": {"type": "float", "min": 100, "max": 45000, "avg": 8500},
    "stock": {"type": "int", "min": 0, "max": 500}
  }
}
```

**Производительность:** 1 SQL запрос. Для SQLite с индексами — <5ms на 100k строк.

## Безопасность (Security)

### 1. Tenant isolation — enforced на уровне DataSource

`tenant_id` **никогда** не доступен LLM как field__op параметр:
- `case "tenant_id": continue` в ParseRequest grep + filter
- `f.Column == "tenant_id"` guard после fieldMap lookup
- TenantID добавляется в Query **только сервером** из контекста аутентификации

### 2. Field whitelist — на каждый вызов

`findColumn()` / `entity.FindColumn()` проверяет что field-имя существует в схеме:
- grep: `stringFields(entity)` — только string-поля из схемы
- filter: `fieldMap[f.Name]` — незнакомые ключи тихо скипаются
- distinct: `entity.FindColumn(column)` — 400 если колонки нет
- sort_by: `findColumn(entity, fieldName)` — тихо скипает незнакомые

### 3. PII/excluded поля

Поле с флагом `exclude_from_search` не попадает ни в один инструмент:
- Не участвует в grep (текстовом поиске)
- Не участвует в filter
- Не участвует в distinct
- Не участвует в schema

### 4. Read-only DB

`ReadOnlyDB` — отдельный тип, разрешающий **только SELECT**:
- QueryContext / QueryRowContext — без Exec, без Prepare write
- Проверка при старте: CREATE TEMP TABLE → если успешно, СУБД не read-only

### 5. Hard limits

| Limit | Значение | Где |
|---|---|---|
| maxLimit | **100** (было 1000) | parseLimitParam |
| maxPatternLen | **500** (было 2000) | grep constructor |
| maxRegexLen | 200 | grep |
| maxTokens | 10 | grep |
| maxFilters | 15 | filter |
| maxTotalConditions | 25 | query engine |
| maxFilterValueLen | 200 | filter |
| maxInValues | 50 | filter |
| statement timeout | 30s configurable | handlers.Context |

### 6. Санитизация ошибок

```
DB error → slog.ErrorContext(ctx, "DB error", "err", err, "tenant", tid, "entity", ent)
HTTP response → {"error": "query_failed", "message": "Query execution failed. Check field names via schema tool."}
```

### 7. Audit

`AuditRecorder` — опциональный, no-op по умолчанию. При подключении логирует:
- Tool name, entity, tenant_id
- Параметры запроса
- Количество возвращённых строк
- Длительность (ms)
- Ошибки

## MCP Tool Generation

Манифест генерируется через `configgen.GenerateMCPTools()`. Для каждой entity
создаются инструменты на основе её endpoints:

| Endpoint type | MCP tool | Генерируется из |
|---|---|---|
| `grep` | `grep_{entity}` | `search.NewGrepStrategy()` |
| `filter` | `filter_{entity}` | `search.NewFilterStrategy()` |
| `schema` | `schema_{entity}` | `search.NewSchemaStrategy()` |
| `get_by_id` | `get_{entity}` | configgen direct |
| `count` | `count_{entity}` | configgen direct |
| `distinct` | `distinct_{entity}` | configgen direct |

## DataSource Interface (для не-SQL бэкендов)

```go
type DataSource interface {
    Type() string
    Search(ctx, q *Query) (*Result, error)
    Filter(ctx, q *Query) (*Result, error)
    GetByID(ctx, entity, id any) (*Record, error)
    Count(ctx, q *Query) (int64, error)
    Distinct(ctx, entity, column string) ([]string, error)
    Schema(ctx, entity string) (*SchemaInfo, error)
    Close() error
}
```

Чтобы добавить новый бэкенд (CRM, NoSQL, API):
1. Реализовать `DataSource` interface
2. Зарегистрировать в `TenantStore`
3. Всё остальное (endpoint_builder, configgen, MCP) не трогать

## Тестирование

```bash
go test ./data-service/internal/query/...     # ~37 тестов engine
go test ./data-service/internal/search/...    # ~60 тестов стратегий
go test ./data-service/...                    # ~613 тестов всего
```

### Основные пакеты тестов

| Пакет | Тестов | Что покрывают |
|---|---|---|
| `query/...` | ~37 | Engine.Build/BuildCount, renderCondition, все операторы |
| `search/grep_test.go` | ~26 | token search, regex, invert, multi-field, empty pattern, security limits |
| `search/filter_test.go` | ~26 | exact, comparison, like, in, skipPK, limit/offset/sort, format |
| `search/schema_test.go` | ~10 | distinct values, min/max/avg, total count |
| `configgen/...` | ~12 | config generation, MCP tool count, PG integration |
| `runtime/handlers/...` | ~50+ | strategy handler, HTTP integration, tenant isolation |
