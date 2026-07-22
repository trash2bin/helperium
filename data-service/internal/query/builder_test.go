package query

import (
	"fmt"
	"strings"
	"testing"
)

// =============================================================================
// Test helpers
// =============================================================================

// sqliteAdapter — реализует AdapterSubset для SQLite.
type sqliteAdapter struct{}

func (sqliteAdapter) TranslatePlaceholder(index int) string { return "?" }
func (sqliteAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (sqliteAdapter) QuoteString(s string) string           { return escapeLike(s) }

// postgresAdapter — реализует AdapterSubset для PostgreSQL.
type postgresAdapter struct{}

func (postgresAdapter) TranslatePlaceholder(index int) string { return fmt.Sprintf("$%d", index) }
func (postgresAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (postgresAdapter) QuoteString(s string) string           { return escapeLike(s) }

// escapeLike — экранирует '%' и '_' в LIKE-паттернах (для SQLite/Postgres).
func escapeLike(s string) string {
	return strings.NewReplacer("%", "\\%", "_", "\\_").Replace(s)
}

// =============================================================================
// Tests: Build
// =============================================================================

func TestBuild_Eq(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			Eq(`"id"`, 42),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" WHERE "id" = ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != 42 {
		t.Errorf("Args = %v, want [42]", args)
	}
}

func TestBuild_Eq_Postgres(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			Eq(`"id"`, 42),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" WHERE "id" = $1`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != 42 {
		t.Errorf("Args = %v, want [42]", args)
	}
}

func TestBuild_Like(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			Like(`"email"`, "%example.com"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" WHERE "email" LIKE ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != `\%example.com` {
		t.Errorf("Args = %v, want [%%example.com escaped]", args)
	}
}

func TestBuild_Like_EscapesWildcards(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"items"`,
		Where: []Condition{
			Like(`"name"`, "100%_complete"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "items" WHERE "name" LIKE ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != `100\%\_complete` {
		t.Errorf("Args = %v, want [complete-value-escaping]", args)
	}
}

func TestBuild_ILike_SQLite(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			ILike(`"email"`, "%Example.COM"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	// SQLite: ILIKE → "email" COLLATE NOCASE LIKE ? for cyrillic support
	wantSQL := `SELECT * FROM "customers" WHERE "email" COLLATE NOCASE LIKE ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	// Value kept as-is for COLLATE NOCASE (no LOWER transform needed)
	if len(args) != 1 || args[0] != `\%Example.COM` {
		t.Errorf("Args = %v, want [escaped]", args)
	}
}

func TestBuild_ILike_Postgres(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			ILike(`"email"`, "%Example.COM"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	// Postgres: ILIKE native
	wantSQL := `SELECT * FROM "customers" WHERE "email" ILIKE $1`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != `\%Example.COM` {
		t.Errorf("Args = %v, want [percent-escaped]", args)
	}
}

func TestBuild_In(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"orders"`,
		Where: []Condition{
			In(`"status"`, "active", "pending"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "orders" WHERE "status" IN (?, ?)`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "active" || args[1] != "pending" {
		t.Errorf("Args = %v, want [active, pending]", args)
	}
}

func TestBuild_In_Postgres(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"orders"`,
		Where: []Condition{
			In(`"status"`, "active", "pending"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "orders" WHERE "status" IN ($1, $2)`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "active" || args[1] != "pending" {
		t.Errorf("Args = %v, want [active, pending]", args)
	}
}

func TestBuild_MultipleWhereWithAnd(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"products"`,
		Where: And(
			Eq(`"category"`, "electronics"),
			Gt(`"price"`, 100),
			Lt(`"stock"`, 50),
		),
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "products" WHERE "category" = ? AND "price" > ? AND "stock" < ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 3 || args[0] != "electronics" || args[1] != 100 || args[2] != 50 {
		t.Errorf("Args = %v, want [electronics, 100, 50]", args)
	}
}

func TestBuild_OrderBy(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Order: []OrderClause{
			{Field: `"created_at"`, Desc: true},
			{Field: `"email"`, Desc: false},
		},
	}

	sql, _, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" ORDER BY "created_at" DESC, "email" ASC`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
}

func TestBuild_Limit(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From:  `"customers"`,
		Limit: 10,
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != 10 {
		t.Errorf("Args = %v, want [10]", args)
	}
}

func TestBuild_LimitOffset(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From:   `"customers"`,
		Limit:  20,
		Offset: 40,
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "customers" LIMIT $1 OFFSET $2`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != 20 || args[1] != 40 {
		t.Errorf("Args = %v, want [20, 40]", args)
	}
}

func TestBuild_SelectColumns(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		Select: SelectClause{Columns: []string{`"id"`, `"email"`}},
		From:   `"customers"`,
	}

	sql, _, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT "id", "email" FROM "customers"`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
}

func TestBuild_EmptyFrom(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{From: ""}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("Build with empty From: expected error, got nil")
	}
}

