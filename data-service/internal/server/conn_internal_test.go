package server

import (
	"context"
	"database/sql"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// TestConnAdapter verifies the delegation methods.
func TestConnAdapter_VerifyDelegation(t *testing.T) {
	// ConnAdapter with mock conn/adapter
	ca := &ConnAdapter{
		Conn: &mockConn{},
		Adp:  &mockAdapterForConn{},
	}

	// These should not panic and return expected values
	got := ca.QuoteIdentifier("test")
	if got != `"test"` {
		t.Errorf("QuoteIdentifier = %q", got)
	}

	got2 := ca.TranslatePlaceholder(3)
	if got2 != "$3" {
		t.Errorf("TranslatePlaceholder = %q", got2)
	}

	err := ca.PingContext(context.Background())
	if err != nil {
		t.Errorf("PingContext = %v, want nil", err)
	}
}

type mockConn struct{}

func (m *mockConn) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return nil, nil
}
func (m *mockConn) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return nil
}
func (m *mockConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return nil, nil
}
func (m *mockConn) PingContext(ctx context.Context) error { return nil }
func (m *mockConn) Close() error                         { return nil }

type mockAdapterForConn struct{}

func (m *mockAdapterForConn) Driver() string                     { return "mock" }
func (m *mockAdapterForConn) QuoteIdentifier(name string) string  { return `"` + name + `"` }
func (m *mockAdapterForConn) TranslatePlaceholder(idx int) string { return "$3" }
func (m *mockAdapterForConn) Connect(ctx context.Context, dsn string) (datasource.Conn, error) {
	return &mockConn{}, nil
}
func (m *mockAdapterForConn) Introspect(ctx context.Context, conn datasource.Conn) (*datasource.Schema, error) {
	return nil, nil
}
