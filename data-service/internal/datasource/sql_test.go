package datasource

import (
	"context"
	"database/sql"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestSQLDataSource_Schema_Works verifies that the Schema() method
// on SQLDataSource returns correct metadata for a real SQLite database.
//
// This is the ONLY live method on SQLDataSource — it's called from
// endpoint_builder.go via DataSourceHandler with method="schema".
func TestSQLDataSource_Schema_Works(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE items (
		id   TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		price INT DEFAULT 0
	)`)
	if err != nil {
		t.Fatalf("CREATE TABLE: %v", err)
	}

	_, err = db.Exec(`INSERT INTO items VALUES ('a','Apple',100),('b','Banana',50),('c','Cherry',300)`)
	if err != nil {
		t.Fatalf("INSERT: %v", err)
	}

	// Build a minimal adapter that wraps *sql.DB
	adapter := &testQuerierAdapter{db: db}

	entity := config.Entity{
		Name:     "items",
		Table:    "items",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeString, PrimaryKey: boolPtr(true)},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "price", Column: "price", Type: config.FieldTypeInt},
		},
	}

	ds := NewSQLDataSource(db, adapter, []config.Entity{entity}, 0)

	ctx := context.Background()
	info, err := ds.Schema(ctx, "items")
	if err != nil {
		t.Fatalf("Schema: %v", err)
	}

	if info == nil {
		t.Fatal("Schema returned nil")
	}
	if info.Entity != "items" {
		t.Errorf("Entity = %q, want %q", info.Entity, "items")
	}
	if info.Total != 3 {
		t.Errorf("Total = %d, want 3", info.Total)
	}
	if info.Fields == nil {
		t.Fatal("Fields is nil")
	}

	// name field should have distinct values
	nameMeta, ok := info.Fields["name"]
	if !ok {
		t.Errorf("Fields missing 'name', got keys: %v", fieldKeys(info.Fields))
	} else {
		if nameMeta.Type != "string" {
			t.Errorf("name.Type = %q, want %q", nameMeta.Type, "string")
		}
		if len(nameMeta.Distinct) != 3 {
			t.Errorf("name.Distinct = %v, want 3 values", nameMeta.Distinct)
		}
		hasApple := false
		for _, v := range nameMeta.Distinct {
			if v == "Apple" {
				hasApple = true
				break
			}
		}
		if !hasApple {
			t.Errorf("name.Distinct should contain 'Apple', got %v", nameMeta.Distinct)
		}
	}

	// price field should have min/max
	priceMeta, ok := info.Fields["price"]
	if !ok {
		t.Errorf("Fields missing 'price', got keys: %v", fieldKeys(info.Fields))
	} else {
		if priceMeta.Type != "int" {
			t.Errorf("price.Type = %q, want %q", priceMeta.Type, "int")
		}
		if priceMeta.Min == nil {
			t.Error("price.Min is nil, want 50")
		} else if *priceMeta.Min != 50 {
			t.Errorf("price.Min = %v, want 50", *priceMeta.Min)
		}
		if priceMeta.Max == nil {
			t.Error("price.Max is nil, want 300")
		} else if *priceMeta.Max != 300 {
			t.Errorf("price.Max = %v, want 300", *priceMeta.Max)
		}
	}
}

// TestSQLDataSource_Type verifies the data source type identifier.
func TestSQLDataSource_Type(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close()

	ds := NewSQLDataSource(db, &testQuerierAdapter{db: db}, nil, 0)
	if ds.Type() != "sql" {
		t.Errorf("Type = %q, want %q", ds.Type(), "sql")
	}
}

// TestSQLDataSource_EntityNotFound verifies that querying a non-existent
// entity returns a clear error.
func TestSQLDataSource_EntityNotFound(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close()

	ds := NewSQLDataSource(db, &testQuerierAdapter{db: db}, nil, 0)
	_, err = ds.Schema(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error for nonexistent entity")
	}
	if !strings.Contains(err.Error(), `entity "nonexistent" not found`) {
		t.Errorf("error = %v, want entity not found", err)
	}
}

// testQuerierAdapter implements query.AdapterSubset for SQLite.
type testQuerierAdapter struct {
	db *sql.DB
}

func (a *testQuerierAdapter) TranslatePlaceholder(index int) string {
	return "?"
}
func (a *testQuerierAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}
func (a *testQuerierAdapter) QuoteString(s string) string {
	escaped := ""
	for _, c := range s {
		if c == '%' || c == '_' {
			escaped += "\\"
		}
		escaped += string(c)
	}
	return escaped
}

// Ensure testQuerierAdapter satisfies query.AdapterSubset.
var _ query.AdapterSubset = (*testQuerierAdapter)(nil)

// Helper: bool pointer for config.EntityField.PrimaryKey.
func boolPtr(b bool) *bool {
	return &b
}

// Helper: extract keys from FieldMeta map.
func fieldKeys(m map[string]FieldMeta) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}
