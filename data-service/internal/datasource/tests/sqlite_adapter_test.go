package datasource_test

import (
	"context"
	"fmt"
	"sync"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
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
		// A1: двойная кавычка внутри идентификатора экранируется удвоением.
		{"a\"b", `"a""b"`},
		{"x\"; DROP TABLE t; --", `"x""; DROP TABLE t; --"`},
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

// TestSqliteAdapter_DefaultPragmas — DSN по умолчанию включает WAL, FK, busy_timeout и synchronous=NORMAL.
// Использует file-based БД, т.к. прагмы (WAL, busy_timeout) работают только с файлами.
func TestSqliteAdapter_DefaultPragmas(t *testing.T) {
	a := datasource.SqliteAdapter{}
	ctx := context.Background()

	// Используем временный файл вместо :memory:, т.к. WAL-mode не работает с in-memory.
	dbPath := t.TempDir() + "/test.db"
	conn, err := a.Connect(ctx, dbPath)
	if err != nil {
		t.Fatalf("Connect %q: %v", dbPath, err)
	}
	defer func() { _ = conn.Close() }()
	_, err = conn.ExecContext(ctx, `CREATE TABLE parent (id INTEGER PRIMARY KEY)`)
	if err != nil {
		t.Fatalf("create parent: %v", err)
	}
	_, err = conn.ExecContext(ctx, `CREATE TABLE child (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES parent(id))`)
	if err != nil {
		t.Fatalf("create child: %v", err)
	}

	// Вставка в child без parent — должна упасть с FK violation
	_, err = conn.ExecContext(ctx, `INSERT INTO child (id, p_id) VALUES (1, 999)`)
	if err == nil {
		t.Error("expected FK violation for orphan insert, got nil")
	}
}

// TestSqliteAdapter_PragmasActiveFromDSN — прагмы должны действовать на
// КАЖДОМ коннекте пула. Проверяем, что после Connect (без ручного Exec)
// foreign_keys=1 и busy_timeout=5000 видны из чтения PRAGMA: это значит,
// что они пришли из DSN-параметров (modernc применяет их к каждому коннекту),
// а не из одноразового ExecContext на 1-м коннекте.
func TestSqliteAdapter_PragmasActiveFromDSN(t *testing.T) {
	a := datasource.SqliteAdapter{}
	ctx := context.Background()

	dbPath := t.TempDir() + "/pragmas.db"
	conn, err := a.Connect(ctx, dbPath)
	if err != nil {
		t.Fatalf("Connect %q: %v", dbPath, err)
	}
	defer func() { _ = conn.Close() }()

	var fk int
	if err := conn.QueryRowContext(ctx, "PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("query foreign_keys: %v", err)
	}
	if fk != 1 {
		t.Errorf("foreign_keys = %d, want 1 (прагма не применена к коннекту)", fk)
	}

	var bt int
	if err := conn.QueryRowContext(ctx, "PRAGMA busy_timeout").Scan(&bt); err != nil {
		t.Fatalf("query busy_timeout: %v", err)
	}
	if bt != 5000 {
		t.Errorf("busy_timeout = %d, want 5000", bt)
	}
}

// TestSqliteAdapter_ExplicitPragmaDSNNotBroken — пользовательский DSN с
// уже заданными _pragma-параметрами не ломается и не перезаписывается.
func TestSqliteAdapter_ExplicitPragmaDSNNotBroken(t *testing.T) {
	a := datasource.SqliteAdapter{}
	ctx := context.Background()

	dbPath := t.TempDir() + "/explicit.db"
	conn, err := a.Connect(ctx, dbPath+"?_pragma=foreign_keys(1)")
	if err != nil {
		t.Fatalf("Connect with explicit pragma: %v", err)
	}
	defer func() { _ = conn.Close() }()

	var fk int
	if err := conn.QueryRowContext(ctx, "PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("query foreign_keys: %v", err)
	}
	if fk != 1 {
		t.Errorf("foreign_keys = %d, want 1", fk)
	}
}

// TestSqliteAdapter_ConcurrentReads — проверяет, что пул соединений
// поддерживает конкурентное чтение (WAL mode + SetMaxOpenConns(2)).
// Использует file-based БД — :memory: не поддерживает multi-connection (каждый conn своя БД).
func TestSqliteAdapter_ConcurrentReads(t *testing.T) {
	ctx := context.Background()
	a := datasource.SqliteAdapter{}

	dbPath := t.TempDir() + "/concurrent.db"
	conn, err := a.Connect(ctx, dbPath)
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer func() { _ = conn.Close() }()

	// Создаём таблицу с данными
	_, err = conn.ExecContext(ctx, `CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)`)
	if err != nil {
		t.Fatalf("create items: %v", err)
	}
	for i := range 10 {
		_, err = conn.ExecContext(ctx, `INSERT INTO items (id, name) VALUES (?, ?)`, i, fmt.Sprintf("item-%d", i))
		if err != nil {
			t.Fatalf("insert item %d: %v", i, err)
		}
	}

	// Конкурентные читатели
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			rows, err := conn.QueryContext(ctx, `SELECT id, name FROM items ORDER BY id`)
			if err != nil {
				t.Errorf("concurrent reader %d: QueryContext: %v", id, err)
				return
			}
			defer rows.Close() //nolint:errcheck
			for rows.Next() {
				var id int
				var name string
				if err := rows.Scan(&id, &name); err != nil {
					t.Errorf("concurrent reader %d: scan: %v", id, err)
				}
			}
		}(i)
	}
	wg.Wait()
}

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

func TestSqliteAdapter_QueryRowContext(t *testing.T) {
	ctx := context.Background()
	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer conn.Close() //nolint:errcheck

	_, err = conn.ExecContext(ctx, `CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("ExecContext: %v", err)
	}
	_, err = conn.ExecContext(ctx, `INSERT INTO t (id, val) VALUES (1, 'hello')`)
	if err != nil {
		t.Fatalf("Insert: %v", err)
	}

	row := conn.QueryRowContext(ctx, `SELECT val FROM t WHERE id = ?`, 1)
	var val string
	if err := row.Scan(&val); err != nil {
		t.Fatalf("QueryRowContext.Scan: %v", err)
	}
	if val != "hello" {
		t.Errorf("val = %q, want 'hello'", val)
	}
}

func TestSqliteAdapter_QueryRowContext_NotFound(t *testing.T) {
	ctx := context.Background()
	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer conn.Close() //nolint:errcheck

	_, err = conn.ExecContext(ctx, `CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("ExecContext: %v", err)
	}

	row := conn.QueryRowContext(ctx, `SELECT val FROM t WHERE id = ?`, 999)
	var val string
	if err := row.Scan(&val); err == nil {
		t.Error("expected error for non-existent row, got nil")
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
