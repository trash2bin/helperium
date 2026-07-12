package datasource_test

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// TestPostgresAdapter_Driver — адаптер сообщает свой идентификатор.
func TestPostgresAdapter_Driver(t *testing.T) {
	if got := (datasource.PostgresAdapter{}).Driver(); got != "postgres" {
		t.Fatalf("Driver() = %q, want %q", got, "postgres")
	}
}

// TestPostgresAdapter_TranslatePlaceholder — Postgres использует '$N' (1-based).
func TestPostgresAdapter_TranslatePlaceholder(t *testing.T) {
	a := datasource.PostgresAdapter{}
	cases := []struct {
		in   int
		want string
	}{
		{1, "$1"},
		{2, "$2"},
		{3, "$3"},
		{42, "$42"},
	}
	for _, c := range cases {
		if got := a.TranslatePlaceholder(c.in); got != c.want {
			t.Errorf("TranslatePlaceholder(%d) = %q, want %q", c.in, got, c.want)
		}
	}
}

// TestPostgresAdapter_QuoteIdentifier — двойные кавычки ANSI SQL.
func TestPostgresAdapter_QuoteIdentifier(t *testing.T) {
	a := datasource.PostgresAdapter{}
	cases := []struct {
		in, want string
	}{
		{"orders", `"orders"`},
		{"order line", `"order line"`},
		{"customer_id", `"customer_id"`},
		{"", `""`},
	}
	for _, c := range cases {
		if got := a.QuoteIdentifier(c.in); got != c.want {
			t.Errorf("QuoteIdentifier(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// TestPostgresAdapter_MapPostgresType — unit-тест маппинга типов.
// mapPostgresType приватна, поэтому unit-тест без БД невозможен.
// Покрытие маппинга выполняется через TestPostgresAdapter_Introspect_Integration,
// если POSTGRES_TEST_URL задана: тест проверяет, что колонки SERIAL/NUMERIC/TEXT/TIMESTAMPTZ
// мапятся в TypeInt/TypeFloat/TypeString/TypeDatetime соответственно.

// TestPostgresAdapter_Introspect_Integration — интеграционный тест против реального
// PostgreSQL. Запускается только если задана переменная окружения POSTGRES_TEST_URL
// (например, "postgres://tutor:tutor@127.0.0.1:5432/postgres?sslmode=disable").
//
// Поднимается в docker-compose через `docker compose up -d db`. Схема test_introspect
// создаётся на лету и удаляется по завершении.
func TestPostgresAdapter_Introspect_Integration(t *testing.T) {
	dsn := os.Getenv("POSTGRES_TEST_URL")
	if dsn == "" {
		t.Skipf("POSTGRES_TEST_URL не задана — пропускаем интеграционный тест.\n" +
			"Поднять Postgres локально:\n" +
			"  docker compose up -d db\n" +
			"Запустить:\n" +
			"  POSTGRES_TEST_URL='postgres://tutor:tutor@127.0.0.1:5432/postgres?sslmode=disable' \\\n" +
			"    go test ./internal/datasource/... -run Introspect_Integration -v")
	}

	ctx := context.Background()
	a := datasource.PostgresAdapter{}

	// 1. Открываем соединение.
	conn, err := a.Connect(ctx, dsn)
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	// 2. Создаём временную схему с таблицами.
	const schema = "test_introspect"
	setup := []string{
		fmt.Sprintf(`DROP SCHEMA IF EXISTS %s CASCADE`, schema),
		fmt.Sprintf(`CREATE SCHEMA %s`, schema),
		fmt.Sprintf(`
			CREATE TABLE %s.users (
				id SERIAL PRIMARY KEY,
				email TEXT NOT NULL,
				age INTEGER,
				created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
			)`, schema),
		fmt.Sprintf(`
			CREATE TABLE %s.orders (
				id SERIAL PRIMARY KEY,
				user_id INTEGER REFERENCES %s.users(id),
				total NUMERIC(10,2)
			)`, schema, schema),
		fmt.Sprintf(`COMMENT ON COLUMN %s.users.email IS 'User email address'`, schema),
	}
	for _, stmt := range setup {
		if _, err := conn.ExecContext(ctx, stmt); err != nil {
			t.Fatalf("setup %q: %v", stmt, err)
		}
	}
	t.Cleanup(func() {
		_, _ = conn.ExecContext(context.Background(),
			fmt.Sprintf(`DROP SCHEMA IF EXISTS %s CASCADE`, schema))
	})

	// 3. Запускаем интроспекцию.
	got, err := a.Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}

	if got.Driver != "postgres" {
		t.Errorf("Driver = %q, want %q", got.Driver, "postgres")
	}

	// 4. Фильтруем таблицы по нашей схеме (тестовая схема test_introspect
	// не должна пересекаться с системными — Introspect их уже исключает,
	// но в реальной БД может быть public/ и другие схемы пользователя).
	users := findTable(got.Tables, schema+".users")
	orders := findTable(got.Tables, schema+".orders")
	if users == nil {
		t.Fatalf("table %q.users не найдена в интроспекции (доступны: %v)",
			schema, tableFQNs(got))
	}
	if orders == nil {
		t.Fatalf("table %q.orders не найдена в интроспекции", schema)
	}

	// 5. Проверяем колонки users.
	wantUsersCols := []struct {
		name     string
		typ      string
		nullable bool
	}{
		{"id", datasource.TypeInt, false},
		{"email", datasource.TypeString, false},
		{"age", datasource.TypeInt, true},
		{"created_at", datasource.TypeDatetime, false},
	}
	if len(users.Columns) != len(wantUsersCols) {
		t.Errorf("users.Columns: длина = %d, want %d (got %v)",
			len(users.Columns), len(wantUsersCols), columnNames(users.Columns))
	}
	for i, want := range wantUsersCols {
		if i >= len(users.Columns) {
			break
		}
		got := users.Columns[i]
		if got.Name != want.name {
			t.Errorf("users.Columns[%d].Name = %q, want %q", i, got.Name, want.name)
		}
		if got.Type != want.typ {
			t.Errorf("users.Columns[%d] (%q).Type = %q, want %q",
				i, want.name, got.Type, want.typ)
		}
		if got.Nullable != want.nullable {
			t.Errorf("users.Columns[%d] (%q).Nullable = %v, want %v",
				i, want.name, got.Nullable, want.nullable)
		}
	}

	// 6. Проверяем PK users.
	if !equalStringSlices(users.PrimaryKey, []string{"id"}) {
		t.Errorf("users.PrimaryKey = %v, want [id]", users.PrimaryKey)
	}

	// 7. Проверяем Description на email.
	emailCol := findColumn(users.Columns, "email")
	if emailCol == nil {
		t.Fatalf("users.email column not found")
	}
	if emailCol.Description != "User email address" {
		t.Errorf("users.email.Description = %q, want %q",
			emailCol.Description, "User email address")
	}

	// 8. Проверяем колонки orders (минимально — total: numeric -> float).
	totalCol := findColumn(orders.Columns, "total")
	if totalCol == nil {
		t.Errorf("orders.total column not found (got columns: %v)",
			columnNames(orders.Columns))
	} else if totalCol.Type != datasource.TypeFloat {
		t.Errorf("orders.total.Type = %q, want %q", totalCol.Type, datasource.TypeFloat)
	}

	// 9. Проверяем FK orders.user_id -> users.id.
	if len(orders.ForeignKeys) != 1 {
		t.Fatalf("orders.ForeignKeys: длина = %d, want 1 (got %v)",
			len(orders.ForeignKeys), orders.ForeignKeys)
	}
	fk := orders.ForeignKeys[0]
	if !equalStringSlices(fk.Columns, []string{"user_id"}) {
		t.Errorf("orders FK Columns = %v, want [user_id]", fk.Columns)
	}
	if fk.ReferencedTable != schema+".users" {
		t.Errorf("orders FK ReferencedTable = %q, want %q",
			fk.ReferencedTable, schema+".users")
	}
	if !equalStringSlices(fk.ReferencedColumns, []string{"id"}) {
		t.Errorf("orders FK ReferencedColumns = %v, want [id]", fk.ReferencedColumns)
	}
}

// findTable возвращает таблицу по FQN "schema.table" или nil.
func findTable(tables []datasource.Table, fqn string) *datasource.Table {
	for i := range tables {
		if tables[i].Name == fqn {
			return &tables[i]
		}
	}
	return nil
}

// findColumn возвращает колонку по имени или nil.
func findColumn(cols []datasource.Column, name string) *datasource.Column {
	for i := range cols {
		if cols[i].Name == name {
			return &cols[i]
		}
	}
	return nil
}

// tableFQNs возвращает список FQN таблиц для диагностики.
func tableFQNs(s *datasource.Schema) []string {
	out := make([]string, 0, len(s.Tables))
	for _, tbl := range s.Tables {
		out = append(out, tbl.Name)
	}
	return out
}

// columnNames возвращает список имён колонок для диагностики.
func columnNames(cols []datasource.Column) []string {
	out := make([]string, 0, len(cols))
	for _, c := range cols {
		out = append(out, c.Name)
	}
	return out
}

// (equalStringSlices moved to helpers_test.go)
