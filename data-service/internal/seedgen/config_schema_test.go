package seedgen

import (
	"strings"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
)

// boolPtr returns a pointer to the given bool. Helper for building
// *bool fields on config.EntityField in tests.
func boolPtr(b bool) *bool { return &b }

// pkEntity builds a minimal config.Entity with the given table name,
// primary-key column name(s), id_column, and field definitions.
// fieldsAndPK is a slice of (colName, isPK) pairs. id_column is used
// only when zero fields are flagged.
func pkEntity(table, idColumn string, fieldsAndPK []struct {
	Col  string
	IsPK bool
}) config.Entity {
	fields := make([]config.EntityField, 0, len(fieldsAndPK))
	for _, f := range fieldsAndPK {
		fields = append(fields, config.EntityField{
			Name:       f.Col,
			Column:     f.Col,
			Type:       config.FieldTypeInt,
			Nullable:   boolPtr(false),
			PrimaryKey: boolPtr(f.IsPK),
		})
	}
	return config.Entity{
		Name:     table,
		Table:    table,
		IDColumn: idColumn,
		Fields:   fields,
	}
}

// countPKClauses returns the number of `PRIMARY KEY` substrings in ddl.
// Each clause inside `PRIMARY KEY (...)` counts as one, regardless of how
// many columns are inside.
func countPKClauses(ddl string) int {
	return strings.Count(ddl, "PRIMARY KEY")
}

// hasInlinePKOnColumn checks whether ddl contains the bug pattern:
// a column definition that ends with " PRIMARY KEY" inline (i.e. the
// last token on the line). Detected by looking for " PRIMARY KEY"
// followed by either newline or comma (the next DDL item).
func hasInlinePKOnColumn(ddl string) bool {
	return strings.Contains(ddl, " PRIMARY KEY\n") ||
		strings.Contains(ddl, " PRIMARY KEY,")
}

