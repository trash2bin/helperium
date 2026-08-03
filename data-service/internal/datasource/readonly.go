package datasource

import (
	"context"
	"database/sql"
	"fmt"
)

// ReadOnlyConn — обёртка над существующим Conn для read-only query path.
// ExecContext всегда возвращает ошибку, даже если нижележащее соединение поддерживает write.
// Используется для code-level гарантии, что data query path не пишет в БД.
type ReadOnlyConn struct {
	inner Conn
}

// NewReadOnlyConn создаёт ReadOnlyConn из существующего подключения.
func NewReadOnlyConn(inner Conn) *ReadOnlyConn {
	return &ReadOnlyConn{inner: inner}
}

func (r *ReadOnlyConn) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return r.inner.QueryContext(ctx, query, args...)
}
func (r *ReadOnlyConn) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return r.inner.QueryRowContext(ctx, query, args...)
}
func (r *ReadOnlyConn) PingContext(ctx context.Context) error {
	return r.inner.PingContext(ctx)
}
func (r *ReadOnlyConn) Close() error {
	return r.inner.Close()
}
func (r *ReadOnlyConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return nil, fmt.Errorf("write operations not allowed on read-only connection")
}
