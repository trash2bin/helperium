package runtime_test

import (
	"context"
	"database/sql"
	"strings"
	"testing"

	_ "modernc.org/sqlite" // pure-Go SQLite driver — для тестового AdapterSubset

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

// =============================================================================
// Test helper: in-memory SQLite adapter, реализующий AdapterSubset.
// =============================================================================
//
// Этот helper — единственное место, где тесты runtime касаются
// database/sql. Сам builder использует только AdapterSubset.

// testAdapter — обёртка над *sql.DB, реализующая три метода AdapterSubset.
//
// *sql.DB нативно имеет QueryContext, поэтому мы просто делегируем.
// QuoteIdentifier и TranslatePlaceholder — фиксированные значения
// для SQLite (двойные кавычки и '?').
type testAdapter struct {
	db *sql.DB
}

func (a *testAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}

func (a *testAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (a *testAdapter) TranslatePlaceholder(index int) string {
	// SQLite нативно использует '?'. Index игнорируется.
	return "?"
}

func (a *testAdapter) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

// newTestAdapter поднимает in-memory SQLite с тестовой схемой.
//
// DDL — generic, не привязан к доменной семантике вуза:
//
//	CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT NOT NULL, created_at TEXT);
//	CREATE TABLE orders    (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, total REAL);
//
// Возвращает AdapterSubset + cleanup-функцию (вызывать через defer).
func newTestAdapter(t *testing.T) (runtime.AdapterSubset, func()) {
	t.Helper()

	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open in-memory: %v", err)
	}

	// Один writer для SQLite — иначе "database is locked" в параллельных тестах.
	db.SetMaxOpenConns(1)

	const ddl = `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			email TEXT NOT NULL,
			created_at TEXT
		);
		CREATE TABLE orders (
			id INTEGER PRIMARY KEY,
			customer_id INTEGER NOT NULL,
			total REAL
		);
	`
	if _, err := db.ExecContext(context.Background(), ddl); err != nil {
		_ = db.Close()
		t.Fatalf("apply ddl: %v", err)
	}

	cleanup := func() { _ = db.Close() }
	return &testAdapter{db: db}, cleanup
}

// customerEntity — тестовая Entity для SELECT-операций.
//
// Поля отсортированы так же, как в SELECT — порядок не критичен,
// но удобен для чтения.
func customerEntity() runtime.Entity {
	return runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "email", Column: "email", Type: "string"},
			{Name: "createdAt", Column: "created_at", Type: "datetime", Nullable: true},
		},
	}
}

// =============================================================================
// Tests: query builder (BuildGetByID, BuildFind, BuildList, BuildCustomQuery).
// =============================================================================

func TestBuildGetByID(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)
	q, err := b.BuildGetByID(customerEntity(), 1)
	if err != nil {
		t.Fatalf("BuildGetByID: unexpected error: %v", err)
	}

	// Проверяем структуру SQL: SELECT <cols> FROM <table> WHERE <id_col> = ?
	wantSubstrs := []string{
		`SELECT "id", "email", "created_at"`,
		`FROM "customers"`,
		`WHERE "id" = ?`,
	}
	for _, s := range wantSubstrs {
		if !strings.Contains(q.SQL, s) {
			t.Errorf("SQL missing %q\nwant substrings: %v\nSQL: %s", s, wantSubstrs, q.SQL)
		}
	}

	if len(q.Args) != 1 || q.Args[0] != 1 {
		t.Errorf("Args = %v, want [1]", q.Args)
	}
}

func TestBuildGetByID_UnknownEntity(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)
	// Entity без Table — программная ошибка конфигурации.
	bad := runtime.Entity{Name: "x", IDColumn: "id"}
	_, err := b.BuildGetByID(bad, 1)
	if err == nil {
		t.Fatal("BuildGetByID with empty Table: expected error, got nil")
	}
	// Проверяем что ошибка содержит упоминание операции для диагностики.
	if !strings.Contains(err.Error(), "BuildGetByID") {
		t.Errorf("error %q should mention op BuildGetByID", err)
	}
}

func TestBuildFind(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)
	q, err := b.BuildFind(customerEntity(), "email", "x@y.com")
	if err != nil {
		t.Fatalf("BuildFind: unexpected error: %v", err)
	}

	wantSubstrs := []string{
		`SELECT "id", "email", "created_at"`,
		`FROM "customers"`,
		`WHERE "email" LIKE ?`,
	}
	for _, s := range wantSubstrs {
		if !strings.Contains(q.SQL, s) {
			t.Errorf("SQL missing %q\nSQL: %s", s, q.SQL)
		}
	}

	if len(q.Args) != 1 || q.Args[0] != "%x@y.com%" {
		t.Errorf("Args = %v, want [%%x@y.com%%]", q.Args)
	}
}

