package db

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

// SQLiteDB реализует интерфейс DB через modernc.org/sqlite (чистый Go, без CGO).
type SQLiteDB struct {
	conn *sql.DB
	path string
}

// NewSQLite открывает (или создаёт) файл SQLite.
// Путь к файлу берётся из DB_PATH или дефолтного university.db.
func NewSQLite() (*SQLiteDB, error) {
	path := os.Getenv("DB_PATH")
	if path == "" {
		path = "university.db"
	}

	// Разрешаем относительный путь относительно рабочей директории
	absPath, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("sqlite: failed to resolve path %q: %w", path, err)
	}

	slog.Info("opening sqlite database", "path", absPath)

	conn, err := sql.Open("sqlite", absPath+"?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		return nil, fmt.Errorf("sqlite: failed to open %q: %w", absPath, err)
	}

	// Проверяем соединение
	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("sqlite: ping failed for %q: %w", absPath, err)
	}

	slog.Info("sqlite database ready", "path", absPath)

	return &SQLiteDB{conn: conn, path: absPath}, nil
}

func (s *SQLiteDB) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return s.conn.QueryRowContext(ctx, query, args...)
}

func (s *SQLiteDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return s.conn.QueryContext(ctx, query, args...)
}

func (s *SQLiteDB) PingContext(ctx context.Context) error {
	return s.conn.PingContext(ctx)
}

func (s *SQLiteDB) Close() error {
	slog.Info("closing sqlite database", "path", s.path)
	return s.conn.Close()
}
