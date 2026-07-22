package search

import (
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// =============================================================================
// Test helpers
// =============================================================================

// testAdapter implements Adapter for SQLite-style quoting.
type testAdapter struct{}

func (testAdapter) QuoteIdentifier(name string) string     { return `"` + name + `"` }
func (testAdapter) QuoteString(s string) string            { return escapeLikeSQL(s) }
func (testAdapter) TranslatePlaceholder(index int) string   { return "?" }
func (testAdapter) IsPostgres() bool                        { return false }

// testAdapterPG implements Adapter for PostgreSQL-style quoting.
type testAdapterPG struct{}

func (testAdapterPG) QuoteIdentifier(name string) string     { return `"` + name + `"` }
func (testAdapterPG) QuoteString(s string) string            { return escapeLikeSQL(s) }
func (testAdapterPG) TranslatePlaceholder(index int) string   { return fmt.Sprintf("$%d", index) }
func (testAdapterPG) IsPostgres() bool                        { return true }

func escapeLikeSQL(s string) string {
	escaped := ""
	for _, c := range s {
		if c == '%' || c == '_' {
			escaped += "\\"
		}
		escaped += string(c)
	}
	return escaped
}

// makeRequest creates an *http.Request with query params for testing.
func makeRequest(params map[string]string) *http.Request {
	q := url.Values{}
	for k, v := range params {
		q.Set(k, v)
	}
	u := &url.URL{RawQuery: q.Encode()}
	return &http.Request{URL: u}
}

// sampleEntity — a test entity with various field types.
var sampleEntity = config.Entity{
	Name:    "products",
	Table:   "products",
	IDColumn: "id",
	Description: "Product catalog",
	Fields: []config.EntityField{
		{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
		{Name: "name", Column: "name", Type: config.FieldTypeString},
		{Name: "description", Column: "description", Type: config.FieldTypeString},
		{Name: "price", Column: "price", Type: config.FieldTypeFloat},
		{Name: "active", Column: "active", Type: config.FieldTypeBool},
		{Name: "category", Column: "category", Type: config.FieldTypeString},
	},
}

func boolPtr(b bool) *bool { return &b }

// buildSQL builds SQL from a QueryPlan for easy comparison.
func buildSQL(plan *query.QueryPlan, a Adapter) (string, []any, error) {
	eng := query.NewEngine(wrapAdapter{a: a})
	return eng.Build(*plan)
}

// wrapAdapter wraps a search.Adapter into a query.AdapterSubset.
type wrapAdapter struct {
	a Adapter
}

func (w wrapAdapter) TranslatePlaceholder(index int) string { return w.a.TranslatePlaceholder(index) }
func (w wrapAdapter) QuoteIdentifier(name string) string    { return w.a.QuoteIdentifier(name) }
func (w wrapAdapter) QuoteString(s string) string          { return w.a.QuoteString(s) }

// =============================================================================
// Tests: GrepStrategy
// =============================================================================

func TestGrepStrategy_Name(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	if got := s.Name(); got != "grep" {
		t.Errorf("Name() = %q, want %q", got, "grep")
	}
}

func TestGrepStrategy_ToolName(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	if got := s.ToolName(sampleEntity); got != "grep_products" {
		t.Errorf("ToolName() = %q, want %q", got, "grep_products")
	}
}

func TestGrepStrategy_EntityIDName(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	if got := s.EntityIDCol(); got != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", got, "id")
	}
	if got := s.EntityNameCol(); got != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", got, "name")
	}
}

func TestGrepStrategy_EmptyPattern_ReturnsError(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("ParseRequest: expected error for empty pattern, got nil")
	}
	if !strings.Contains(err.Error(), "'pattern' is required") {
		t.Errorf("Error = %q, want 'pattern' is required", err.Error())
	}
}

