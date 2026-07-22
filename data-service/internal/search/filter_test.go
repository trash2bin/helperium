package search

import (
	"fmt"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/query"
)

// =============================================================================
// Tests: FilterStrategy
// =============================================================================

func TestFilterStrategy_Name(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	if got := s.Name(); got != "filter" {
		t.Errorf("Name() = %q, want %q", got, "filter")
	}
}

func TestFilterStrategy_ToolName(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	if got := s.ToolName(sampleEntity); got != "filter_products" {
		t.Errorf("ToolName() = %q, want %q", got, "filter_products")
	}
}

func TestFilterStrategy_NoFilters_ReturnsError(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("ParseRequest: expected error for empty filter, got nil")
	}
	if !strings.Contains(err.Error(), "at least one filter parameter") {
		t.Errorf("Unexpected error: %v", err)
	}
}

func TestFilterStrategy_ExactMatch(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "category" = ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "electronics" {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestFilterStrategy_ExactMatchNumeric(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"price": "99.99"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "price" = ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	// price is float, so 99.99 should be parsed as float64
	if _, ok := args[0].(float64); !ok {
		t.Errorf("price arg should be float64, got %T: %v", args[0], args[0])
	}
}

func TestFilterStrategy_ComparisonOpGt(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"price__gt": "1000"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "price" > ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 2 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestFilterStrategy_ComparisonOps(t *testing.T) {
	tests := []struct {
		param   string
		wantOp  string
		wantVal any
	}{
		{"price__gt", ">", float64(100)},
		{"price__gte", ">=", float64(100)},
		{"price__lt", "<", float64(100)},
		{"price__lte", "<=", float64(100)},
	}

	s := NewFilterStrategy("id", "name")
	for _, tt := range tests {
		t.Run(tt.param, func(t *testing.T) {
			r := makeRequest(map[string]string{tt.param: "100"})
			plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
			if err != nil {
				t.Fatalf("ParseRequest: unexpected error: %v", err)
			}

			sql, args, err := buildSQL(plan, testAdapter{})
			if err != nil {
				t.Fatalf("buildSQL: unexpected error: %v", err)
			}

			wantSQL := fmt.Sprintf(`SELECT "id", "name" FROM "products" WHERE "price" %s ? LIMIT ?`, tt.wantOp)
			if sql != wantSQL {
				t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
			}
			if len(args) < 1 {
				t.Fatal("No args")
			}
		})
	}
}

func TestFilterStrategy_LikeOp(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"name__like": "%muffler%"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "name" COLLATE NOCASE LIKE ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "%muffler%" {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestFilterStrategy_InOp(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category__in": "a,b,c"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	// Use postgres adapter to verify IN placeholders.
	eng := query.NewEngine(wrapAdapter{a: testAdapter{}})
	sql, args, err := eng.Build(*plan)
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "category" IN (?, ?, ?) LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 4 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestFilterStrategy_LimitDefault(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "x"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Limit != 10 {
		t.Errorf("Limit = %d, want 10", plan.Limit)
	}
}

func TestFilterStrategy_CustomLimit(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "x", "limit": "50"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Limit != 50 {
		t.Errorf("Limit = %d, want 50", plan.Limit)
	}
}

func TestFilterStrategy_SortBy(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "x", "sort_by": "-price"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	if !contains(sql, `ORDER BY "price" DESC`) {
		t.Errorf("Expected ORDER BY price DESC: %q", sql)
	}
}

func TestFilterStrategy_MultipleConditions(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{
		"category":   "electronics",
		"active":     "true",
		"price__gte": "100",
	})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// All three conditions should appear.
	if !contains(sql, `"category" = ?`) {
		t.Errorf("Missing category condition: %q", sql)
	}
	if !contains(sql, `"active" = ?`) {
		t.Errorf("Missing active condition: %q", sql)
	}
	if !contains(sql, `"price" >= ?`) {
		t.Errorf("Missing price>= condition: %q", sql)
	}
}

func TestFilterStrategy_SkipsPKField(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"id": "5"})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("ParseRequest: expected error (PK only = no filter), got nil")
	}
	if !strings.Contains(err.Error(), "at least one filter parameter") {
		t.Errorf("Unexpected error: %v", err)
	}
}

func TestFilterStrategy_FormatFull(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "x", "format": "full"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Format != query.FormatFull {
		t.Errorf("Format = %d, want FormatFull", plan.Format)
	}
}

func TestFilterStrategy_ToolParams(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	params := s.ToolParams(sampleEntity)

	// Check that field-specific params are generated.
	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	expected := []string{"name", "name__like", "name__in", "description", "description__like", "description__in",
		"price", "price__gt", "price__gte", "price__lt", "price__lte", "price__in",
		"active", "active__in",
		"category", "category__like", "category__in",
		"limit"}
	unexpected := []string{"offset", "sort_by", "format", "id"}
	for _, name := range expected {
		if !paramNames[name] {
			t.Errorf("Missing param: %s", name)
		}
	}
	for _, name := range unexpected {
		if paramNames[name] {
			t.Errorf("Unexpected param in schema: %s (should not be in ToolParams)", name)
		}
	}
}

func TestFilterStrategy_Description(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription should not be empty")
	}
}

// =============================================================================
// Tests: SimpleStrategy
// =============================================================================

func TestSimpleStrategy_Name(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	if got := s.Name(); got != "simple" {
		t.Errorf("Name() = %q, want %q", got, "simple")
	}
}

func TestSimpleStrategy_ToolName(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	if got := s.ToolName(sampleEntity); got != "simple_products" {
		t.Errorf("ToolName() = %q, want %q", got, "simple_products")
	}
}

func TestSimpleStrategy_NoFilters(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// No filters → no WHERE.
	wantSQL := `SELECT "id", "name", "description", "price", "active", "category" FROM "products" LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != 50 {
		t.Errorf("Limit should be 50 (default for simple), got %v", args)
	}
}

func TestSimpleStrategy_SearchField(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{"name": "muffler"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	// Use postgres adapter to see ILIKE.
	eng := query.NewEngine(wrapAdapter{a: testAdapter{}})
	sql, args, err := eng.Build(*plan)
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// SQLite: ILIKE → "name" COLLATE NOCASE LIKE ? for cyrillic support
	if !contains(sql, `"name" COLLATE NOCASE LIKE ?`) {
		t.Errorf("Expected COLLATE NOCASE on name: %q", sql)
	}
	if len(args) < 1 {
		t.Fatal("No args")
	}
	likeVal, ok := args[0].(string)
	if !ok || likeVal != "%muffler%" {
		t.Errorf("LIKE value = %q, want %%muffler%%", likeVal)
	}
}

func TestSimpleStrategy_ExactFilter(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{"category": "electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	if !contains(sql, `"category" = ?`) {
		t.Errorf("Expected category = ?: %q", sql)
	}
	if len(args) < 1 || args[0] != "electronics" {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestSimpleStrategy_SearchAndFilter(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{"name": "muffler", "category": "electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	_, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// 2 conditions + limit = 3 args.
	if len(args) != 3 {
		t.Errorf("Expected 3 args (2 conditions + limit), got %d: %v", len(args), args)
	}
}

// =============================================================================
// Tests: Adapter wrapper
// =============================================================================

func TestNewAdapter_DetectsSQLite(t *testing.T) {
	q := newQueryAdapter(false)
	a := NewAdapter(q)
	if a.IsPostgres() {
		t.Error("SQLite adapter should not be Postgres")
	}
}

func TestNewAdapter_DetectsPostgres(t *testing.T) {
	q := newQueryAdapter(true)
	a := NewAdapter(q)
	if !a.IsPostgres() {
		t.Error("Postgres adapter should be Postgres")
	}
}

// newQueryAdapter creates a minimal query.AdapterSubset for testing NewAdapter.
func newQueryAdapter(postgres bool) query.AdapterSubset {
	if postgres {
		return &mockAdapter{ph: "$1"}
	}
	return &mockAdapter{ph: "?"}
}

type mockAdapter struct {
	ph string
}

func (m *mockAdapter) TranslatePlaceholder(i int) string { return m.ph }
func (m *mockAdapter) QuoteIdentifier(s string) string    { return `"` + s + `"` }
func (m *mockAdapter) QuoteString(s string) string        { return s }

// =============================================================================
// Tests: GrepStrategy — Postgres placeholders
// =============================================================================

func TestGrepStrategy_PostgresPlaceholders(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapterPG{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	eng := query.NewEngine(wrapAdapter{a: testAdapterPG{}})
	sql, args, err := eng.Build(*plan)
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Postgres: ILIKE and $N placeholders.
	if !contains(sql, "ILIKE") {
		t.Errorf("Expected ILIKE for Postgres: %q", sql)
	}
	// Should have $N placeholders.
	if contains(sql, "?") {
		t.Errorf("Should not have SQLite placeholder '?' in Postgres mode: %q", sql)
	}
	if len(args) == 0 {
		t.Errorf("Expected args, got none")
	}
}
