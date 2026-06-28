package datasource_test

import (
	"context"
	"testing"

	"github.com/agent-tutor/data-service/internal/datasource"
)

// TestSqliteAdapter_Driver — адаптер сообщает свой идентификатор.
func TestSqliteAdapter_Driver(t *testing.T) {
	if got := (datasource.SqliteAdapter{}).Driver(); got != "sqlite" {
		t.Fatalf("Driver() = %q, want %q", got, "sqlite")
	}
}

// TestSqliteAdapter_TranslatePlaceholder — SQLite использует нативный '?'.
func TestSqliteAdapter_TranslatePlaceholder(t *testing.T) {
	a := datasource.SqliteAdapter{}
	for _, idx := range []int{1, 2, 3, 42} {
		if got := a.TranslatePlaceholder(idx); got != "?" {
			t.Fatalf("TranslatePlaceholder(%d) = %q, want %q", idx, got, "?")
		}
	}
}

// TestSqliteAdapter_QuoteIdentifier — двойные кавычки ANSI SQL.
func TestSqliteAdapter_QuoteIdentifier(t *testing.T) {
	a := datasource.SqliteAdapter{}
	cases := []struct {
		in, want string
	}{
		{"items", `"items"`},
		{"item name", `"item name"`},
		{"created_at", `"created_at"`},
		{"", `""`},
	}
	for _, c := range cases {
		if got := a.QuoteIdentifier(c.in); got != c.want {
			t.Errorf("QuoteIdentifier(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// TestSqliteAdapter_Introspect_Empty — на :memory: без таблиц возвращается
// пустой Schema с правильным Driver.
func TestSqliteAdapter_Introspect_Empty(t *testing.T) {
	ctx := context.Background()

	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	got, err := (datasource.SqliteAdapter{}).Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}
	if got.Driver != "sqlite" {
		t.Errorf("Driver = %q, want %q", got.Driver, "sqlite")
	}
	if len(got.Tables) != 0 {
		t.Errorf("len(Tables) = %d, want 0; got tables = %v", len(got.Tables), tableNames(got))
	}
}

// TestSqliteAdapter_Introspect_GenericSchema — на generic-схеме
// (магазин: customers/orders/items) проверяем корректность introspector
// без привязки к доменной семантике (никаких university-имён).
//
// Покрывает: PRIMARY KEY, FOREIGN KEY с композитным ключом, разные типы
// колонок (INTEGER/TEXT/REAL/BLOB/INTEGER NULL/INTEGER NOT NULL).
func TestSqliteAdapter_Introspect_GenericSchema(t *testing.T) {
	ctx := context.Background()

	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	// Generic DDL — намеренно нейтральная (e-commerce минимум).
	ddl := []string{
		`CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			email TEXT NOT NULL,
			created_at TEXT
		)`,
		`CREATE TABLE items (
			id INTEGER PRIMARY KEY,
			sku TEXT NOT NULL,
			price REAL NOT NULL,
			metadata BLOB
		)`,
		`CREATE TABLE orders (
			id INTEGER PRIMARY KEY,
			customer_id INTEGER NOT NULL,
			item_id INTEGER NOT NULL,
			quantity INTEGER,
			FOREIGN KEY (customer_id) REFERENCES customers(id),
			FOREIGN KEY (item_id) REFERENCES items(id)
		)`,
	}
	for _, stmt := range ddl {
		if _, err := conn.ExecContext(ctx, stmt); err != nil {
			t.Fatalf("DDL %q: %v", stmt, err)
		}
	}

	got, err := (datasource.SqliteAdapter{}).Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}

	if got.Driver != "sqlite" {
		t.Errorf("Driver = %q, want %q", got.Driver, "sqlite")
	}
	if len(got.Tables) != 3 {
		t.Fatalf("len(Tables) = %d, want 3; got = %v", len(got.Tables), tableNames(got))
	}

	wantTables := map[string]struct {
		columns    []string
		primaryKey []string
		fkCount    int
	}{
		"customers": {
			columns:    []string{"id", "email", "created_at"},
			primaryKey: []string{"id"},
			fkCount:    0,
		},
		"items": {
			columns:    []string{"id", "sku", "price", "metadata"},
			primaryKey: []string{"id"},
			fkCount:    0,
		},
		"orders": {
			columns:    []string{"id", "customer_id", "item_id", "quantity"},
			primaryKey: []string{"id"},
			fkCount:    2, // → customers, → items
		},
	}

	byName := make(map[string]datasource.Table, len(got.Tables))
	for _, tbl := range got.Tables {
		byName[tbl.Name] = tbl
	}

	for name, want := range wantTables {
		tbl, ok := byName[name]
		if !ok {
			t.Errorf("table %q missing from introspection", name)
			continue
		}

		gotCols := make([]string, 0, len(tbl.Columns))
		for _, c := range tbl.Columns {
			gotCols = append(gotCols, c.Name)
		}
		if !equalStringSlices(gotCols, want.columns) {
			t.Errorf("table %q columns = %v, want %v", name, gotCols, want.columns)
		}

		if !equalStringSlices(tbl.PrimaryKey, want.primaryKey) {
			t.Errorf("table %q primary_key = %v, want %v", name, tbl.PrimaryKey, want.primaryKey)
		}

		if len(tbl.ForeignKeys) != want.fkCount {
			t.Errorf("table %q foreign_keys count = %d, want %d (got %v)",
				name, len(tbl.ForeignKeys), want.fkCount, tbl.ForeignKeys)
		}
	}

	// Проверяем маппинг типов на конкретных колонках.
	if tbl, ok := byName["items"]; ok {
		byCol := make(map[string]string, len(tbl.Columns))
		for _, c := range tbl.Columns {
			byCol[c.Name] = c.Type
		}
		if got := byCol["price"]; got != datasource.TypeFloat {
			t.Errorf("items.price type = %q, want %q", got, datasource.TypeFloat)
		}
		if got := byCol["metadata"]; got != datasource.TypeJSON {
			t.Errorf("items.metadata type = %q, want %q (BLOB→json per project convention)",
				got, datasource.TypeJSON)
		}
	}
}

// TestSqliteAdapter_Introspect_NullableDetection — проверяем что
// колонки без NOT NULL правильно помечаются как nullable=true.
func TestSqliteAdapter_Introspect_NullableDetection(t *testing.T) {
	ctx := context.Background()

	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	ddl := []string{
		`CREATE TABLE t (
			id INTEGER PRIMARY KEY,
			required TEXT NOT NULL,
			optional TEXT
		)`,
	}
	for _, stmt := range ddl {
		if _, err := conn.ExecContext(ctx, stmt); err != nil {
			t.Fatalf("DDL: %v", err)
		}
	}

	got, err := (datasource.SqliteAdapter{}).Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}
	if len(got.Tables) != 1 {
		t.Fatalf("expected 1 table, got %d", len(got.Tables))
	}

	byCol := make(map[string]datasource.Column, len(got.Tables[0].Columns))
	for _, c := range got.Tables[0].Columns {
		byCol[c.Name] = c
	}
	if byCol["required"].Nullable {
		t.Errorf("required should be nullable=false")
	}
	if !byCol["optional"].Nullable {
		t.Errorf("optional should be nullable=true")
	}
	if byCol["id"].Nullable {
		t.Errorf("id (PRIMARY KEY) should be nullable=false")
	}
}

// tableNames — утилита для диагностических сообщений.
func tableNames(s *datasource.Schema) []string {
	out := make([]string, 0, len(s.Tables))
	for _, tbl := range s.Tables {
		out = append(out, tbl.Name)
	}
	return out
}
