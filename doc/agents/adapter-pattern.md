# Adapter Pattern — Добавление новой СУБД

Чтобы добавить MySQL, MSSQL и т.д., нужно реализовать интерфейс `datasource.Adapter`:

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

## Шаг 4: JSON Schema

Обновить `enum` в schema (если есть).

## Шаг 5: Тесты

```bash
go test ./data-service/internal/datasource/ -run TestMySQLAdapter_*
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"id":"test","config":{"version":1,"data_source":{"driver":"mysql","dsn":"mysql://user:pass@host:3306/db"},"entities":[],"endpoints":[]}}' \
  http://localhost:8084/admin/tenants
```

**Весь существующий код** (runtime handlers, query builder, admin API, MCP tools) не требует правок — работает через интерфейс.
