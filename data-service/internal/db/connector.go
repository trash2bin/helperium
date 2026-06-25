// Package db предоставляет абстракцию над драйвером БД.
// Репозитории зависят от интерфейса DB, а не от конкретного драйвера.
package db

import (
	"context"
	"database/sql"
	"fmt"
	"os"
)

// DB — интерфейс базы данных, используемый репозиториями.
// Абстрагирует database/sql, чтобы можно было подменить SQLite на PostgreSQL
// без изменения кода репозиториев.
type DB interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
	PingContext(ctx context.Context) error
	Close() error
}

// New открывает соединение с БД на основе переменных окружения:
//   DB_DRIVER: "sqlite" (по умолчанию) или "postgres"
//   DB_PATH:   путь к файлу SQLite (по умолчанию university.db)
//   DATABASE_URL: строка подключения PostgreSQL
func New() (DB, error) {
	driver := os.Getenv("DB_DRIVER")
	if driver == "" {
		driver = "sqlite"
	}

	switch driver {
	case "sqlite":
		return NewSQLite()
	case "postgres":
		return nil, fmt.Errorf("postgres driver not implemented yet — use DB_DRIVER=sqlite")
	default:
		return nil, fmt.Errorf("unknown DB_DRIVER: %s (expected 'sqlite' or 'postgres')", driver)
	}
}
