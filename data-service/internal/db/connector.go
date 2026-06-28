// Package db предоставляет абстракцию над драйвером БД.
//
// Архитектура фазы 3.1:
//   - DB (этот файл) — низкоуровневый интерфейс к database/sql (как раньше).
//   - internal/datasource — адаптеры поверх DB с интроспекцией схемы.
//
// Connector выбирает driver по переменной окружения DB_DRIVER
// и открывает соединение через соответствующий datasource.Adapter.
//
// Backward-compat:
//   - DB_DRIVER=sqlite (default) — поведение как раньше, путь из DB_PATH
//     или дефолтный "university.db".
//   - DB_DRIVER=postgres — DSN из DATABASE_URL (как было объявлено).
//
// В фазе 3.2 connector будет читать cfg.data_source.dsn и cfg.data_source.driver
// вместо env. Текущая логика сохранена для backward-compat на время миграции.
package db

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	"github.com/agent-tutor/data-service/internal/datasource"
)

// DB — интерфейс базы данных, используемый репозиториями (пока).
// В фазе 3.3 репозитории удаляются, и этот интерфейс останется только как
// низкоуровневая абстракция для datasource-адаптеров.
type DB interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
	PingContext(ctx context.Context) error
	Close() error
}

// New открывает соединение с БД на основе переменных окружения:
//   DB_DRIVER:    "sqlite" (по умолчанию) или "postgres"
//   DB_PATH:      путь к файлу SQLite (по умолчанию university.db)
//   DATABASE_URL: строка подключения PostgreSQL
//
// Внутри — через datasource.Registry, чтобы вызов драйвера шёл единым путём
// с интроспекцией. Это убирает дублирование между SQLite (env+path) и Postgres
// (env+DSN) — оба теперь открываются через один контракт.
func New() (DB, error) {
	driver := os.Getenv("DB_DRIVER")
	if driver == "" {
		driver = "sqlite"
	}

	registry := datasource.NewDefaultRegistry()
	adapter, ok := registry.Get(driver)
	if !ok {
		return nil, fmt.Errorf("unknown DB_DRIVER: %s (registered: %v)", driver, registry.Drivers())
	}

	dsn, err := resolveDSN(driver)
	if err != nil {
		return nil, fmt.Errorf("%s: resolve dsn: %w", driver, err)
	}

	return adapter.Connect(context.Background(), dsn)
}

// resolveDSN возвращает DSN для указанного driver из переменных окружения.
//   - sqlite:    DB_PATH (default: university.db), абсолютный путь.
//   - postgres:  DATABASE_URL.
func resolveDSN(driver string) (string, error) {
	switch driver {
	case "sqlite":
		path := os.Getenv("DB_PATH")
		if path == "" {
			path = "university.db"
		}
		absPath, err := filepath.Abs(path)
		if err != nil {
			return "", fmt.Errorf("resolve path %q: %w", path, err)
		}
		return absPath, nil
	case "postgres":
		url := os.Getenv("DATABASE_URL")
		if url == "" {
			return "", fmt.Errorf("DATABASE_URL is required for postgres driver")
		}
		return url, nil
	default:
		return "", fmt.Errorf("unsupported driver %q", driver)
	}
}
