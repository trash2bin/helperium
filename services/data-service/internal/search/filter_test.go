package search

import (
	"fmt"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
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

	wantSQL := `SELECT "id", "name" FROM "products" WHERE "name" COLLATE NOCASE LIKE ? ESCAPE '\' LIMIT ?`
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

	if !strings.Contains(sql, `ORDER BY "price" DESC`) {
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
	if !strings.Contains(sql, `"category" = ?`) {
		t.Errorf("Missing category condition: %q", sql)
	}
	if !strings.Contains(sql, `"active" = ?`) {
		t.Errorf("Missing active condition: %q", sql)
	}
	if !strings.Contains(sql, `"price" >= ?`) {
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

func TestFilter_UnknownFieldSkipped(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	// Unknown field param should be silently ignored; only category is valid.
	r := makeRequest(map[string]string{"category": "Electronics", "bogus_field": "x"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	// Should have exactly 1 condition (category, not bogus_field)
	if len(plan.Where) != 1 {
		t.Errorf("Expected 1 condition (category), got %d", len(plan.Where))
	}
}

func TestFilter_PKSkippedWithValidField(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	// PK field combined with valid field — should work.
	r := makeRequest(map[string]string{"id": "5", "category": "Electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	// Should have exactly 1 condition (category, not id which is PK)
	if len(plan.Where) != 1 {
		t.Errorf("Expected 1 condition (category only), got %d", len(plan.Where))
	}
}

func TestFilter_FloatComparison(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"price__gt": "50.5"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if len(plan.Where) != 1 {
		t.Fatalf("Expected 1 condition, got %d", len(plan.Where))
	}
	val, ok := plan.Where[0].Value.(float64)
	if !ok {
		t.Fatalf("Expected float64 value, got %T=%v", plan.Where[0].Value, plan.Where[0].Value)
	}
	if val != 50.5 {
		t.Errorf("Value = %f, want 50.5", val)
	}
}

func TestFilter_StringComparison(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	// Comparison on a string field (e.g. date as string).
	r := makeRequest(map[string]string{"name__gt": "m"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	sql, _, err := buildSQL(plan, testAdapter{})
	if err != nil {
		t.Fatalf("buildSQL: unexpected error: %v", err)
	}
	wantSQL := `SELECT "id", "name" FROM "products" WHERE "name" > ? LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q\nwant %q", sql, wantSQL)
	}
}

func TestFilter_DateTimeComparison(t *testing.T) {
	// Entity with a datetime/string field for date filtering.
	entity := config.Entity{
		Name:     "events",
		Table:    "events",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
			{Name: "event_date", Column: "event_date", Type: config.FieldTypeString},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
		},
	}
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{"event_date__gte": "2024-01-15"})
	plan, err := s.ParseRequest(r, entity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if len(plan.Where) != 1 {
		t.Fatalf("Expected 1 condition, got %d", len(plan.Where))
	}
	val, ok := plan.Where[0].Value.(string)
	if !ok {
		t.Fatalf("Expected string value for date, got %T=%v", plan.Where[0].Value, plan.Where[0].Value)
	}
	if val != "2024-01-15" {
		t.Errorf("Value = %q, want 2024-01-15", val)
	}
}

func TestFilter_MakeEqConditionInvalidConversion(t *testing.T) {
	// Invalid bool value should return error.
	f := config.EntityField{Name: "active", Column: "active", Type: config.FieldTypeBool}
	_, err := makeEqCondition(`"active"`, f, "notabool")
	if err == nil {
		t.Error("Expected error for invalid bool conversion, got nil")
	}

	// Invalid int value should return error.
	f2 := config.EntityField{Name: "qty", Column: "qty", Type: config.FieldTypeInt}
	_, err = makeEqCondition(`"qty"`, f2, "notanumber")
	if err == nil {
		t.Error("Expected error for invalid int conversion, got nil")
	}

	// Invalid float value should return error.
	f3 := config.EntityField{Name: "price", Column: "price", Type: config.FieldTypeFloat}
	_, err = makeEqCondition(`"price"`, f3, "notanumber")
	if err == nil {
		t.Error("Expected error for invalid float conversion, got nil")
	}
}

func TestFilter_MakeComparisonFloat(t *testing.T) {
	f := config.EntityField{Name: "price", Column: "price", Type: config.FieldTypeFloat}
	cond, err := makeComparison(`"price"`, "gte", f, "100.5")
	if err != nil {
		t.Fatalf("makeComparison: %v", err)
	}
	val, ok := cond.Value.(float64)
	if !ok {
		t.Fatalf("Expected float64 value, got %T=%v", cond.Value, cond.Value)
	}
	if val != 100.5 {
		t.Errorf("Value = %f, want 100.5", val)
	}
}

func TestFilter_EntityIDNameCol(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	if got := s.EntityIDCol(); got != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", got, "id")
	}
	if got := s.EntityNameCol(); got != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", got, "name")
	}
}

func TestFilter_MakeEqConditionNonString(t *testing.T) {
	// Direct test of unexported makeEqCondition with non-string types.
	f := config.EntityField{Name: "price", Column: "price", Type: config.FieldTypeFloat}
	cond, err := makeEqCondition(`"price"`, f, "99.5")
	if err != nil {
		t.Fatalf("makeEqCondition: %v", err)
	}
	val, ok := cond.Value.(float64)
	if !ok {
		t.Fatalf("Expected float64 value, got %T=%v", cond.Value, cond.Value)
	}
	if val != 99.5 {
		t.Errorf("Value = %f, want 99.5", val)
	}

	// Bool type
	f2 := config.EntityField{Name: "active", Column: "active", Type: config.FieldTypeBool}
	cond2, err := makeEqCondition(`"active"`, f2, "true")
	if err != nil {
		t.Fatalf("makeEqCondition: %v", err)
	}
	val2, ok := cond2.Value.(bool)
	if !ok {
		t.Fatalf("Expected bool value, got %T=%v", cond2.Value, cond2.Value)
	}
	if !val2 {
		t.Errorf("Value = false, want true")
	}

	// Int type
	f3 := config.EntityField{Name: "qty", Column: "qty", Type: config.FieldTypeInt}
	cond3, err := makeEqCondition(`"qty"`, f3, "42")
	if err != nil {
		t.Fatalf("makeEqCondition: %v", err)
	}
	val3, ok := cond3.Value.(int64)
	if !ok {
		t.Fatalf("Expected int64 value, got %T=%v", cond3.Value, cond3.Value)
	}
	if val3 != 42 {
		t.Errorf("Value = %d, want 42", val3)
	}
}

func TestFilter_Description(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription should not be empty")
	}
}

func TestFilterSecurity_MaxFilters_Exceeded(t *testing.T) {
	s := NewFilterStrategy("id", "name")

	// 16 filter conditions — should exceed maxFilters=15
	params := map[string]string{
		"name":              "a",
		"name__like":        "b",
		"name__neq":         "c",
		"name__in":          "d",
		"description":       "e",
		"description__like": "f",
		"description__neq":  "g",
		"description__in":   "h",
		"category":          "i",
		"category__like":    "j",
		"category__neq":     "k",
		"category__in":      "l",
		"price":             "1",
		"price__gt":         "2",
		"price__gte":        "3",
		"price__lt":         "4",
	}
	r := makeRequest(params)
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Fatal("ParseRequest: expected error for 16 filter conditions (max 15), got nil")
	}
	if !strings.Contains(err.Error(), "too many filter conditions") {
		t.Errorf("Unexpected error: %v", err)
	}
}

func TestFilterSecurity_MaxFilters_EdgeCase15(t *testing.T) {
	s := NewFilterStrategy("id", "name")

	// Exactly 15 filter conditions — should be allowed
	params := map[string]string{
		"name":              "a",
		"name__like":        "b",
		"name__neq":         "c",
		"name__in":          "d",
		"description":       "e",
		"description__like": "f",
		"description__neq":  "g",
		"description__in":   "h",
		"category":          "i",
		"category__like":    "j",
		"category__neq":     "k",
		"category__in":      "l",
		"price":             "1",
		"price__gt":         "2",
		"price__gte":        "3",
	}
	r := makeRequest(params)
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error for 15 filter conditions: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
}

// ── Noise entity для тестов фильтрации мусора ───────────────────────────────

var noiseEntity = config.Entity{
	Name:     "products",
	Table:    "products",
	IDColumn: "id",
	Fields: []config.EntityField{
		{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
		{Name: "name", Column: "name", Type: config.FieldTypeString},
		{Name: "price", Column: "price", Type: config.FieldTypeFloat},
		{Name: "category_id", Column: "category_id", Type: config.FieldTypeInt},
		{Name: "brand_id", Column: "brand_id", Type: config.FieldTypeInt},
		{Name: "is_available", Column: "is_available", Type: config.FieldTypeBool},
		{Name: "description", Column: "description", Type: config.FieldTypeString},
		{Name: "seo_title", Column: "seo_title", Type: config.FieldTypeString},             // ← noise
		{Name: "seo_description", Column: "seo_description", Type: config.FieldTypeString}, // ← noise
		{Name: "views_count", Column: "views_count", Type: config.FieldTypeInt},            // ← noise
		{Name: "weight_kg", Column: "weight_kg", Type: config.FieldTypeFloat},              // ← noise
		{Name: "dimensions", Column: "dimensions", Type: config.FieldTypeString},           // ← noise
		{Name: "image", Column: "image", Type: config.FieldTypeString},                     // ← noise
		{Name: "created_at", Column: "created_at", Type: config.FieldTypeDatetime},         // ← noise
		{Name: "is_popular", Column: "is_popular", Type: config.FieldTypeBool},             // ← noise
		{Name: "is_new", Column: "is_new", Type: config.FieldTypeBool},                     // ← noise
		{Name: "warranty_months", Column: "warranty_months", Type: config.FieldTypeInt},    // ← noise
	},
}

func TestFilterStrategy_IsFilterableField_NoiseExcluded(t *testing.T) {
	// Проверяем что бизнес-поля есть, а мусор — выкинут.
	// isFilterableField — unexported, проверяем косвенно через ToolParams.
	s := NewFilterStrategy("id", "name")
	params := s.ToolParams(noiseEntity)

	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	// Должны быть
	for _, name := range []string{"name", "name__in", "description", "price", "price__gt",
		"category_id", "category_id__in", "brand_id", "brand_id__in",
		"is_available", "is_available__in", "limit"} {
		if !paramNames[name] {
			t.Errorf("Missing expected param: %s", name)
		}
	}

	// Не должны быть
	for _, name := range []string{"seo_title", "seo_description", "views_count",
		"weight_kg", "dimensions", "image", "created_at",
		"is_popular", "is_new", "warranty_months"} {
		if paramNames[name] {
			t.Errorf("Noise field should be excluded: %s", name)
		}
	}

	// FK поля не должны иметь __gt/__lt
	for _, suffix := range []string{"__gt", "__lt", "__gte", "__lte"} {
		if paramNames["category_id"+suffix] {
			t.Errorf("FK field category_id should not have comparison op: %s", suffix)
		}
	}
}

func TestFilterStrategy_ToolParams_CountSanity(t *testing.T) {
	// 17 полей в noiseEntity (включая PK). После фильтрации:
	// PK (id) → скип
	// noise (seo, views, weight, dims, image, created, is_popular, is_new, warranty) → скип (9 шт)
	// filterable: name, price, category_id, brand_id, is_available, description = 6
	// Каждое: exact + __in = 2; price: +4 comparison (__gt/gte/lt/lte);
	//   name/description: +1 __like; category_id/brand_id/is_available: FK/bool — без comparison
	// Итого: 6*2 + 2 + 2 + 1 = ~17 params + limit = ~18
	// Просто проверяем что меньше 30 (было бы ~70+ с мусором)
	s := NewFilterStrategy("id", "name")
	params := s.ToolParams(noiseEntity)

	if len(params) >= 30 {
		t.Errorf("ToolParams should filter noise fields: got %d params, want < 30", len(params))
	}
	if len(params) < 5 {
		t.Errorf("ToolParams should still have business params: got %d, want >= 5", len(params))
	}
	t.Logf("noiseEntity filter params: %d (was ~70+ before fix)", len(params))
}

func TestFilterStrategy_IsFilterable_DateField(t *testing.T) {
	// * _date поля должны проходить через isFilterableField (бизнес-даты).
	// created_at/updated_at/deleted_at НЕ проходят — это системные поля.
	entity := config.Entity{
		Name:     "orders",
		Table:    "orders",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
			{Name: "shipped_date", Column: "shipped_date", Type: config.FieldTypeString},   // бизнес-дата
			{Name: "delivery_date", Column: "delivery_date", Type: config.FieldTypeString}, // бизнес-дата
			{Name: "created_at", Column: "created_at", Type: config.FieldTypeDatetime},     // системная
			{Name: "updated_at", Column: "updated_at", Type: config.FieldTypeDatetime},     // системная
		},
	}

	s := NewFilterStrategy("id", "name")
	params := s.ToolParams(entity)
	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	// Бизнес-даты должны быть
	for _, name := range []string{"shipped_date", "shipped_date__in", "delivery_date", "delivery_date__in"} {
		if !paramNames[name] {
			t.Errorf("Business date field should be filterable: %s", name)
		}
	}

	// Системные даты — нет
	for _, name := range []string{"created_at", "created_at__in", "updated_at", "updated_at__in"} {
		if paramNames[name] {
			t.Errorf("System datetime field should be excluded: %s", name)
		}
	}
}

func TestFilterStrategy_ToolParams_NoFilterableFields(t *testing.T) {
	// Entity с одними PK + системными полями — filter не должен падать,
	// но params будут содержать только limit.
	entity := config.Entity{
		Name:     "logs",
		Table:    "logs",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
			{Name: "seo_title", Column: "seo_title", Type: config.FieldTypeString},     // noise
			{Name: "created_at", Column: "created_at", Type: config.FieldTypeDatetime}, // system
			{Name: "weight_kg", Column: "weight_kg", Type: config.FieldTypeFloat},      // noise
		},
	}

	s := NewFilterStrategy("id", "name")
	params := s.ToolParams(entity)

	// Должен быть только limit — других полей нет
	if len(params) != 1 {
		t.Errorf("Expected only limit param for entity with no filterable fields, got %d: %v", len(params), params)
	}
	if len(params) > 0 && params[0].Name != "limit" {
		t.Errorf("Expected only 'limit' param, got %q", params[0].Name)
	}
	t.Logf("entity with only noise fields → %d params (just limit)", len(params))
}

// =============================================================================
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
func (m *mockAdapter) QuoteIdentifier(s string) string   { return `"` + s + `"` }
func (m *mockAdapter) QuoteString(s string) string       { return s }

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
	if !strings.Contains(sql, "ILIKE") {
		t.Errorf("Expected ILIKE for Postgres: %q", sql)
	}
	// Should have $N placeholders.
	if strings.Contains(sql, "?") {
		t.Errorf("Should not have SQLite placeholder '?' in Postgres mode: %q", sql)
	}
	if len(args) == 0 {
		t.Errorf("Expected args, got none")
	}
}

// TestFilterStrategy_ParseRequest_RespectsFilterableRules — M3(b): ParseRequest
// должен применять filterableRules (как ToolParams). Заблокированное поле
// block-only правилом не фильтруется через HTTP.
func TestFilterStrategy_ParseRequest_RespectsFilterableRules(t *testing.T) {
	blockRule := config.FieldRule{BlockNames: []string{"category"}}
	// Реалистичный набор: дефолтное allow-правило (name filterable)
	// + block-only правило (category blocked).
	allowRule := config.FieldRule{
		ID:         "filterable.common",
		AllowNames: []string{"name", "price", "status"},
	}
	s := NewFilterStrategy("id", "name", allowRule, blockRule)

	// category заблокирован → 0 conditions → "at least one filter parameter".
	r := makeRequest(map[string]string{"category": "x"})
	if _, err := s.ParseRequest(r, sampleEntity, testAdapter{}); err == nil {
		t.Fatal("expected error for blocked field category, got nil")
	}

	// name разрешён (allow-правило).
	r2 := makeRequest(map[string]string{"name": "x"})
	if _, err := s.ParseRequest(r2, sampleEntity, testAdapter{}); err != nil {
		t.Errorf("expected OK for allowed field name, got: %v", err)
	}

	// Зеркальность: ToolParams тоже не содержит category.
	params := s.ToolParams(sampleEntity)
	for _, p := range params {
		if p.Name == "category" {
			t.Errorf("ToolParams should exclude blocked field category")
		}
	}
}

func TestFilterStrategy_ToolParamsIncludeFieldDescription(t *testing.T) {
	s := NewFilterStrategy("id", "name", config.DefaultFilterableFieldRules()...)
	entity := config.Entity{
		Name: "catalog_product",
		Fields: []config.EntityField{
			{Name: "old_price", Column: "old_price", Type: config.FieldTypeFloat, Description: "Price discount means old_price is higher than the current price."},
			{Name: "label", Column: "label", Type: config.FieldTypeString, Description: "sale and promo are marketing labels independent of price discount."},
		},
	}

	params := s.ToolParams(entity)
	descriptions := make(map[string]string, len(params))
	for _, param := range params {
		descriptions[param.Name] = param.Description
	}
	for _, name := range []string{"old_price__gt", "label__in"} {
		if !strings.Contains(descriptions[name], "Field meaning:") {
			t.Errorf("%s description = %q, want field meaning", name, descriptions[name])
		}
	}
	if !strings.Contains(descriptions["old_price__gt"], "higher than the current price") {
		t.Errorf("old_price__gt description = %q", descriptions["old_price__gt"])
	}
	if !strings.Contains(descriptions["label__in"], "marketing labels") {
		t.Errorf("label__in description = %q", descriptions["label__in"])
	}
}

func TestFilterStrategy_ToolDescription_ExplainsRequiredFilterAndTotal(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	description := s.ToolDescription(sampleEntity)
	for _, want := range []string{
		"every call must include at least one field filter",
		"limit only controls the returned preview; it is not a filter",
		"total, the authoritative number of records",
		"For a count question, use total",
	} {
		if !strings.Contains(description, want) {
			t.Errorf("ToolDescription() missing %q: %s", want, description)
		}
	}
}
