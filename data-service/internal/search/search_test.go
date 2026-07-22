package search

import (
	"fmt"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/query"
)

// =============================================================================
// Tests: SearchStrategy — unified grep + filter
// =============================================================================

func TestSearchStrategy_Name(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	if got := s.Name(); got != "search" {
		t.Errorf("Name() = %q, want %q", got, "search")
	}
}

func TestSearchStrategy_ToolName(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	if got := s.ToolName(sampleEntity); got != "search_products" {
		t.Errorf("ToolName() = %q, want %q", got, "search_products")
	}
}

func TestSearchStrategy_EntityIDName(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	if got := s.EntityIDCol(); got != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", got, "id")
	}
	if got := s.EntityNameCol(); got != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", got, "name")
	}
}

func TestSearchStrategy_NoParams_ReturnsError(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("ParseRequest: expected error for empty params, got nil")
	}
	if !strings.Contains(err.Error(), "at least one parameter") {
		t.Errorf("Error = %q, want 'at least one parameter'", err.Error())
	}
	// Should include available fields in the error message
	if !strings.Contains(err.Error(), "Available fields:") && !strings.Contains(err.Error(), "Доступные поля") {
		t.Errorf("Error should list available fields: %s", err.Error())
	}
}

func TestSearchStrategy_PatternOnly_Simple(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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
	// SQLite: COLLATE NOCASE for cyrillic support.
	wantSQL := `SELECT "id", "name" FROM "products" WHERE ("name" COLLATE NOCASE LIKE ?) OR ("description" COLLATE NOCASE LIKE ?) OR ("category" COLLATE NOCASE LIKE ?) LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 4 || args[0] != "%muffler%" || args[3] != 10 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestSearchStrategy_PatternOnly_MultiToken(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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
	expectedSubstr := "(\"name\" COLLATE NOCASE LIKE ? AND \"name\" COLLATE NOCASE LIKE ?)"
	if !contains(sql, expectedSubstr) {
		t.Errorf("SQL missing AND clause for multi-token: %q", sql)
	}
	if len(args) != 7 { // 2 tokens × 3 fields = 6 + limit
		t.Errorf("Expected 7 args, got %d: %v", len(args), args)
	}
}

func TestSearchStrategy_PatternOnly_Regex(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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
	wantSQL := `SELECT "id", "name" FROM "products" WHERE ("name" REGEXP ? OR "description" REGEXP ? OR "category" REGEXP ?) LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 4 || args[0] != "^ABC.*" || args[3] != 10 {
		t.Errorf("Args unexpected: %v", args)
	}
}

