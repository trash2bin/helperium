package runtime_test

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/agent-tutor/data-service/internal/runtime"
)

// benchmarkAdapter — подготавливает in-memory SQLite для бенчмарков
type benchmarkAdapter struct {
	db *sql.DB
}

func (a *benchmarkAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *benchmarkAdapter) QuoteIdentifier(name string) string { return `"` + name + `"` }
func (a *benchmarkAdapter) TranslatePlaceholder(idx int) string { return "?" }
func (a *benchmarkAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }

func benchEntity() runtime.Entity {
	return runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
			{Name: "score", Column: "score", Type: "float"},
			{Name: "active", Column: "active", Type: "bool"},
		},
	}
}

// BenchmarkBuildGetByID — сборка запроса SELECT по ID
func BenchmarkBuildGetByID(b *testing.B) {
	adapter := &benchmarkAdapter{}
	builder := runtime.NewBuilder(adapter)
	entity := benchEntity()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := builder.BuildGetByID(entity, 42)
		if err != nil {
			b.Fatalf("BuildGetByID: %v", err)
		}
	}
}

// BenchmarkBuildList — сборка запроса SELECT без WHERE
func BenchmarkBuildList(b *testing.B) {
	adapter := &benchmarkAdapter{}
	builder := runtime.NewBuilder(adapter)
	entity := benchEntity()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := builder.BuildList(entity, "", nil)
		if err != nil {
			b.Fatalf("BuildList: %v", err)
		}
	}
}

// BenchmarkBuildList_WithWhere — сборка запроса с WHERE
func BenchmarkBuildList_WithWhere(b *testing.B) {
	adapter := &benchmarkAdapter{}
	builder := runtime.NewBuilder(adapter)
	entity := benchEntity()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := builder.BuildList(entity, "active = ?", []any{true})
		if err != nil {
			b.Fatalf("BuildList with where: %v", err)
		}
	}
}

// BenchmarkBuildFind — сборка запроса поиска
func BenchmarkBuildFind(b *testing.B) {
	adapter := &benchmarkAdapter{}
	builder := runtime.NewBuilder(adapter)
	entity := benchEntity()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := builder.BuildFind(entity, "email", "test@example.com")
		if err != nil {
			b.Fatalf("BuildFind: %v", err)
		}
	}
}

// BenchmarkBuildCustomQuery — сборка кастомного запроса
func BenchmarkBuildCustomQuery(b *testing.B) {
	adapter := &benchmarkAdapter{}
	builder := runtime.NewBuilder(adapter)

	cq := runtime.CustomQuery{
		SQL:    "SELECT id, name, email, score, active FROM customers WHERE id = ? AND email = ?",
		Params: []string{"id", "email"},
		MaxRows: 100,
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := builder.BuildCustomQuery(cq, []any{1, "test@example.com"})
		if err != nil {
			b.Fatalf("BuildCustomQuery: %v", err)
		}
	}
}

// BenchmarkMapRow — сканирование строки из SQLite с type coercion
func BenchmarkMapRow(b *testing.B) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		b.Fatalf("sql.Open: %v", err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL,
			score REAL,
			active INTEGER
		);
		INSERT INTO customers (id, name, email, score, active) VALUES
			(1, 'John Doe', 'john@example.com', 95.5, 1);
	`)

	adapter := &benchmarkAdapter{db: db}
	builder := runtime.NewBuilder(adapter)
	entity := benchEntity()

	rows, err := db.QueryContext(context.Background(),
		`SELECT id, name, email, score, active FROM customers WHERE id = ?`, 1)
	if err != nil {
		b.Fatalf("query: %v", err)
	}
	defer rows.Close()

	if !rows.Next() {
		b.Fatal("no rows")
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		row, err := builder.MapRow(rows, entity)
		if err != nil {
			b.Fatalf("MapRow: %v", err)
		}
		_ = row
		// Перезапрашиваем строку для следующей итерации
		if !rows.Next() {
			rows.Close()
			rows, err = db.QueryContext(context.Background(),
				`SELECT id, name, email, score, active FROM customers WHERE id = ?`, 1)
			if err != nil {
				b.Fatalf("re-query: %v", err)
			}
			rows.Next()
		}
	}
	rows.Close()
}

// BenchmarkMapRow_CoerceInt — type coercion int
func BenchmarkMapRow_CoerceInt(b *testing.B) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		b.Fatalf("sql.Open: %v", err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE types (id INTEGER PRIMARY KEY, val INTEGER);
		INSERT INTO types (id, val) VALUES (1, 42);
	`)

	entity := runtime.Entity{
		Name: "types", Table: "types", IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "val", Column: "val", Type: "int"},
		},
	}

	adapter := &benchmarkAdapter{db: db}
	builder := runtime.NewBuilder(adapter)

	rows, _ := db.QueryContext(context.Background(), `SELECT id, val FROM types WHERE id = 1`)
	defer rows.Close()
	rows.Next()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		row, _ := builder.MapRow(rows, entity)
		_ = row
		if !rows.Next() {
			rows.Close()
			rows, _ = db.QueryContext(context.Background(), `SELECT id, val FROM types WHERE id = 1`)
			rows.Next()
		}
	}
	rows.Close()
}