func TestGrepStrategy_SimplePattern(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "muffler"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Single token → LIKE on all string fields, OR between them.
	// SQLite test adapter (non-Postgres) wraps with COLLATE NOCASE for cyrillic support.
	wantSQL := `SELECT "id", "name" FROM "products" WHERE ("name" COLLATE NOCASE LIKE ?) OR ("description" COLLATE NOCASE LIKE ?) OR ("category" COLLATE NOCASE LIKE ?) LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 4 || args[0] != "%muffler%" || args[3] != 10 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestGrepStrategy_MultiTokenPattern(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "глушители авто"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Two tokens → AND inside each field, OR between fields.
	// SQLite adapter wraps with COLLATE NOCASE for cyrillic support.
	expectedSubstr := "(\"name\" COLLATE NOCASE LIKE ? AND \"name\" COLLATE NOCASE LIKE ?)"
	if !contains(sql, expectedSubstr) {
		t.Errorf("SQL missing AND clause for multi-token: %q", sql)
	}
	if len(args) != 7 { // 2 tokens × 3 fields = 6 + limit
		t.Errorf("Expected 7 args, got %d: %v", len(args), args)
	}
}

func TestGrepStrategy_RegexPattern(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "^ABC.*", "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Regex: OR between fields with REGEXP operator.
	// Each field gets its own placeholder.
	wantSQL := `SELECT "id", "name" FROM "products" WHERE ("name" REGEXP ? OR "description" REGEXP ? OR "category" REGEXP ?) LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 4 || args[0] != "^ABC.*" || args[1] != "^ABC.*" || args[2] != "^ABC.*" || args[3] != 10 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestGrepStrategy_RegexInvert(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "^ABC.*", "regex": "true", "invert": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	if !contains(sql, "!REGEXP") {
		t.Errorf("Regex invert should use !REGEXP, got: %q", sql)
	}
}

func TestGrepStrategy_Limit(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "limit": "25"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	if !contains(sql, "LIMIT ?") {
		t.Errorf("SQL missing LIMIT: %q", sql)
	}
	if len(args) != 4 || args[3] != 25 {
		t.Errorf("Limit not 25: %v", args)
	}
}

func TestGrepStrategy_LimitCapped(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "limit": "5000"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Limit != 100 {
		t.Errorf("Limit = %d, want 100", plan.Limit)
	}
}

func TestGrepStrategy_Offset(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "offset": "20"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Offset != 20 {
		t.Errorf("Offset = %d, want 20", plan.Offset)
	}
}

func TestGrepStrategy_IgnoreCaseDefault(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "Test"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// SQLite: COLLATE NOCASE wrapping for cyrillic support.
	if !contains(sql, "COLLATE NOCASE") {
		t.Errorf("Expected COLLATE NOCASE for SQLite: %q", sql)
	}
	if !contains(sql, "LIKE") {
		t.Errorf("Expected LIKE for SQLite: %q", sql)
	}
}

func TestGrepStrategy_IgnoreCasePostgres(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "Test"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapterPG{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	eng := query.NewEngine(wrapAdapter{a: testAdapterPG{}})
	sql, _, err := eng.Build(*plan)
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Postgres: should use ILIKE.
	if !contains(sql, "ILIKE") {
		t.Errorf("Expected ILIKE for Postgres: %q", sql)
	}
}

func TestGrepStrategy_IgnoreCaseFalse(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "Test", "ignore_case": "false"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// ignore_case=false → use LIKE without COLLATE NOCASE (case-sensitive).
	if contains(sql, "COLLATE NOCASE") {
		t.Errorf("Expected NO COLLATE NOCASE when ignore_case=false: %q", sql)
	}
	if !contains(sql, "LIKE") {
		t.Errorf("Expected LIKE: %q", sql)
	}
}

func TestGrepStrategy_CustomFields(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "fields": "name,category"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Only "name" and "category" should be in the query.
	// SQLite adapter wraps with COLLATE NOCASE for cyrillic support.
	expectedSubstr1 := `"name" COLLATE NOCASE LIKE`
	expectedSubstr2 := `"category" COLLATE NOCASE LIKE`
	notExpected := `"description"`

	if !contains(sql, expectedSubstr1) {
		t.Errorf("Expected name in query: %q", sql)
	}
	if !contains(sql, expectedSubstr2) {
		t.Errorf("Expected category in query: %q", sql)
	}
	if contains(sql, notExpected) {
		t.Errorf("Did NOT expect description in query: %q", sql)
	}
}

func TestGrepStrategy_Invert(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "invert": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Invert with COLLATE NOCASE for SQLite: "name" COLLATE NOCASE NOT LIKE ?
	if !contains(sql, "NOT LIKE") && !contains(sql, "COLLATE NOCASE") {
		t.Errorf("Invert should produce NOT LIKE, got: %q", sql)
	}
}

func TestGrepStrategy_FormatFull(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "format": "full"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Full format should select all columns.
	expectedCols := `"id", "name", "description", "price", "active", "category"`
	if !contains(sql, expectedCols) {
		t.Errorf("Full format should select all columns: %q", sql)
	}

	if plan.Format != query.FormatFull {
		t.Errorf("Format = %d, want FormatFull", plan.Format)
	}
}

func TestGrepStrategy_SortBy(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "sort_by": "-price"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Should have ORDER BY "price" DESC.
	if !contains(sql, `ORDER BY "price" DESC`) {
		t.Errorf("Expected ORDER BY price DESC: %q", sql)
	}
}

func TestGrepStrategy_SortByAsc(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "sort_by": "price"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	if !contains(sql, `ORDER BY "price" ASC`) {
		t.Errorf("Expected ORDER BY price ASC: %q", sql)
	}
}

// =============================================================================
// Tests: ToolParams
// =============================================================================

func TestGrepStrategy_ToolParams(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	params := s.ToolParams(sampleEntity)

	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	// Only 3 params: pattern (required), limit, fields
	expected := []string{"pattern", "limit", "fields"}
	for _, name := range expected {
		if !paramNames[name] {
			t.Errorf("Missing param: %s", name)
		}
	}

	// Should NOT have removed params
	removed := []string{"ignore_case", "invert", "regex", "format", "offset", "sort_by"}
	for _, name := range removed {
		if paramNames[name] {
			t.Errorf("Expected removed param: %s", name)
		}
	}

	// pattern should be required.
	for _, p := range params {
		if p.Name == "pattern" {
			if p.Required == nil || !*p.Required {
				t.Error("pattern should be required")
			}
		}
	}
}

func TestGrepStrategy_Description(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription should not be empty")
	}
}

// =============================================================================
// Security tests
// =============================================================================

func TestGrepSecurity_ReDoS_RegexTooLong(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	// Build a 201-char pattern
	longPattern := ""
	for i := 0; i < 201; i++ {
		longPattern += "a"
	}

	r := makeRequest(map[string]string{"pattern": longPattern, "regex": "true"})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("Expected error for regex pattern > 200 chars, got nil")
	}
	if !contains(err.Error(), "too long") {
		t.Errorf("Expected 'too long' error, got: %v", err)
	}
}

