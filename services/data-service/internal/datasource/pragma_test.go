package datasource

import (
	"context"
	"strings"
	"testing"
)

// TestEnsurePragmaParams_AddsParams — ensurePragmaParams добавляет _pragma
// параметры в DSN без них (голый путь и DSN с query).
// mode=ro DSN — read-only: НЕ должен получать write-прагмы (journal_mode WAL,
// foreign_keys — требует write при переключении), только read-безопасные.
func TestEnsurePragmaParams_AddsParams(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"/tmp/db.sqlite", "/tmp/db.sqlite?_pragma="},
		{"file:/tmp/db.sqlite?mode=ro", "file:/tmp/db.sqlite?mode=ro&_pragma="},
	}
	for _, c := range cases {
		got := ensurePragmaParams(c.in)
		if !strings.HasPrefix(got, c.want) {
			t.Errorf("ensurePragmaParams(%q) = %q, want prefix %q", c.in, got, c.want)
		}
		if strings.Contains(c.in, "mode=ro") {
			// Read-only: НЕ должно быть write-прагм (WAL, foreign_keys).
			if strings.Contains(got, "journal_mode(WAL)") {
				t.Errorf("ensurePragmaParams(%q) read-only DSN should NOT get journal_mode(WAL): %q", c.in, got)
			}
			if strings.Contains(got, "foreign_keys") {
				t.Errorf("ensurePragmaParams(%q) read-only DSN should NOT get foreign_keys: %q", c.in, got)
			}
			if !strings.Contains(got, "busy_timeout(5000)") {
				t.Errorf("ensurePragmaParams(%q) read-only DSN should keep busy_timeout: %q", c.in, got)
			}
		} else {
			if !strings.Contains(got, "_pragma=foreign_keys(1)") {
				t.Errorf("ensurePragmaParams(%q) missing foreign_keys pragma: %q", c.in, got)
			}
			if !strings.Contains(got, "_pragma=busy_timeout(5000)") {
				t.Errorf("ensurePragmaParams(%q) missing busy_timeout pragma: %q", c.in, got)
			}
		}
	}
}

// TestEnsurePragmaParams_ReadOnly — read-only DSN (mode=ro / immutable=1)
// не получает write-прагм; read-write DSN получает полный набор.
func TestEnsurePragmaParams_ReadOnly(t *testing.T) {
	ro := ensurePragmaParams("file:/data/ro.db?mode=ro")
	if strings.Contains(ro, "journal_mode(WAL)") || strings.Contains(ro, "foreign_keys") {
		t.Errorf("read-only DSN got write pragmas: %q", ro)
	}
	imm := ensurePragmaParams("file:/data/imm.db?immutable=1")
	if strings.Contains(imm, "journal_mode(WAL)") || strings.Contains(imm, "foreign_keys") {
		t.Errorf("immutable DSN got write pragmas: %q", imm)
	}
	rw := ensurePragmaParams("/data/rw.db")
	if !strings.Contains(rw, "journal_mode(WAL)") || !strings.Contains(rw, "foreign_keys(1)") {
		t.Errorf("read-write DSN should get full pragmas: %q", rw)
	}
}

// TestConnect_ReadOnlyDSN_NoWAL — L-фикс: Connect с mode=ro DSN не падает
// (раньше journal_mode(WAL) write-прагма давала "attempt to write a
// readonly database"). Живой прогон на реальном файле БД.
func TestConnect_ReadOnlyDSN_NoWAL(t *testing.T) {
	dir := t.TempDir()
	ro := dir + "/ro.db"

	// Создаём файл БД (write доступ есть у теста).
	w, err := (SqliteAdapter{}).Connect(context.Background(), ro)
	if err != nil {
		t.Fatalf("Connect (write): %v", err)
	}
	if _, err := w.ExecContext(context.Background(), "CREATE TABLE IF NOT EXISTS t (id INTEGER)"); err != nil {
		t.Fatalf("create: %v", err)
	}
	w.Close() //nolint:errcheck

	// Read-only DSN: должен открыться без WAL-ошибки.
	roDSN := "file:" + ro + "?mode=ro"
	c, err := (SqliteAdapter{}).Connect(context.Background(), roDSN)
	if err != nil {
		t.Fatalf("Connect read-only DSN failed (WAL pragma bug?): %v", err)
	}
	defer c.Close() //nolint:errcheck

	var n int
	if err := c.QueryRowContext(context.Background(), "SELECT count(*) FROM t").Scan(&n); err != nil {
		t.Fatalf("read-only query: %v", err)
	}
}

// TestConnect_RegexpFunction — F-REGEXP: grep strategy regex=true на SQLite
// работает (раньше "no such function: REGEXP"). Проверяем живой вызов
// через зарегистрированную функцию.
func TestConnect_RegexpFunction(t *testing.T) {
	conn, err := (SqliteAdapter{}).Connect(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer conn.Close() //nolint:errcheck

	var matched bool
	if err := conn.QueryRowContext(context.Background(), `SELECT 'abc123' REGEXP '^[a-z]+\d+$'`).Scan(&matched); err != nil {
		t.Fatalf("regexp query: %v", err)
	}
	if !matched {
		t.Error("regexp should match 'abc123' against ^[a-z]+\\d+$")
	}

	var notMatched bool
	if err := conn.QueryRowContext(context.Background(), `SELECT 'abc' REGEXP '^\d+$'`).Scan(&notMatched); err != nil {
		t.Fatalf("regexp no-match query: %v", err)
	}
	if notMatched {
		t.Error("regexp should NOT match 'abc' against ^\\d+$")
	}
}

// TestEnsurePragmaParams_KeepsExplicit — DSN с уже заданными _pragma
// не модифицируется (приоритет у явных параметров).
func TestEnsurePragmaParams_KeepsExplicit(t *testing.T) {
	dsn := "file:/tmp/db.sqlite?_pragma=foreign_keys(0)"
	got := ensurePragmaParams(dsn)
	if got != dsn {
		t.Errorf("ensurePragmaParams changed explicit DSN: %q → %q", dsn, got)
	}
}

// TestConnect_ExplicitPragma_NotOverridden — M5: DSN с явным _pragma=
// НЕ должен перебиваться Exec-fallback'ом (раньше conn1 получал
// foreign_keys=ON вопреки foreign_keys(0) в DSN, а conn2 — вообще без прагм).
// Тест проверяет, что при _pragma= в DSN Exec-fallback не выполняется:
// foreign_keys остаётся как задано пользователем.
func TestConnect_ExplicitPragma_NotOverridden(t *testing.T) {
	dir := t.TempDir()
	dsn := "file:" + dir + "/fk.db?_pragma=foreign_keys(0)&_pragma=busy_timeout(0)"
	conn, err := (SqliteAdapter{}).Connect(context.Background(), dsn)
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer conn.Close() //nolint:errcheck

	// Проверяем через PRAGMA foreign_keys: должен быть 0 (явная настройка),
	// а НЕ 1 (перебитая Exec-fallback'ом).
	var fk int
	if err := conn.QueryRowContext(context.Background(), "PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("query foreign_keys: %v", err)
	}
	if fk != 0 {
		t.Errorf("foreign_keys = %d, want 0 (explicit DSN pragma must not be overridden)", fk)
	}
}