func TestBuildFind_UnknownField(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)
	// "phone" не описан в Fields → должна быть ошибка QueryError.
	_, err := b.BuildFind(customerEntity(), "phone", "555")
	if err == nil {
		t.Fatal("BuildFind with unknown field: expected error, got nil")
	}
	if !strings.Contains(err.Error(), "BuildFind") {
		t.Errorf("error %q should mention op BuildFind", err)
	}
}

func TestBuildList(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)

	// Без where.
	q, err := b.BuildList(customerEntity(), "", nil)
	if err != nil {
		t.Fatalf("BuildList (no where): unexpected error: %v", err)
	}
	if !strings.Contains(q.SQL, `SELECT "id", "email", "created_at" FROM "customers"`) {
		t.Errorf("SQL should be clean SELECT, got: %s", q.SQL)
	}
	if strings.Contains(q.SQL, "WHERE") {
		t.Errorf("SQL without where should not contain WHERE, got: %s", q.SQL)
	}

	// С where — builder просто конкатенирует, не валидирует.
	q, err = b.BuildList(customerEntity(), `"id" > ?`, []any{10})
	if err != nil {
		t.Fatalf("BuildList (with where): unexpected error: %v", err)
	}
	if !strings.Contains(q.SQL, `WHERE "id" > ?`) {
		t.Errorf("SQL with where missing WHERE clause, got: %s", q.SQL)
	}
	if len(q.Args) != 1 || q.Args[0] != 10 {
		t.Errorf("Args = %v, want [10]", q.Args)
	}
}

func TestBuildCustomQuery(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)

	cq := runtime.CustomQuery{
		SQL:    `SELECT id, email FROM customers WHERE id = ?`,
		Params: []string{"id"},
		ResultMapping: map[string]runtime.ResultMappingField{
			"id":    {Type: "int"},
			"email": {Type: "string"},
		},
		MaxRows: 100,
	}
	q, err := b.BuildCustomQuery(cq, []any{42})
	if err != nil {
		t.Fatalf("BuildCustomQuery: unexpected error: %v", err)
	}
	// SQLite TranslatePlaceholder → "?", SQL не меняется.
	if !strings.Contains(q.SQL, `WHERE id = ?`) {
		t.Errorf("SQL = %s, want WHERE id = ?", q.SQL)
	}
	if len(q.Args) != 1 || q.Args[0] != 42 {
		t.Errorf("Args = %v, want [42]", q.Args)
	}
}

func TestBuildCustomQuery_ArgCountMismatch(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	b := runtime.NewBuilder(adapter)

	cq := runtime.CustomQuery{
		SQL:    `SELECT * FROM customers WHERE id = ? AND email = ?`,
		Params: []string{"id", "email"},
		ResultMapping: map[string]runtime.ResultMappingField{
			"id":    {Type: "int"},
			"email": {Type: "string"},
		},
	}
	// Передаём 1 аргумент вместо 2 → ошибка.
	_, err := b.BuildCustomQuery(cq, []any{1})
	if err == nil {
		t.Fatal("BuildCustomQuery with arg count mismatch: expected error, got nil")
	}
	if !strings.Contains(err.Error(), "mismatch") {
		t.Errorf("error %q should describe mismatch", err)
	}
}

// =============================================================================
// Tests: isValidSelect validation (via BuildCustomQuery public API).
// =============================================================================
//
// isValidSelect — unexported, тестируем через BuildCustomQuery:
// невалидный SQL → BuildCustomQuery возвращает error, валидный — успех.

// TestBuildCustomQuery_RejectsNonSelect — INSERT, DELETE, DROP должны
// давать ошибку "must be a SELECT statement".
func TestBuildCustomQuery_RejectsNonSelect(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	tests := []struct {
		name string
		sql  string
	}{
		{"INSERT", `INSERT INTO users VALUES(1)`},
		{"UPDATE", `UPDATE users SET name='x'`},
		{"DELETE", `DELETE FROM users`},
		{"DROP", `DROP TABLE users`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cq := runtime.CustomQuery{
				SQL:           tt.sql,
				Params:        []string{},
				ResultMapping: map[string]runtime.ResultMappingField{},
				MaxRows:       100,
			}
			_, err := b.BuildCustomQuery(cq, []any{})
			if err == nil {
				t.Fatal("expected error for non-SELECT SQL, got nil")
			}
			if !strings.Contains(err.Error(), "must be a SELECT statement") {
				t.Errorf("error = %q, want to contain 'must be a SELECT statement'", err)
			}
		})
	}
}

func TestBuildCustomQuery_AcceptsSelect(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	cq := runtime.CustomQuery{
		SQL:           `SELECT * FROM customers`,
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err != nil {
		t.Fatalf("expected no error for valid SELECT, got: %v", err)
	}
}

func TestBuildCustomQuery_AcceptsSelectWithWhitespace(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	cq := runtime.CustomQuery{
		SQL:           `  SELECT 1`,
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err != nil {
		t.Fatalf("expected no error for SELECT with whitespace, got: %v", err)
	}
}

func TestBuildCustomQuery_AcceptsWithCTE(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	// WITH-запрос — валидный SELECT (был сломан в looksLikeSelect).
	cq := runtime.CustomQuery{
		SQL:           `WITH cte AS (SELECT * FROM customers) SELECT * FROM cte`,
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err != nil {
		t.Fatalf("expected no error for WITH CTE, got: %v", err)
	}
}

func TestBuildCustomQuery_RejectsMultiStatement(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	// SELECT 1; DROP TABLE — multi-statement guard.
	cq := runtime.CustomQuery{
		SQL:           `SELECT 1; DROP TABLE users`,
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err == nil {
		t.Fatal("expected error for multi-statement SQL, got nil")
	}
	if !strings.Contains(err.Error(), "must be a SELECT statement") {
		t.Errorf("error = %q, want to contain 'must be a SELECT statement'", err)
	}
}

func TestBuildCustomQuery_RejectsMySqlCommentBypass(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	// /*! SELECT */ DROP TABLE — MySQL comment bypass.
	cq := runtime.CustomQuery{
		SQL:           `/*! SELECT */ DROP TABLE users`,
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err == nil {
		t.Fatal("expected error for MySQL comment bypass, got nil")
	}
	if !strings.Contains(err.Error(), "must be a SELECT statement") {
		t.Errorf("error = %q, want to contain 'must be a SELECT statement'", err)
	}
}

func TestBuildCustomQuery_AcceptsLineCommentBeforeSelect(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()
	b := runtime.NewBuilder(adapter)

	// -- comment\nSELECT 1 — line comment before SELECT is ok.
	cq := runtime.CustomQuery{
		SQL:           "-- comment\nSELECT 1",
		Params:        []string{},
		ResultMapping: map[string]runtime.ResultMappingField{},
		MaxRows:       100,
	}
	_, err := b.BuildCustomQuery(cq, []any{})
	if err != nil {
		t.Fatalf("expected no error for line comment before SELECT, got: %v", err)
	}
}

// =============================================================================
// Tests: response mapper.
// =============================================================================

func TestMapRow(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	// Вставляем одну строку через testAdapter (он же открыл БД).
	db := adapter.(*testAdapter).db
	if _, err := db.ExecContext(ctx,
		`INSERT INTO customers (id, email, created_at) VALUES (?, ?, ?)`,
		1, "alice@example.com", "2024-01-01",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	b := runtime.NewBuilder(adapter)
	rows, err := adapter.QueryContext(ctx, `SELECT id, email, created_at FROM customers WHERE id = ?`, 1)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	row, err := b.MapRow(rows, customerEntity())
	if err != nil {
		t.Fatalf("MapRow: %v", err)
	}

	// Проверяем, что ключи — публичные имена (id, email, createdAt),
	// а НЕ колонки БД (id, email, created_at).
	wantKeys := []string{"id", "email", "createdAt"}
	for _, k := range wantKeys {
		if _, ok := row[k]; !ok {
			t.Errorf("row missing key %q, got: %v", k, row)
		}
	}
	// created_at НЕ должен попасть в результат — это имя колонки БД,
	// а публичное имя поля — createdAt.
	if _, ok := row["created_at"]; ok {
		t.Errorf("row should not contain DB column name 'created_at', got: %v", row)
	}
}

func TestMapRows_MaxRowsLimit(t *testing.T) {
	adapter, cleanup := newTestAdapter(t)
	defer cleanup()

	ctx := context.Background()
	db := adapter.(*testAdapter).db

	// Вставляем 5 строк.
	for i := 1; i <= 5; i++ {
		if _, err := db.ExecContext(ctx,
			`INSERT INTO customers (id, email, created_at) VALUES (?, ?, ?)`,
			i, "u"+string(rune('0'+i))+"@example.com", nil,
		); err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}

	b := runtime.NewBuilder(adapter)
	rows, err := adapter.QueryContext(ctx, `SELECT id, email, created_at FROM customers ORDER BY id`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}

	mapper := func(r *sql.Rows) (map[string]any, error) {
		return b.MapRow(r, customerEntity())
	}

	out, err := b.MapRows(rows, mapper, 2)
	if err != nil {
		t.Fatalf("MapRows: %v", err)
	}

	if len(out) != 2 {
		t.Fatalf("MapRows with maxRows=2: got %d rows, want 2", len(out))
	}
}
