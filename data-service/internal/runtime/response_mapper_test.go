package runtime

import (
	"context"
	"database/sql"
	"encoding/json"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

// testRuntimeAdapter — минимальный AdapterSubset для тестов в package runtime.
type testRuntimeAdapter struct {
	db *sql.DB
}

func (a *testRuntimeAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *testRuntimeAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (a *testRuntimeAdapter) TranslatePlaceholder(idx int) string   { return "?" }
func (a *testRuntimeAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }

func newRuntimeTestAdapter(t *testing.T) (*testRuntimeAdapter, func()) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, created_at TEXT);
	`); err != nil {
		t.Fatalf("create table: %v", err)
	}

	return &testRuntimeAdapter{db: db}, func() { _ = db.Close() }
}

func TestCoerceValue(t *testing.T) {
	tests := []struct {
		val, typ string
		want     any
	}{
		// int
		{"42", "int", 42},
		{"0", "int", 0},
		{"notanumber", "int", "notanumber"},

		// float
		{"3.14", "float", 3.14},
		{"0.0", "float", 0.0},
		{"badfloat", "float", "badfloat"},

		// bool
		{"true", "bool", true},
		{"false", "bool", false},
		{"1", "bool", true},
		{"0", "bool", false},
		{"yes", "bool", "yes"},

		// json — массив
		{`["a","b"]`, "json", []any{"a", "b"}},
		// json — объект
		{`{"key":"val"}`, "json", map[string]any{"key": "val"}},
		// json — невалидный
		{`{bad`, "json", `{bad`},

		// string / unknown type
		{"hello", "string", "hello"},
		{"anything", "unknown_type", "anything"},

		// empty
		{"", "int", ""},
		{"", "json", ""},
	}

	for _, tc := range tests {
		got := coerceValue(tc.val, tc.typ)
		wantJSON, _ := json.Marshal(tc.want)
		gotJSON, _ := json.Marshal(got)
		if string(wantJSON) != string(gotJSON) {
			t.Errorf("coerceValue(%q, %q) = %v (%T), want %v (%T)",
				tc.val, tc.typ, got, got, tc.want, tc.want)
		}
	}
}

func TestMapCustomQueryRow(t *testing.T) {
	adapter, cleanup := newRuntimeTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	db := adapter.db

	if _, err := db.ExecContext(ctx, `
		CREATE TABLE stats (id INTEGER PRIMARY KEY, name TEXT, score REAL, active INTEGER);
	`); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.ExecContext(ctx,
		`INSERT INTO stats (id, name, score, active) VALUES (?, ?, ?, ?)`,
		1, "alice", 95.5, 1,
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	b := NewBuilder(adapter)

	rows, err := adapter.QueryContext(ctx, `SELECT id, name, score, active FROM stats`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	mapping := map[string]ResultMappingField{
		"id":     {Type: "int"},
		"name":   {Type: "string"},
		"score":  {Type: "float"},
		"active": {Type: "bool"},
	}

	row, err := b.MapCustomQueryRow(rows, mapping)
	if err != nil {
		t.Fatalf("MapCustomQueryRow: %v", err)
	}

	if row["id"] != 1 {
		t.Errorf("id = %v (%T), want 1 (int)", row["id"], row["id"])
	}
	if row["name"] != "alice" {
		t.Errorf("name = %v, want alice", row["name"])
	}
	if row["score"] != 95.5 {
		t.Errorf("score = %v, want 95.5", row["score"])
	}
	if row["active"] != true {
		t.Errorf("active = %v, want true", row["active"])
	}
}

func TestMapCustomQueryRow_NilValue(t *testing.T) {
	adapter, cleanup := newRuntimeTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	db := adapter.db

	if _, err := db.ExecContext(ctx, `
		CREATE TABLE nullable_data (id INTEGER PRIMARY KEY, val TEXT);
	`); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.ExecContext(ctx,
		`INSERT INTO nullable_data (id, val) VALUES (?, ?)`, 1, nil,
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	b := NewBuilder(adapter)

	rows, err := adapter.QueryContext(ctx, `SELECT id, val FROM nullable_data`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	mapping := map[string]ResultMappingField{
		"id":  {Type: "int"},
		"val": {Type: "string", Nullable: true},
	}

	row, err := b.MapCustomQueryRow(rows, mapping)
	if err != nil {
		t.Fatalf("MapCustomQueryRow: %v", err)
	}

	if row["val"] != nil {
		t.Errorf("val = %v, want nil", row["val"])
	}
}

func TestMapCustomQueryRow_NoMapping(t *testing.T) {
	adapter, cleanup := newRuntimeTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	db := adapter.db

	if _, err := db.ExecContext(ctx, `
		CREATE TABLE raw_data (id INTEGER PRIMARY KEY, raw TEXT);
	`); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.ExecContext(ctx,
		`INSERT INTO raw_data (id, raw) VALUES (?, ?)`, 1, "hello",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	b := NewBuilder(adapter)

	rows, err := adapter.QueryContext(ctx, `SELECT id, raw FROM raw_data`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	// No mapping — все значения возвращаются как строки
	row, err := b.MapCustomQueryRow(rows, nil)
	if err != nil {
		t.Fatalf("MapCustomQueryRow: %v", err)
	}

	if row["id"] != "1" {
		t.Errorf("id = %v (%T), want '1' (string)", row["id"], row["id"])
	}
	if row["raw"] != "hello" {
		t.Errorf("raw = %v, want hello", row["raw"])
	}
}

func TestMapCustomQueryRow_PartialMapping(t *testing.T) {
	adapter, cleanup := newRuntimeTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	db := adapter.db

	if _, err := db.ExecContext(ctx, `
		CREATE TABLE mixed (id INTEGER PRIMARY KEY, name TEXT, extra TEXT);
	`); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.ExecContext(ctx,
		`INSERT INTO mixed (id, name, extra) VALUES (?, ?, ?)`, 1, "bob", "metadata",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	b := NewBuilder(adapter)

	rows, err := adapter.QueryContext(ctx, `SELECT id, name, extra FROM mixed`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	// Только id и name в маппинге; extra — без маппинга → строка
	mapping := map[string]ResultMappingField{
		"id":   {Type: "int"},
		"name": {Type: "string"},
	}

	row, err := b.MapCustomQueryRow(rows, mapping)
	if err != nil {
		t.Fatalf("MapCustomQueryRow: %v", err)
	}

	if row["id"] != 1 {
		t.Errorf("id = %v (%T), want 1 (int)", row["id"], row["id"])
	}
	if row["name"] != "bob" {
		t.Errorf("name = %v, want bob", row["name"])
	}
	if row["extra"] != "metadata" {
		t.Errorf("extra = %v, want metadata (string)", row["extra"])
	}
}

// ── entity resolver tests ──

func TestNewEntityResolver_Duplicate(t *testing.T) {
	entities := []Entity{
		{Name: "dup", Table: "dups1", IDColumn: "id", Fields: []EntityField{{Name: "id", Column: "id", Type: "int", PrimaryKey: true}}},
		{Name: "dup", Table: "dups2", IDColumn: "id", Fields: []EntityField{{Name: "id", Column: "id", Type: "int", PrimaryKey: true}}},
	}

	_, err := NewEntityResolver(entities)
	if err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Errorf("expected duplicate error, got: %v", err)
	}
}