func TestGrepSecurity_ReDoS_EdgeCase200Chars(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	// Exactly 200 chars — should be allowed
	pattern := ""
	for i := 0; i < 200; i++ {
		pattern += "a"
	}

	r := makeRequest(map[string]string{"pattern": pattern, "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("Expected 200-char pattern to be allowed, got error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

func TestGrepSecurity_TokenFlood_Capped(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	// 15 tokens — should be capped to 10
	pattern := "a b c d e f g h i j k l m n o"

	r := makeRequest(map[string]string{"pattern": pattern})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL error: %v", err)
	}
	_ = sql

	// 10 tokens × 3 string fields = 30 + limit
	if len(args) != 31 {
		t.Errorf("Expected 31 args for 10 capped tokens (10×3=30 + limit), got %d: %v", len(args), args)
	}
}

func TestGrepSecurity_FieldsLimit(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	// 25 comma-separated fields — should error
	fields := ""
	for i := 0; i < 25; i++ {
		if i > 0 {
			fields += ","
		}
		fields += fmt.Sprintf("field%d", i)
	}

	r := makeRequest(map[string]string{"pattern": "test", "fields": fields})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("Expected error for 25 fields, got nil")
	}
	if !contains(err.Error(), "too many fields") {
		t.Errorf("Expected 'too many fields' error, got: %v", err)
	}
}

func TestGrepSecurity_NonRegexPatternNoLimit(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	// 500-char non-regex pattern — should be allowed (only regex has length limit)
	pattern := ""
	for i := 0; i < 500; i++ {
		pattern += "x"
	}

	r := makeRequest(map[string]string{"pattern": pattern})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("Long non-regex pattern should be allowed, got error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

// =============================================================================
// ReDoS security tests
// =============================================================================

func TestGrep_RejectsLongRegex(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// 201-char regex pattern — exceeds maxRegexLen (200)
	pattern := ""
	for i := 0; i < 201; i++ {
		pattern += "a"
	}
	r := makeRequest(map[string]string{"pattern": pattern, "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatalf("Expected error for 201-char regex, got plan=%+v", plan)
	}
	if !strings.Contains(err.Error(), "regex pattern too long") {
		t.Errorf("Expected 'regex pattern too long' error, got: %v", err)
	}
}

func TestGrep_AcceptsBoundaryRegex(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// 200-char regex pattern — exactly at maxRegexLen boundary
	pattern := ""
	for i := 0; i < 200; i++ {
		pattern += "a"
	}
	r := makeRequest(map[string]string{"pattern": pattern, "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("Expected no error for 200-char regex, got: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

func TestGrep_CatastrophicBacktrackingPattern(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// Catastrophic backtracking pattern (a+)+b$ — length < 200, should be accepted
	pattern := `(a+)+b$`
	r := makeRequest(map[string]string{"pattern": pattern, "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("Expected no error for catastrophic backtracking pattern, got: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

func TestGrep_ListPlanFallback(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// Entity with NO string fields should trigger listPlan fallback.
	noStrEntity := config.Entity{
		Name:     "items",
		Table:    "items",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
			{Name: "price", Column: "price", Type: config.FieldTypeFloat},
			{Name: "active", Column: "active", Type: config.FieldTypeBool},
		},
	}
	r := makeRequest(map[string]string{"pattern": "test"})
	plan, err := s.ParseRequest(r, noStrEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error for no-string entity: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	// Fallback = listPlan = no WHERE clause (since no string fields to search)
	if len(plan.Where) != 0 {
		t.Errorf("Expected no WHERE for no-string entity, got %d conditions", len(plan.Where))
	}
	// Default limit for grep
	if plan.Limit != 10 {
		t.Errorf("Limit = %d, want 10", plan.Limit)
	}
}

func TestGrep_TokenizeEmptyString(t *testing.T) {
	result := tokenize("")
	if result != nil {
		t.Errorf("tokenize('') = %v, want nil", result)
	}
}

func TestGrep_TokenizeSpacesOnly(t *testing.T) {
	result := tokenize("   ")
	if result != nil {
		t.Errorf("tokenize('   ') = %v, want nil", result)
	}
}

func TestGrep_RegexInvertPostgres(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "^ABC", "regex": "true", "invert": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapterPG{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	eng := query.NewEngine(wrapAdapter{a: testAdapterPG{}})
	sql, _, err := eng.Build(*plan)
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Postgres: invert uses !~
	if !contains(sql, "!~") {
		t.Errorf("Expected !~ (invert REGEXP) for Postgres, got: %q", sql)
	}
}

func TestGrep_InvalidFormat(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "format": "bogus"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	// Invalid format should default to compact
	if plan.Format != query.FormatCompact {
		t.Errorf("Format = %d (want FormatCompact=%d) for invalid format 'bogus'", plan.Format, query.FormatCompact)
	}
}

func TestGrep_InvalidSortBy(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "sort_by": "nonexistent_field"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	// Invalid sort_by should result in no ORDER clause
	if len(plan.Order) != 0 {
		t.Errorf("Expected no Order for invalid sort_by, got %v", plan.Order)
	}
}

func TestGrep_RawRegexLike(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// Realistic short regex: license plate pattern
	pattern := `^[A-Z]{2}\d{4}[A-Z]{2}$`
	r := makeRequest(map[string]string{"pattern": pattern, "regex": "true"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("Expected no error for realistic regex, got: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

// =============================================================================
// Helpers
// =============================================================================

func contains(s, substr string) bool {
	return len(s) >= len(substr) && containsStr(s, substr)
}

func containsStr(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