// TestGenerateDDL covers the composite-primary-key bug that previously
// caused PostgreSQL to reject materialization of the `stress` scenario
// (`product_tags` table has two PK columns).
//
// The fix:
//   - PK-flagged columns are collected, then emitted as a single
//     `PRIMARY KEY (col1, col2, ...)` table constraint after the columns.
//   - Inline ` PRIMARY KEY` per column is removed.
//   - Fallback to `id_column` is preserved when no field is flagged.
func TestGenerateDDL(t *testing.T) {
	t.Run("CompositePK_SQLite", func(t *testing.T) {
		entities := []config.Entity{
			pkEntity("product_tags", "product_id", []struct {
				Col  string
				IsPK bool
			}{
				{"product_id", true},
				{"tag", true},
			}),
		}
		ddl, err := GenerateDDL(entities, "sqlite")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if got, want := countPKClauses(ddl), 1; got != want {
			t.Errorf("PRIMARY KEY clause count = %d, want %d\nDDL:\n%s", got, want, ddl)
		}
		if hasInlinePKOnColumn(ddl) {
			t.Errorf("inline ` PRIMARY KEY` on column detected (bug regressed):\n%s", ddl)
		}
		if !strings.Contains(ddl, `PRIMARY KEY ("product_id", "tag")`) {
			t.Errorf("expected composite PRIMARY KEY (\"product_id\", \"tag\") in DDL:\n%s", ddl)
		}
	})

	t.Run("CompositePK_Postgres", func(t *testing.T) {
		entities := []config.Entity{
			pkEntity("product_tags", "product_id", []struct {
				Col  string
				IsPK bool
			}{
				{"product_id", true},
				{"tag", true},
			}),
		}
		ddl, err := GenerateDDL(entities, "postgres")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if got, want := countPKClauses(ddl), 1; got != want {
			t.Errorf("PRIMARY KEY clause count = %d, want %d\nDDL:\n%s", got, want, ddl)
		}
		if hasInlinePKOnColumn(ddl) {
			t.Errorf("inline ` PRIMARY KEY` on column detected (bug regressed):\n%s", ddl)
		}
		if !strings.Contains(ddl, `PRIMARY KEY ("product_id", "tag")`) {
			t.Errorf("expected composite PRIMARY KEY (\"product_id\", \"tag\") in DDL:\n%s", ddl)
		}
	})

	t.Run("SingleFlaggedPK", func(t *testing.T) {
		entities := []config.Entity{
			pkEntity("users", "id", []struct {
				Col  string
				IsPK bool
			}{
				{"id", true},
				{"name", false},
			}),
		}
		ddl, err := GenerateDDL(entities, "postgres")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if got, want := countPKClauses(ddl), 1; got != want {
			t.Errorf("PRIMARY KEY clause count = %d, want %d (must NOT be 2 — no `(id, id)`)\nDDL:\n%s", got, want, ddl)
		}
		if !strings.Contains(ddl, `PRIMARY KEY ("id")`) {
			t.Errorf("expected PRIMARY KEY (\"id\") in DDL:\n%s", ddl)
		}
		if hasInlinePKOnColumn(ddl) {
			t.Errorf("inline ` PRIMARY KEY` on column detected:\n%s", ddl)
		}
	})

	t.Run("FallbackToIDColumn", func(t *testing.T) {
		// Mirrors the stress scenario's `sqitch_*` migration tables:
		// no field is flagged, but `id_column = "version"` must still produce a PK.
		entities := []config.Entity{
			pkEntity("sqitch_test", "version", []struct {
				Col  string
				IsPK bool
			}{
				{"version", false},
				{"description", false},
			}),
		}
		ddl, err := GenerateDDL(entities, "postgres")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if got, want := countPKClauses(ddl), 1; got != want {
			t.Errorf("PRIMARY KEY clause count = %d, want %d\nDDL:\n%s", got, want, ddl)
		}
		if !strings.Contains(ddl, `PRIMARY KEY ("version")`) {
			t.Errorf("expected PRIMARY KEY (\"version\") fallback in DDL:\n%s", ddl)
		}
	})

	t.Run("NoPKAtAll", func(t *testing.T) {
		// Smoke test: if no field is flagged and id_column is set (without
		// matching any field), the generator falls back to id_column-based
		// PRIMARY KEY. This is the same code path as the sqitch_* tables.
		entities := []config.Entity{
			pkEntity("loose", "loose_id", []struct {
				Col  string
				IsPK bool
			}{
				{"loose_id", false},
				{"x", false},
			}),
		}
		ddl, err := GenerateDDL(entities, "postgres")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if !strings.Contains(ddl, "CREATE TABLE") {
			t.Errorf("expected CREATE TABLE in DDL:\n%s", ddl)
		}
		if !strings.Contains(ddl, `PRIMARY KEY ("loose_id")`) {
			t.Errorf("expected fallback PRIMARY KEY (\"loose_id\") from id_column:\n%s", ddl)
		}
	})

	t.Run("EmptyEntities", func(t *testing.T) {
		if _, err := GenerateDDL(nil, "postgres"); err == nil {
			t.Error("expected error for empty entities slice")
		}
	})

	t.Run("EntityWithoutIDColumn_IsSkipped", func(t *testing.T) {
		// Document current behavior: an entity with empty id_column and
		// no flagged PK fields is skipped entirely (no CREATE TABLE emitted).
		// This guards against regressions in the early-return guard.
		entities := []config.Entity{
			{
				Name:     "loose",
				Table:    "loose",
				IDColumn: "",
				Fields: []config.EntityField{
					{Name: "x", Column: "x", Type: config.FieldTypeString, Nullable: boolPtr(true)},
				},
			},
		}
		ddl, err := GenerateDDL(entities, "postgres")
		if err != nil {
			t.Fatalf("GenerateDDL: %v", err)
		}
		if strings.Contains(ddl, "CREATE TABLE") {
			t.Errorf("entity with empty id_column should be skipped, got DDL:\n%s", ddl)
		}
	})
}
