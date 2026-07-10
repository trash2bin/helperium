package runtime

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

func TestResponseMapper_PublicFor_Found(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id_col"},
			{Name: "email", Column: "email_address"},
		},
	}

	name, ok := b.publicFor(entity, "email_address")
	if !ok {
		t.Fatal("publicFor('email_address'): expected ok=true")
	}
	if name != "email" {
		t.Errorf("publicFor = %q, want %q", name, "email")
	}
}

func TestResponseMapper_PublicFor_NotFound(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id_col"},
		},
	}

	_, ok := b.publicFor(entity, "nope")
	if ok {
		t.Error("publicFor('nope'): expected ok=false")
	}
}

func TestResponseMapper_PublicFor_EmptyFields(t *testing.T) {
	b := &Builder{}
	entity := Entity{Fields: []EntityField{}}

	_, ok := b.publicFor(entity, "anything")
	if ok {
		t.Error("publicFor with empty fields: expected ok=false")
	}
}

func TestFieldTypeFor_Found(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Type: "int"},
			{Name: "name", Type: "string"},
			{Name: "score", Type: "float"},
			{Name: "active", Type: "bool"},
			{Name: "data", Type: "json"},
		},
	}

	tests := []struct {
		field string
		want  string
	}{
		{"id", "int"},
		{"name", "string"},
		{"score", "float"},
		{"active", "bool"},
		{"data", "json"},
	}
	for _, tc := range tests {
		got := b.fieldTypeFor(entity, tc.field)
		if got != tc.want {
			t.Errorf("fieldTypeFor(%q) = %q, want %q", tc.field, got, tc.want)
		}
	}
}

func TestFieldTypeFor_NotFound(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Type: "int"},
		},
	}

	got := b.fieldTypeFor(entity, "nonexistent")
	if got != "" {
		t.Errorf("fieldTypeFor('nonexistent') = %q, want ''", got)
	}
}

func TestFieldTypeFor_EmptyFields(t *testing.T) {
	b := &Builder{}
	entity := Entity{Fields: []EntityField{}}

	got := b.fieldTypeFor(entity, "anything")
	if got != "" {
		t.Errorf("fieldTypeFor with empty fields = %q, want ''", got)
	}
}

// TestMapRow_WithPublicMapping runs MapRow against a real SQLite DB
// to ensure column→public name mapping and type coercion both work.
func TestMapRow_WithPublicMapping(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx,
		`CREATE TABLE test_table (id INTEGER PRIMARY KEY, full_name TEXT, points REAL)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	_, err = db.ExecContext(ctx,
		`INSERT INTO test_table (id, full_name, points) VALUES (?, ?, ?)`,
		42, "Alice", 95.5)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:     "test",
		Table:    "test_table",
		IDColumn: "id",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "full_name", Type: "string"},
			{Name: "score", Column: "points", Type: "float"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, full_name, points FROM test_table`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	row, err := b.MapRow(rows, entity)
	if err != nil {
		t.Fatalf("MapRow: %v", err)
	}

	// Check public names, NOT column names
	if row["id"] != 42 {
		t.Errorf("id = %v (%T), want 42 (int)", row["id"], row["id"])
	}
	if row["name"] != "Alice" {
		t.Errorf("name = %v, want Alice", row["name"])
	}
	if row["score"] != 95.5 {
		t.Errorf("score = %v, want 95.5", row["score"])
	}

	// Must NOT contain raw column name
	if _, ok := row["full_name"]; ok {
		t.Error("row should not contain DB column name 'full_name', got ok=true")
	}
}

// TestMapRows_LimitAndFullIteration tests MapRows with different maxRows values.
func TestMapRows_LimitAndFullIteration(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	for i := 1; i <= 5; i++ {
		_, err = db.ExecContext(ctx, `INSERT INTO items (id, val) VALUES (?, ?)`, i, "x")
		if err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "item",
		Table: "items",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
			{Name: "val", Column: "val", Type: "string"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, val FROM items ORDER BY id`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}

	mapper := func(r *sql.Rows) (map[string]any, error) {
		return b.MapRow(r, entity)
	}

	out, err := b.MapRows(rows, mapper, 2)
	if err != nil {
		t.Fatalf("MapRows: %v", err)
	}
	if len(out) != 2 {
		t.Errorf("MapRows maxRows=2: got %d rows, want 2", len(out))
	}
}

func TestMapRows_ZeroLimit(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	for i := 1; i <= 3; i++ {
		if _, err = db.ExecContext(ctx, `INSERT INTO items (id, val) VALUES (?, ?)`, i, "x"); err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "item",
		Table: "items",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id FROM items ORDER BY id`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}

	mapper := func(r *sql.Rows) (map[string]any, error) {
		return b.MapRow(r, entity)
	}

	out, err := b.MapRows(rows, mapper, 0)
	if err != nil {
		t.Fatalf("MapRows: %v", err)
	}
	if len(out) != 3 {
		t.Errorf("MapRows maxRows=0: got %d rows, want 3", len(out))
	}
}

// TestMapRow_NilColumn tests MapRow with NULL in the DB.
func TestMapRow_NilColumn(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE nullable (id INTEGER PRIMARY KEY, val TEXT, score REAL)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	_, err = db.ExecContext(ctx,
		`INSERT INTO nullable (id, val, score) VALUES (?, ?, ?)`, 1, nil, nil)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "nullable",
		Table: "nullable",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
			{Name: "val", Column: "val", Type: "string", Nullable: true},
			{Name: "score", Column: "score", Type: "float", Nullable: true},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, val, score FROM nullable`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	row, err := b.MapRow(rows, entity)
	if err != nil {
		t.Fatalf("MapRow: %v", err)
	}

	if row["val"] != nil {
		t.Errorf("val = %v, want nil", row["val"])
	}
	if row["score"] != nil {
		t.Errorf("score = %v, want nil", row["score"])
	}
}

// internalTestAdapter — local adapter for internal tests.
type internalTestAdapter struct {
	db *sql.DB
}

func (a *internalTestAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *internalTestAdapter) QuoteIdentifier(name string) string { return `"` + name + `"` }
func (a *internalTestAdapter) TranslatePlaceholder(idx int) string { return "?" }
func (a *internalTestAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }
