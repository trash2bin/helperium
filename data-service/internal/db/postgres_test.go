package db

import (
	"context"
	"net"
	"os"
	"strings"
	"testing"
	"time"
)

// TestNewPostgres_BadDSN проверяет, что NewPostgres возвращает ошибку за разумное
// время при недоступном хосте. Используется заведомо недостижимый адрес
// (TEST-NET-1 192.0.2.1) — пакеты не должны маршрутизироваться, поэтому таймаут
// TCP быстро сработает и тест завершится < 10 секунд даже на медленных машинах.
func TestNewPostgres_BadDSN(t *testing.T) {
	// 192.0.2.0/24 — TEST-NET-1 (RFC 5737), гарантированно не маршрутизируется в публичном интернете.
	// Порт 1 — зарезервирован и обычно не слушается.
	dsn := "postgres://user:secret@192.0.2.1:1/dbname?sslmode=disable&connect_timeout=3"

	start := time.Now()
	db, err := NewPostgres(dsn)
	elapsed := time.Since(start)

	if err == nil {
		if db != nil {
			db.Close()
		}
		t.Fatalf("expected error for unreachable host, got nil (elapsed=%s)", elapsed)
	}

	if elapsed >= 10*time.Second {
		t.Fatalf("NewPostgres took too long on bad DSN: %s (expected < 10s)", elapsed)
	}

	// Сообщение должно указывать на проблему с ping'ом.
	if !strings.Contains(err.Error(), "postgres") {
		t.Errorf("expected error message to mention 'postgres', got: %v", err)
	}

	t.Logf("got expected error after %s: %v", elapsed, err)
}

// TestMaskDSN проверяет, что пароль в DSN маскируется в обоих форматах.
// Не требует реального PostgreSQL — тестирует только утилиту маскирования.
func TestMaskDSN(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "URL form with password",
			in:   "postgres://alice:supersecret@db.local:5432/mydb?sslmode=disable",
			want: "postgres://alice:***@db.local:5432/mydb?sslmode=disable",
		},
		{
			name: "URL form without password",
			in:   "postgres://bob@db.local:5432/mydb",
			want: "postgres://bob@db.local:5432/mydb",
		},
		{
			name: "keyword form",
			in:   "host=db.local user=alice password=supersecret dbname=mydb port=5432",
			want: "host=db.local user=alice password=*** dbname=mydb port=5432",
		},
		{
			name: "empty",
			in:   "",
			want: "",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := maskDSN(tc.in)
			if got != tc.want {
				t.Errorf("maskDSN(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

// TestNewPostgres_Integration запускается только если задана переменная окружения
// POSTGRES_TEST_URL. Без неё тест скипается — мы не хотим требовать реальный
// PostgreSQL для CI, который работает только с SQLite.
//
// Формат POSTGRES_TEST_URL:
//   postgres://user:password@host:port/dbname?sslmode=disable
func TestNewPostgres_Integration(t *testing.T) {
	dsn := os.Getenv("POSTGRES_TEST_URL")
	if dsn == "" {
		t.Skip("POSTGRES_TEST_URL not set")
	}

	// Быстрый pre-flight: проверяем, что хост вообще отвечает на TCP.
	// Это позволяет получить более понятный скип, если PostgreSQL не запущен,
	// вместо долгого таймаута на уровне драйвера.
	if !canDialDSN(t, dsn, 2*time.Second) {
		t.Skipf("POSTGRES_TEST_URL host is not reachable: %s", maskDSN(dsn))
	}

	db, err := NewPostgres(dsn)
	if err != nil {
		t.Fatalf("NewPostgres: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })

	if err := db.PingContext(context.Background()); err != nil {
		t.Fatalf("PingContext: %v", err)
	}

	var one int
	if err := db.QueryRowContext(context.Background(), "SELECT 1").Scan(&one); err != nil {
		t.Fatalf("SELECT 1: %v", err)
	}
	if one != 1 {
		t.Fatalf("SELECT 1 returned %d, want 1", one)
	}

	t.Logf("integration OK via %s", db)
}

// canDialDSN пытается открыть TCP-соединение к хосту из URL-DSN.
// Возвращает true, если соединение установлено в течение timeout.
func canDialDSN(t *testing.T, dsn string, timeout time.Duration) bool {
	t.Helper()

	const prefix = "postgres://"
	if !strings.HasPrefix(dsn, prefix) {
		// keyword-формат — пропускаем pre-flight, пусть драйвер ругается.
		return true
	}

	// Минимальный парсинг: postgres://user:pass@host:port/db?...
	at := strings.Index(dsn, "@")
	if at < 0 {
		return true
	}
	hostPart := dsn[at+1:]
	q := strings.Index(hostPart, "?")
	if q >= 0 {
		hostPart = hostPart[:q]
	}
	slash := strings.Index(hostPart, "/")
	if slash >= 0 {
		hostPart = hostPart[:slash]
	}

	host, port, err := net.SplitHostPort(hostPart)
	if err != nil {
		return true // плохой формат — пусть драйвер сообщит
	}
	if host == "" {
		return true
	}

	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), timeout)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}