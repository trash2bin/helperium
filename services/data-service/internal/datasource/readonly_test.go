package datasource

import (
	"context"
	"database/sql"
	"testing"
)

// recordingConn records whether the write path was ever reached. The
// read-only wrapper must reject writes before touching the inner connection.
type recordingConn struct {
	execCalls int
}

func (c *recordingConn) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return nil
}

func (c *recordingConn) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return nil, nil
}

func (c *recordingConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	c.execCalls++
	return nil, nil
}

func (c *recordingConn) PingContext(ctx context.Context) error { return nil }
func (c *recordingConn) Close() error                          { return nil }

func TestReadOnlyConn_ExecContextRejectsWithoutReachingInnerConn(t *testing.T) {
	inner := &recordingConn{}
	conn := NewReadOnlyConn(inner)
	ctx := context.Background()

	for _, query := range []string{
		"INSERT INTO products(name) VALUES('x')",
		"UPDATE products SET name = 'x'",
		"DELETE FROM products",
		"DROP TABLE products",
		"CREATE TABLE t(id int)",
		"ALTER TABLE products ADD COLUMN c int",
		"SELECT 1; INSERT INTO products(name) VALUES('x')",
		"PRAGMA journal_mode = DELETE",
	} {
		if _, err := conn.ExecContext(ctx, query); err == nil {
			t.Fatalf("ExecContext(%q) unexpectedly succeeded", query)
		}
	}

	if inner.execCalls != 0 {
		t.Fatalf("inner ExecContext reached %d times, want 0", inner.execCalls)
	}
}