func TestBuild_Neq_Lt_Gt_Gte_Lte(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			Neq(`"a"`, 1),
			Lt(`"b"`, 2),
			Gt(`"c"`, 3),
			Lte(`"d"`, 4),
			Gte(`"e"`, 5),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "t" WHERE "a" != ? AND "b" < ? AND "c" > ? AND "d" <= ? AND "e" >= ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 5 || fmt.Sprint(args) != "[1 2 3 4 5]" {
		t.Errorf("Args = %v, want [1 2 3 4 5]", args)
	}
}

func TestBuild_Between(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"products"`,
		Where: []Condition{
			Between(`"price"`, 10, 100),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "products" WHERE "price" BETWEEN ? AND ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != 10 || args[1] != 100 {
		t.Errorf("Args = %v, want [10, 100]", args)
	}
}

func TestBuild_NotFlag(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	c := Eq(`"active"`, true)
	c.Not = true
	plan := QueryPlan{
		From:  `"users"`,
		Where: []Condition{c},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "users" WHERE "active" NOT = $1`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != true {
		t.Errorf("Args = %v, want [true]", args)
	}
}

func TestBuild_Regexp_SQLite(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"items"`,
		Where: []Condition{
			Regexp(`"code"`, "^ABC"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "items" WHERE "code" REGEXP ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != "^ABC" {
		t.Errorf("Args = %v, want [^ABC]", args)
	}
}

func TestBuild_Regexp_Postgres(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"items"`,
		Where: []Condition{
			Regexp(`"code"`, "^ABC"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "items" WHERE "code" ~ $1`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != "^ABC" {
		t.Errorf("Args = %v, want [^ABC]", args)
	}
}

func TestBuild_NotLike(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"items"`,
		Where: []Condition{
			NotLike(`"name"`, "%test"),
		},
	}

	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	wantSQL := `SELECT * FROM "items" WHERE "name" NOT LIKE ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 1 || args[0] != `\%test` {
		t.Errorf("Args = %v, want [percent-escaped]", args)
	}
}

// =============================================================================
// Tests: BuildCount
// =============================================================================

func TestBuildCount(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			Eq(`"status"`, "active"),
		},
		Order: []OrderClause{
			{Field: `"id"`, Desc: true},
		},
		Limit: 10,
	}

	sql, args, err := eng.BuildCount(plan)
	if err != nil {
		t.Fatalf("BuildCount: unexpected error: %v", err)
	}

	// BuildCount заменяет колонки на COUNT(*) и сохраняет WHERE.
	wantSQL := `SELECT COUNT(*) FROM "customers" WHERE "status" = ? ORDER BY "id" DESC LIMIT ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "active" || args[1] != 10 {
		t.Errorf("Args = %v, want [active, 10]", args)
	}
}

func TestBuildCount_NoWhere(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"orders"`,
	}

	sql, args, err := eng.BuildCount(plan)
	if err != nil {
		t.Fatalf("BuildCount: unexpected error: %v", err)
	}

	wantSQL := `SELECT COUNT(*) FROM "orders"`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 0 {
		t.Errorf("Args = %v, want []", args)
	}
}

func TestBuildCount_PostgresPlaceholders(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"customers"`,
		Where: []Condition{
			Eq(`"status"`, "active"),
			Gt(`"age"`, 18),
		},
	}

	sql, args, err := eng.BuildCount(plan)
	if err != nil {
		t.Fatalf("BuildCount: unexpected error: %v", err)
	}

	wantSQL := `SELECT COUNT(*) FROM "customers" WHERE "status" = $1 AND "age" > $2`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
	if len(args) != 2 || args[0] != "active" || args[1] != 18 {
		t.Errorf("Args = %v, want [active, 18]", args)
	}
}

// =============================================================================
// Tests: Error cases
// =============================================================================

func TestBuild_LikeNonString(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	// Use Condition literal to bypass type check (pass non-string)
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			{Field: `"x"`, Operator: OpLike, Value: 42},
		},
	}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("LIKE with non-string value: expected error, got nil")
	}
}

func TestBuild_ILikeNonString(t *testing.T) {
	eng := NewEngine(postgresAdapter{})
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			{Field: `"x"`, Operator: OpILike, Value: 42},
		},
	}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("ILIKE with non-string value: expected error, got nil")
	}
}

func TestBuild_RegexpNonString(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			{Field: `"x"`, Operator: OpRegex, Value: 42},
		},
	}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("REGEXP with non-string value: expected error, got nil")
	}
}

func TestBuild_In_Empty(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			In(`"x"`),
		},
	}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("IN with no values: expected error, got nil")
	}
}

func TestBuild_Between_Not2Values(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	plan := QueryPlan{
		From: `"t"`,
		Where: []Condition{
			{Field: `"x"`, Operator: OpBetween, Values: []any{1}},
		},
	}

	_, _, err := eng.Build(plan)
	if err == nil {
		t.Fatal("BETWEEN with 1 value: expected error, got nil")
	}
}

// =============================================================================
// Tests: FormatRows
// =============================================================================

