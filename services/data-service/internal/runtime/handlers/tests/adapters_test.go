package handlers_test

import (
	"context"
	"database/sql"
)

// testAdapter — обёртка над *sql.DB, реализующая AdapterSubset.
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
	return "?"
}

func (a *testAdapter) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

// errorAdapter — адаптер, который возвращает ошибку при выполнении запросов.
type errorAdapter struct {
	db      *testAdapter
	errFunc func(context.Context, string, ...any) (*sql.Rows, error)
}

func (e *errorAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	if e.errFunc != nil {
		return e.errFunc(ctx, query, args...)
	}
	return nil, nil
}

func (e *errorAdapter) QuoteIdentifier(name string) string {
	return e.db.QuoteIdentifier(name)
}

func (e *errorAdapter) TranslatePlaceholder(index int) string {
	return e.db.TranslatePlaceholder(index)
}

func (e *errorAdapter) PingContext(ctx context.Context) error {
	return e.db.PingContext(ctx)
}