func TestSearchStrategy_FilterOnly_ExactMatch(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{"category": "electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Filter-only → clean Condition-based path, uses parseFilterLimit (default 20).
	wantSQL := `SELECT "id", "name" FROM "products" WHERE "category" = ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "electronics" {
		t.Errorf("Args unexpected: %v", args)
	}
	if plan.Limit != 10 {
		t.Errorf("Limit = %d, want 10 (filter default)", plan.Limit)
	}
}

func TestSearchStrategy_FilterOnly_ComparisonOp(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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
	if plan.Limit != 10 {
		t.Errorf("Limit = %d, want 10 (filter default)", plan.Limit)
	}
}

func TestSearchStrategy_FilterOnly_LikeOp(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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

func TestSearchStrategy_FilterOnly_InOp(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{"category__in": "a,b,c"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
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

func TestSearchStrategy_PatternAndFilter_Combined(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{
		"pattern":  "muffler",
		"category": "brakes",
	})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	sql, args, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}

	// Combined path: pattern → RawWhere grep part + filter → RawWhere condition part.
	// Both wrapped and joined by AND.
	// Should contain LIKE grep parts AND category = part.
	if !contains(sql, `COLLATE NOCASE LIKE ?`) {
		t.Errorf("Expected grep-like LIKE in combined query: %q", sql)
	}
	if !contains(sql, `"category" = ?`) {
		t.Errorf("Expected filter condition 'category = ?' in combined query: %q", sql)
	}
	// Args: 3 patterns (3 string fields) + 1 category value + 1 limit = 5
	if len(args) != 5 {
		t.Errorf("Expected 5 args (3 grep + 1 filter + 1 limit), got %d: %v", len(args), args)
	}
	// Default limit for combined (hasFilters) should be filter default: 10
	if plan.Limit != 10 {
		t.Errorf("Limit = %d, want 10 (filter default in combined mode)", plan.Limit)
	}
}

func TestSearchStrategy_CustomFields(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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

func TestSearchStrategy_Limit(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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

func TestSearchStrategy_ToolParams(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	params := s.ToolParams(sampleEntity)

	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	// pattern should be present and REQUIRED (prevents LLM from sending empty calls)
	var patternRequired *bool
	for _, p := range params {
		if p.Name == "pattern" {
			patternRequired = p.Required
			break
		}
	}
	if patternRequired == nil {
		t.Error("pattern should have a Required field")
	} else if !*patternRequired {
		t.Error("pattern should be REQUIRED to prevent LLM empty-call loop")
	}

	// Field params should be present
	expected := []string{"pattern", "name", "name__like", "name__neq",
		"description", "description__like", "description__neq",
		"price", "price__gt", "price__lt", "price__neq",
		"active", "active__neq",
		"category", "category__like", "category__neq",
		"limit"}
	for _, name := range expected {
		if !paramNames[name] {
			t.Errorf("Missing param: %s", name)
		}
	}

	// Should NOT include pagination/internal params in schema
	unexpected := []string{"offset", "sort_by", "format", "id", "fields", "ignore_case", "regex", "invert"}
	for _, name := range unexpected {
		if paramNames[name] {
			t.Errorf("Unexpected param in schema: %s (should not be in ToolParams)", name)
		}
	}
}

func TestSearchStrategy_ToolDescription(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription should not be empty")
	}
	if !contains(desc, "search_products") {
		t.Errorf("Description should mention the entity: %q", desc)
	}
	if !contains(desc, "pattern") {
		t.Errorf("Description should mention 'pattern' parameter: %q", desc)
	}
	if !contains(desc, "__gt") {
		t.Errorf("Description should mention field operators: %q", desc)
	}
}

// =============================================================================
// Tests: SearchStrategy — Security
// =============================================================================

func TestSearchSecurity_ReDoS_RegexTooLong(t *testing.T) {
	s := NewSearchStrategy("id", "name")

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

func TestSearchSecurity_ReDoS_EdgeCase200Chars(t *testing.T) {
	s := NewSearchStrategy("id", "name")

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

func TestSearchSecurity_TokenFlood_Capped(t *testing.T) {
	s := NewSearchStrategy("id", "name")

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

func TestSearchSecurity_FieldsLimit(t *testing.T) {
	s := NewSearchStrategy("id", "name")

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

// =============================================================================
// Tests: SearchStrategy — Postgres Placeholders
// =============================================================================

func TestSearchStrategy_PostgresPlaceholders(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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
	if contains(sql, "?") {
		t.Errorf("Should not have SQLite placeholder '?' in Postgres mode: %q", sql)
	}
	if len(args) == 0 {
		t.Errorf("Expected args, got none")
	}
}

// =============================================================================
// Tests: SearchStrategy — SortBy and Offset
// =============================================================================

func TestSearchStrategy_SortBy(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "sort_by": "-price"})
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

func TestSearchStrategy_Offset(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	r := makeRequest(map[string]string{"pattern": "test", "offset": "20"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}

	if plan.Offset != 20 {
		t.Errorf("Offset = %d, want 20", plan.Offset)
	}
}

// =============================================================================
// Tests: SearchStrategy — Format Full
// =============================================================================

func TestSearchStrategy_FormatFull(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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

// =============================================================================
// Tests: SearchStrategy — Invert + Regex
// =============================================================================

func TestSearchStrategy_InvertRegex(t *testing.T) {
	s := NewSearchStrategy("id", "name")
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

// =============================================================================
// Tests: SearchStrategy — Non-regex long pattern allowed
// =============================================================================

func TestSearchSecurity_NonRegexPatternAllows500(t *testing.T) {
	s := NewSearchStrategy("id", "name")

	// 500-char non-regex pattern — should be allowed (maxPatternLen=2000)
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

func TestSearchSecurity_PatternTooLong_Over2000(t *testing.T) {
	s := NewSearchStrategy("id", "name")

	// 2001 chars — should error
	var b strings.Builder
	for i := 0; i < 2001; i++ {
		b.WriteByte('x')
	}

	r := makeRequest(map[string]string{"pattern": b.String()})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("Expected error for pattern > 2000 chars, got nil")
	}
	if !contains(err.Error(), "too long") {
		t.Errorf("Expected 'too long' error, got: %v", err)
	}
}

func TestSearchSecurity_FilterValueTooLong(t *testing.T) {
	s := NewSearchStrategy("id", "name")

	// 1001-char filter value — should error
	var b strings.Builder
	for i := 0; i < 1001; i++ {
		b.WriteByte('a')
	}

	r := makeRequest(map[string]string{"category": b.String()})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("Expected error for filter value > 1000 chars, got nil")
	}
	if !contains(err.Error(), "too long") {
		t.Errorf("Expected 'too long' error, got: %v", err)
	}
}

func TestSearchSecurity_InTooManyValues(t *testing.T) {
	s := NewSearchStrategy("id", "name")

	// 101 values in IN — should error
	var b strings.Builder
	for i := 0; i < 101; i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "val%d", i)
	}

	r := makeRequest(map[string]string{"category__in": b.String()})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("Expected error for 101 IN values (max 100), got nil")
	}
	if !contains(err.Error(), "too many values") {
		t.Errorf("Expected 'too many values' error, got: %v", err)
	}
}

func TestSearchStrategy_ToolParams_IncludesNeq(t *testing.T) {
	s := NewSearchStrategy("id", "name")
	params := s.ToolParams(sampleEntity)

	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	// Check __neq params exist for non-PK fields.
	if !paramNames["name__neq"] {
		t.Error("Missing param: name__neq")
	}
	if !paramNames["category__neq"] {
		t.Error("Missing param: category__neq")
	}
	if !paramNames["price__neq"] {
		t.Error("Missing param: price__neq")
	}
	if !paramNames["active__neq"] {
		t.Error("Missing param: active__neq")
	}
	if paramNames["id__neq"] {
		t.Error("Should not have id__neq (it's PK)")
	}
}
