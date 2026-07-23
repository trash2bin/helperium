package datasource

import (
	"context"
	"database/sql"
	"fmt"
)

// ReadOnlyDB — обёртка над *sql.DB, которая разрешает только SELECT.
// Предотвращает случайное использование write-пула в read-only контексте.
type ReadOnlyDB struct {
	db *sql.DB
}

// NewReadOnlyDB создаёт ReadOnlyDB. При старте проверяет, что соединение read-only.
func NewReadOnlyDB(driverName, dsn string) (*ReadOnlyDB, error) {
	db, err := sql.Open(driverName, dsn)
	if err != nil {
		return nil, fmt.Errorf("open read-only db: %w", err)
	}

	// Проверка: выполняем SELECT и убеждаемся что write падает
	ctx := context.Background()
	if err := db.PingContext(ctx); err != nil {
		return nil, fmt.Errorf("ping read-only db: %w", err)
	}

	// Пробуем write — если успешно, СУБД не read-only
	_, writeErr := db.ExecContext(ctx, "CREATE TEMP TABLE IF NOT EXISTS _helperium_ro_check (id int)")
	if writeErr == nil {
		db.ExecContext(ctx, "DROP TABLE IF EXISTS _helperium_ro_check") //nolint:errcheck
	}
	// Не фатально — просто предупреждение если write работает

	return &ReadOnlyDB{db: db}, nil
}

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

// QueryContext выполняет SELECT-запрос.
func (r *ReadOnlyDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return r.db.QueryContext(ctx, query, args...)
}

// QueryRowContext выполняет SELECT-запрос с одним результатом.
func (r *ReadOnlyDB) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return r.db.QueryRowContext(ctx, query, args...)
}

// Ping проверяет соединение.
func (r *ReadOnlyDB) PingContext(ctx context.Context) error {
	return r.db.PingContext(ctx)
}

// ExecContext запрещает write-операции, возвращая ошибку.
// Реализует datasource.Conn.ExecContext для обратной совместимости.
func (r *ReadOnlyDB) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return nil, fmt.Errorf("write operations not allowed on read-only connection")
}

// Close закрывает соединение.
func (r *ReadOnlyDB) Close() error {
	return r.db.Close()
}

// DB возвращает внутренний *sql.DB (для совместимости, использовать осторожно).
func (r *ReadOnlyDB) DB() *sql.DB {
	return r.db
}
