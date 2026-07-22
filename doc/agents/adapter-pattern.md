# Adapter Pattern — Добавление новой СУБД / нового бэкенда

Есть **два уровня** адаптации:

1. **DataSource** — высокоуровневый. Нужен для подключения не-SQL бэкенда (CRM, NoSQL, REST API)
2. **Adapter** — низкоуровневый. Нужен для добавления новой СУБД в SQL pipeline (MySQL, MSSQL)

---

## Уровень 1: DataSource (для не-SQL бэкендов)

### Интерфейс

`data-service/internal/datasource/datasource.go`:

```go
type DataSource interface {
    Type() string                        // "sql", "crm", "nosql"
    Search(ctx, q *Query) (*Result, error)
    Filter(ctx, q *Query) (*Result, error)
    GetByID(ctx, entity string, id any) (*Record, error)
    Count(ctx, q *Query) (int64, error)
    Distinct(ctx, entity, column string) ([]string, error)
    Schema(ctx, entity string) (*SchemaInfo, error)
    Close() error
}
```

### Как добавить CRM/Nosql/API

1. Реализовать `DataSource` interface в любом пакете
2. Зарегистрировать в `TenantStore` через `SetDataSource()`
3. Всё остальное — не трогать

```go
// Пример: CRMDataSource для HubSpot
type CRMDataSource struct {
    client *hubspot.Client
    tenant string
}

func (c *CRMDataSource) Type() string { return "crm" }

func (c *CRMDataSource) Search(ctx context.Context, q *Query) (*Result, error) {
    // API вызов к CRM вместо SQL
    contacts, err := c.client.Search(q.Pattern, q.Limit)
    return &Result{Total: len(contacts), Preview: contacts}, nil
}

// ... Filter, GetByID, Count, Distinct, Schema — аналогично
```

### Query — структура запроса

```go
type Query struct {
    Entity   string        // имя сущности ("product", "contact")
    Pattern  string        // текстовый поиск (для Search)
    Filters  []FieldFilter // field__op фильтры
    Fields   []string      // поля для поиска
    Limit    int           // hard cap 100
    Offset   int
    Format   ResultFormat  // compact | full
    TenantID string        // заполняется сервером, не из аргументов LLM
}

type FieldFilter struct {
    Field    string
    Operator string  // "eq", "neq", "gt", "gte", "lt", "lte", "like", "in"
    Value    any
    Values   []any
}
```

---

## Уровень 2: SQL Adapter (для новой СУБД)

### Два интерфейса

#### datasource.Adapter — подключение, интроспекция

`data-service/internal/datasource/adapter.go`:

```go
type Adapter interface {
    Driver() string                         // "sqlite", "postgres", "mysql"
    Connect(ctx, dsn) (Conn, error)         // sql.Open + PingContext
    Introspect(ctx, conn) (*Schema, error)  // системные таблицы → generic Schema
    TranslatePlaceholder(index int) string   // "?" / "$N"
    QuoteIdentifier(name string) string      // `"name"` / `\`name\``
}
```

#### query.AdapterSubset — для поискового движка

`data-service/internal/query/builder.go`:

```go
type AdapterSubset interface {
    TranslatePlaceholder(index int) string   // "?" / "$1"
    QuoteIdentifier(name string) string      // квотирование имён
    QuoteString(s string) string             // экранирование % и _ для LIKE
}
```

`QuoteString` экранирует wildcard-символы LIKE: `% → \%`, `_ → \_`.
Для SQLite/Postgres реализация одинакова. Если твоя СУБД использует другой escape
(например MySQL — `%%`), переопредели.

### Шаги для MySQL

**Шаг 1: Адаптер**

```go
// data-service/internal/datasource/mysql_adapter.go
type MySQLAdapter struct{}

func (MySQLAdapter) Driver() string                         { return "mysql" }
func (MySQLAdapter) Connect(ctx, dsn) (Conn, error)         // sql.Open + PingContext
func (MySQLAdapter) TranslatePlaceholder(index int) string  // "?" для MySQL
func (MySQLAdapter) QuoteIdentifier(name string) string     // `backtick`
func (MySQLAdapter) Introspect(ctx, conn) (*Schema, error)  // SHOW TABLES/COLUMNS + маппинг типов
```

**Шаг 2: Зарегистрировать**

`data-service/internal/datasource/registry.go` → `NewDefaultRegistry()` → `r.Register(MySQLAdapter{})`

**Шаг 3: Driver const + Valid()**

`helperium-go/config/types.go` → добавить `DriverMySQL` в enum и `Valid()`.

**Шаг 4: Тесты**

```bash
go test ./data-service/internal/datasource/ -run TestMySQLAdapter_*
go test ./data-service/internal/query/...     # ~37 тестов engine (должны проходить без правок)
go test ./data-service/internal/search/...    # ~60 тестов стратегий (должны проходить без правок)
```

**Весь существующий код** (runtime handlers, search strategies, query engine, MCP tools, admin API)
не требует правок — работает через интерфейсы.

---

## Связь уровней

```
DataSource interface          ← высокий уровень, для LLM
    │
    ├── SQLDataSource         ← реализация через query.Engine
    │       │
    │       └── AdapterSubset ← низкий уровень, для СУБД
    │               │
    │               ├── SQLiteAdapter
    │               ├── PostgresAdapter
    │               └── MySQLAdapter (future)
    │
    └── CRMDataSource (future) ← другая реализация
    └── NoSQLDataSource (future)
```

**Зачем разделение:**
- DataSource — для LLM-сценариев (Search/Filter/Count). Интерфейс предметной области.
- Adapter — для SQL-сценариев (интроспекция схемы, placeholder'ы). Интерфейс СУБД.

Детали поискового движка: [search-strategies.md](search-strategies.md)
