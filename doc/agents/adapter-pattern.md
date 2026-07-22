# Adapter Pattern — Добавление новой СУБД

Чтобы добавить MySQL, MSSQL и т.д., нужно реализовать интерфейсы `datasource.Adapter` (подключение/интроспекция) и `query.AdapterSubset` (поисковый движок).

## Интерфейсы

### datasource.Adapter — подключение, интроспекция

`data-service/internal/datasource/adapter.go`:

```go
type Adapter interface {
    Driver() string                     // "sqlite", "postgres", "mysql"
    Connect(ctx, dsn) (Conn, error)    // sql.Open + PingContext
    Introspect(ctx, conn) (*Schema, error)  // системные таблицы → generic Schema
    TranslatePlaceholder(index int) string   // "?" / "$N"
    QuoteIdentifier(name string) string      // `"name"` / `\`name\`` / `[name]`
}
```

### query.AdapterSubset — для поискового движка

`data-service/internal/query/builder.go`:

```go
type AdapterSubset interface {
    TranslatePlaceholder(index int) string   // "?" / "$1"
    QuoteIdentifier(name string) string      // квотирование имён
    QuoteString(s string) string             // экранирование % и _ для LIKE
}
```

`QuoteString` экранирует wildcard-символы LIKE: `% → \%`, `_ → \_`. Для SQLite/Postgres реализация одинакова. Если твоя СУБД использует другой escape (например MySQL — `%%`), переопредели.

## Шаг 1: Адаптер

```go
// data-service/internal/datasource/mysql_adapter.go
type MySQLAdapter struct{}

func (MySQLAdapter) Driver() string                            { return "mysql" }
func (MySQLAdapter) Connect(ctx, dsn) (Conn, error)            // sql.Open + PingContext
func (MySQLAdapter) TranslatePlaceholder(index int) string     // "?" для MySQL
func (MySQLAdapter) QuoteIdentifier(name string) string        // `backtick`
func (MySQLAdapter) Introspect(ctx, conn) (*Schema, error)     // SHOW TABLES/COLUMNS + маппинг типов
```

## Шаг 2: Зарегистрировать

`data-service/internal/datasource/registry.go` → `NewDefaultRegistry()` → `r.Register(MySQLAdapter{})`

## Шаг 3: Driver const + Valid()

`helperium-go/config/types.go` → добавить `DriverMySQL` в enum и `Valid()`.

## Шаг 4: Тесты

```bash
go test ./data-service/internal/datasource/ -run TestMySQLAdapter_*
go test ./data-service/internal/query/...     # 37 тестов engine (должны проходить без правок)
go test ./data-service/internal/search/...    # 51 тест стратегий (должны проходить без правок)
```

**Весь существующий код** (runtime handlers, search strategies, query engine, MCP tools, admin API) не требует правок — работает через интерфейсы.

Детали поискового движка: [doc/agents/search-strategies.md](search-strategies.md)
