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

// itoa tests

func TestItoa(t *testing.T) {
	tests := []struct {
		n    int
		want string
	}{
		{0, "0"},
		{1, "1"},
		{42, "42"},
		{999, "999"},
		{-1, "-1"},
		{-42, "-42"},
		{123456789, "123456789"},
		{-987654321, "-987654321"},
	}
	for _, tc := range tests {
		got := itoa(tc.n)
		if got != tc.want {
			t.Errorf("itoa(%d) = %q, want %q", tc.n, got, tc.want)
		}
	}
}

// paramCountMismatchReason tests

func TestParamCountMismatchReason(t *testing.T) {
	tests := []struct {
		expected, got int
		want          string
	}{
		{2, 1, "arg count mismatch: query expects 2 params, got 1"},
		{0, 5, "arg count mismatch: query expects 0 params, got 5"},
		{3, 0, "arg count mismatch: query expects 3 params, got 0"},
		{-1, 0, "arg count mismatch: query expects -1 params, got 0"},
	}
	for _, tc := range tests {
		got := paramCountMismatchReason(tc.expected, tc.got)
		if got != tc.want {
			t.Errorf("paramCountMismatchReason(%d,%d) = %q, want %q",
				tc.expected, tc.got, got, tc.want)
		}
	}
}

// quote tests

func TestQuote(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"hello", `"hello"`},
		{"", `""`},
		{"a b c", `"a b c"`},
		{"table_name", `"table_name"`},
	}
	for _, tc := range tests {
		got := quote(tc.input)
		if got != tc.want {
			t.Errorf("quote(%q) = %s, want %s", tc.input, got, tc.want)
		}
	}
}

// summarizeSQL tests

func TestSummarizeSQL(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"SELECT 1", `"SELECT 1"`},
		{"", `""`},
		{"  SELECT 1  ", `"SELECT 1"`},
	}
	for _, tc := range tests {
		got := summarizeSQL(tc.input)
		if got != tc.want {
			t.Errorf("summarizeSQL(%q) = %s, want %s", tc.input, got, tc.want)
		}
	}
}

func TestSummarizeSQL_Long(t *testing.T) {
	longSQL := "SELECT very_long_column_name, another_long_column, yet_another FROM somewhere WHERE id = ?"
	got := summarizeSQL(longSQL)
	if len(got) > 65 {
		t.Errorf("summarizeSQL too long: %d chars: %s", len(got), got)
	}
	if !hasSuffix(got, "...\"") {
		t.Errorf("summarizeSQL long should end with ...\", got: %s", got)
	}
}

// hasSuffix — local helper for test
func hasSuffix(s, suffix string) bool {
	if len(s) < len(suffix) {
		return false
	}
	return s[len(s)-len(suffix):] == suffix
}

// hasStandaloneWord tests

func TestHasStandaloneWord(t *testing.T) {
	tests := []struct {
		s, word string
		want    bool
	}{
		// Basic cases
		{"SELECT * FROM t", "SELECT", true},
		{"SELECT * FROM t", "SELECT", true},
		{"SELECT", "SELECT", true},
		{"", "SELECT", false},
		// Part of identifier — underscore protection
		{"SELECT_INTO x", "SELECT", false},
		{"INSERT_INTO x", "INSERT", false},
		// Part of word
		{"UNSELECTED", "SELECT", false},
		{"UPSERT", "INSERT", false},
		// Multiple keywords
		{"DELETE FROM t WHERE id = 1", "DELETE", true},
		{"DELETE FROM t WHERE id = 1", "UPDATE", false},
		// Edge: word at start, word at end
		{"DROP TABLE t", "DROP", true},
		{"EXECUTE proc", "EXECUTE", true},
		{"EXEC proc", "EXEC", true},
	}
	for _, tc := range tests {
		got := hasStandaloneWord(tc.s, tc.word)
		if got != tc.want {
			t.Errorf("hasStandaloneWord(%q, %q) = %v, want %v",
				tc.s, tc.word, got, tc.want)
		}
	}
}

// isIdentChar tests

func TestIsIdentChar(t *testing.T) {
	tests := []struct {
		c    byte
		want bool
	}{
		// letters
		{'a', true}, {'z', true}, {'A', true}, {'Z', true},
		// digits
		{'0', true}, {'9', true},
		// underscore
		{'_', true},
		// non-ident chars
		{' ', false}, {'.', false}, {'(', false}, {')', false},
		{'"', false}, {';', false}, {'-', false}, {'/', false},
		{'*', false},
	}
	for _, tc := range tests {
		got := isIdentChar(tc.c)
		if got != tc.want {
			t.Errorf("isIdentChar(%q) = %v, want %v", tc.c, got, tc.want)
		}
	}
}

// columnFor tests

func TestColumnFor_Found(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "identifier"},
			{Name: "email", Column: "email_address"},
		},
	}
	col, ok := b.columnFor(entity, "email")
	if !ok {
		t.Error("columnFor: expected ok=true")
	}
	if col != "email_address" {
		t.Errorf("columnFor = %q, want %q", col, "email_address")
	}
}

func TestColumnFor_NotFound(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id"},
		},
	}
	_, ok := b.columnFor(entity, "nonexistent")
	if ok {
		t.Error("columnFor: expected ok=false")
	}
}

func TestColumnFor_EmptyFields(t *testing.T) {
	b := &Builder{}
	entity := Entity{Fields: []EntityField{}}
	_, ok := b.columnFor(entity, "anything")
	if ok {
		t.Error("columnFor with empty fields: expected ok=false")
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
func (m *mockAdapter) QuoteIdentifier(name string) string {
	return m.qid + name + m.qid
}
func (m *mockAdapter) TranslatePlaceholder(index int) string {
	return m.ph
}
func (m *mockAdapter) PingContext(ctx context.Context) error {
	return nil
}