func TestFormatRows_Compact(t *testing.T) {
	rows := []map[string]any{
		{"id": 1, "name": "Alice", "email": "alice@example.com"},
		{"id": 2, "name": "Bob", "email": "bob@example.com"},
	}

	result := FormatRows(rows, 100, FormatCompact, "id", "name")

	if result.Total != 100 {
		t.Errorf("Total = %d, want 100", result.Total)
	}
	if result.Returned != 2 {
		t.Errorf("Returned = %d, want 2", result.Returned)
	}
	if len(result.Preview) != 2 {
		t.Fatalf("len(Preview) = %d, want 2", len(result.Preview))
	}
	if result.Preview[0].ID != 1 || result.Preview[0].Name != "Alice" {
		t.Errorf("Preview[0] = %+v, want {ID:1 Name:Alice}", result.Preview[0])
	}
	if result.Preview[1].ID != 2 || result.Preview[1].Name != "Bob" {
		t.Errorf("Preview[1] = %+v, want {ID:2 Name:Bob}", result.Preview[1])
	}
	if result.Data != nil {
		t.Errorf("Data should be nil for compact, got %v", result.Data)
	}
}

func TestFormatRows_Full(t *testing.T) {
	rows := []map[string]any{
		{"id": 1, "name": "Alice"},
	}

	result := FormatRows(rows, 1, FormatFull, "id", "name")

	if result.Total != 1 {
		t.Errorf("Total = %d, want 1", result.Total)
	}
	if result.Returned != 1 {
		t.Errorf("Returned = %d, want 1", result.Returned)
	}
	if len(result.Data) != 1 {
		t.Fatalf("len(Data) = %d, want 1", len(result.Data))
	}
	if result.Data[0]["id"] != 1 || result.Data[0]["name"] != "Alice" {
		t.Errorf("Data[0] = %v, want {id:1 name:Alice}", result.Data[0])
	}
	if result.Preview != nil {
		t.Errorf("Preview should be nil for full, got %v", result.Preview)
	}
}

func TestFormatRows_Count(t *testing.T) {
	result := FormatRows(nil, 42, FormatCount, "id", "name")

	if result.Total != 42 {
		t.Errorf("Total = %d, want 42", result.Total)
	}
	if result.Returned != 0 {
		t.Errorf("Returned = %d, want 0", result.Returned)
	}
	if result.Data != nil {
		t.Errorf("Data should be nil for count, got %v", result.Data)
	}
	if result.Preview != nil {
		t.Errorf("Preview should be nil for count, got %v", result.Preview)
	}
}

func TestFormatRows_Compact_NameFallback(t *testing.T) {
	// Если указанная nameCol не найдена, берётся первое строковое поле.
	// Go map iteration is non-deterministic, so we only check that
	// SOME non-empty string value was picked up.
	rows := []map[string]any{
		{"id": 1, "title": "Widget", "color": "red"},
	}

	result := FormatRows(rows, 1, FormatCompact, "id", "name")

	if result.Preview[0].ID != 1 {
		t.Errorf("ID = %v, want 1", result.Preview[0].ID)
	}
	// "name" нет в row → fallback на первое строковое поле (map order non-deterministic)
	if result.Preview[0].Name == "" {
		t.Errorf("Name should be non-empty (fallback to any string field), got empty")
	}
}

func TestFormatRows_Compact_NoStringFields(t *testing.T) {
	rows := []map[string]any{
		{"id": 1, "count": 42},
	}

	result := FormatRows(rows, 1, FormatCompact, "id", "name")

	if result.Preview[0].ID != 1 {
		t.Errorf("ID = %v, want 1", result.Preview[0].ID)
	}
	if result.Preview[0].Name != "" {
		t.Errorf("Name = %q, want empty", result.Preview[0].Name)
	}
}

// =============================================================================
// Tests: Engine edge cases
// =============================================================================

func TestNewEngine_DetectsPostgres(t *testing.T) {
	e := NewEngine(postgresAdapter{})
	if !e.isPostgres {
		t.Error("NewEngine(postgresAdapter): isPostgres should be true")
	}
}

func TestNewEngine_DetectsSQLite(t *testing.T) {
	e := NewEngine(sqliteAdapter{})
	if e.isPostgres {
		t.Error("NewEngine(sqliteAdapter): isPostgres should be false")
	}
}

func TestBuild_EmptyPlan(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	// Plan with only From — valid minimal query
	plan := QueryPlan{From: `"t"`}
	sql, args, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}
	if sql != `SELECT * FROM "t"` {
		t.Errorf("SQL = %q, want %q", sql, `SELECT * FROM "t"`)
	}
	if len(args) != 0 {
		t.Errorf("Args = %v, want []", args)
	}
}

func TestBuild_NotFlag_Neq(t *testing.T) {
	eng := NewEngine(sqliteAdapter{})
	c := Neq(`"x"`, 1)
	c.Not = true
	plan := QueryPlan{
		From:  `"t"`,
		Where: []Condition{c},
	}

	sql, _, err := eng.Build(plan)
	if err != nil {
		t.Fatalf("Build: unexpected error: %v", err)
	}

	// NOT != — двойное отрицание, но синтаксически корректно
	wantSQL := `SELECT * FROM "t" WHERE "x" NOT != ?`
	if sql != wantSQL {
		t.Errorf("SQL = %q, want %q", sql, wantSQL)
	}
}
