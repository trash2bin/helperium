package handlers

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)




type testDB struct {
	db *sql.DB
}

func (a *testDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}

func (a *testDB) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (a *testDB) TranslatePlaceholder(index int) string {
	return "?"
}

func (a *testDB) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

func TestRunCountQuery_CancelledContext(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `CREATE TABLE test (id INT)`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testDB{db: db}

	// Создаём отменённый контекст
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // отменяем сразу

	// runCountQuery с отменённым контекстом должен вернуть -1
	got := runCountQuery(ctx, adapter, "SELECT COUNT(*) FROM test", nil)
	if got != -1 {
		t.Errorf("runCountQuery with cancelled context = %d, want -1", got)
	}
}

func TestRunCountQuery_Success(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE test (id INT);
		INSERT INTO test VALUES (1), (2), (3);
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testDB{db: db}

	got := runCountQuery(context.Background(), adapter, "SELECT COUNT(*) FROM test", nil)
	if got != 3 {
		t.Errorf("runCountQuery = %d, want 3", got)
	}
}
