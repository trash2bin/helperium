package db

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// PostgresDB реализует интерфейс DB через pgx/v5 stdlib (database/sql совместимый драйвер).
type PostgresDB struct {
	conn *sql.DB
	dsn  string
}

// NewPostgres открывает соединение с PostgreSQL через pgx/v5 stdlib,
// настраивает пул и пингует сервер для проверки доступности.
//
// Ожидается DSN в одном из форматов:
//   - URL:     postgres://user:password@host:port/dbname?sslmode=disable
//   - Keyword: host=... user=... password=... dbname=... port=...
//
// В логах DSN маскируется (пароль скрыт).
func NewPostgres(dsn string) (*PostgresDB, error) {
	if dsn == "" {
		return nil, fmt.Errorf("postgres: empty DSN")
	}

	slog.Info("opening postgres database", "dsn", maskDSN(dsn))

	conn, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres: failed to open: %w", err)
	}

	conn.SetMaxOpenConns(25)
	conn.SetMaxIdleConns(5)
	conn.SetConnMaxLifetime(5 * time.Minute)

	// Проверяем соединение. На этом этапе выявляются неверные учётные данные,
	// недоступный хост и прочие ошибки подключения.
	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("postgres: ping failed: %w", err)
	}

	slog.Info("postgres database ready", "dsn", maskDSN(dsn))

	return &PostgresDB{conn: conn, dsn: dsn}, nil
}

func (p *PostgresDB) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return p.conn.QueryRowContext(ctx, query, args...)
}

func (p *PostgresDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return p.conn.QueryContext(ctx, query, args...)
}

func (p *PostgresDB) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return p.conn.ExecContext(ctx, query, args...)
}

func (p *PostgresDB) PingContext(ctx context.Context) error {
	return p.conn.PingContext(ctx)
}

func (p *PostgresDB) Close() error {
	slog.Info("closing postgres database", "dsn", maskDSN(p.dsn))
	return p.conn.Close()
}

func (p *PostgresDB) String() string {
	return "postgres:" + maskDSN(p.dsn)
}

// maskDSN скрывает пароль в DSN при логировании. Поддерживает URL-формат
// (postgres://user:pass@host:port/db?...) и keyword-формат
// (host=... user=... password=secret ...).
//
// Возвращает исходную строку без изменений, если её не удалось распарсить —
// это сознательное решение: лучше показать DSN as-is, чем падать в логе.
func maskDSN(s string) string {
	if s == "" {
		return ""
	}

	// URL-формат. Собираем строку вручную, чтобы избежать re-encoding
	// (url.URL.String() превратит `***` в `%2A%2A%2A`).
	if strings.Contains(s, "://") {
		u, err := url.Parse(s)
		if err != nil {
			return s
		}
		if u.User != nil {
			if _, hasPassword := u.User.Password(); hasPassword {
				username := u.User.Username()
				scheme := u.Scheme
				u.User = nil
				rest := u.String()[len(scheme)+len("://"):]
				return scheme + "://" + username + ":***@" + rest
			}
		}
		return s
	}

	// Keyword-формат (key=value key="value" ...).
	masked := s
	for _, key := range []string{"password", "Password", "PASSWORD"} {
		// Схема: key=value или key="value with spaces"
		prefix := key + "="
		idx := strings.Index(masked, prefix)
		if idx < 0 {
			continue
		}
		rest := masked[idx+len(prefix):]
		// quoted value
		if strings.HasPrefix(rest, `"`) {
			end := strings.Index(rest[1:], `"`)
			if end < 0 {
				continue
			}
			masked = masked[:idx+len(prefix)] + `"***"` + rest[1+end+1:]
		} else {
			// bare value — до следующего пробела
			sp := strings.IndexAny(rest, " \t")
			if sp < 0 {
				masked = masked[:idx+len(prefix)] + "***"
			} else {
				masked = masked[:idx+len(prefix)] + "***" + rest[sp:]
			}
		}
		break
	}
	return masked
}