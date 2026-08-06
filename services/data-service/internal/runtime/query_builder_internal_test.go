package runtime

import (
	"context"
	"database/sql"
	"testing"
)

// buildColumnList tests

func TestBuildColumnList_WithFields(t *testing.T) {
	adapter := &mockAdapter{qid: `"`, ph: "?"}
	entity := Entity{
		Table: "customers",
		Fields: []EntityField{
			{Name: "id", Column: "id"},
			{Name: "email", Column: "email"},
		},
	}
	got := buildColumnList(adapter, entity)
	want := `"id", "email"`
	if got != want {
		t.Errorf("buildColumnList = %q, want %q", got, want)
	}
}

func TestBuildColumnList_EmptyFields(t *testing.T) {
	adapter := &mockAdapter{qid: `"`, ph: "?"}
	entity := Entity{Table: "customers", Fields: []EntityField{}}
	got := buildColumnList(adapter, entity)
	if got != "*" {
		t.Errorf("buildColumnList with empty Fields = %q, want %q", got, "*")
	}
}

func TestBuildColumnList_NilFields(t *testing.T) {
	adapter := &mockAdapter{qid: `"`, ph: "?"}
	entity := Entity{Table: "customers"}
	got := buildColumnList(adapter, entity)
	if got != "*" {
		t.Errorf("buildColumnList with nil Fields = %q, want %q", got, "*")
	}
}

// mockAdapter — minimal stub implementing AdapterSubset for internal tests.
// Does not open a real database — only QuoteIdentifier and TranslatePlaceholder are used.
type mockAdapter struct {
	qid string
	ph  string
}

func (m *mockAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return nil, nil
}

func (m *mockAdapter) PingContext(ctx context.Context) error {
	return nil
}

func (m *mockAdapter) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return nil
}

func (m *mockAdapter) QuoteIdentifier(s string) string {
	return m.qid + s + m.qid
}

func (m *mockAdapter) TranslatePlaceholder(i int) string {
	return m.ph
}
