// Package server — тесты конкурентной нагрузки.
//
// Нагружаем сервер параллельными запросами и проверяем:
//   - нет deadlock'ов / panic'ов
//   - соотношение 2xx/5xx в пределах нормы
//   - после нагрузки /health продолжает отвечать 200
//
// Использует file-based SQLite (через tempdir), иначе in-memory в heavy-concurrent
// режиме возвращает много 5xx из-за SQLITE_BUSY.
package server_test

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
	_ "modernc.org/sqlite"
)

// loadConcurrencyTestDb creates an SQLite DB with TestSeed data for concurrency tests.
func loadConcurrencyTestDb(t *testing.T) (*config.Config, *sql.DB) {
	t.Helper()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Entities: []config.Entity{
			{Name: "student", Table: "students", IDColumn: "id", Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeString, Nullable: boolPtr(false)},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
				{Name: "group_id", Column: "group_id", Type: config.FieldTypeString},
				{Name: "course", Column: "course", Type: config.FieldTypeInt},
			}},
			{Name: "group", Table: "groups", IDColumn: "id", Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeString, Nullable: boolPtr(false)},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
				{Name: "speciality", Column: "speciality", Type: config.FieldTypeString},
			}},
		},
	}

	dbPath := t.TempDir() + "/concurrency.db"
	db, err := sql.Open("sqlite", fmt.Sprintf("file:%s?_journal_mode=WAL&_foreign_keys=on&_busy_timeout=5000", dbPath))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}

	cfg.DataSource.DSN = fmt.Sprintf("file:%s", dbPath)

	schema := `
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT,
    speciality TEXT
);
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    name TEXT,
    group_id TEXT,
    course INTEGER,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
`
	if _, err := db.ExecContext(context.Background(), schema); err != nil {
		t.Fatalf("create schema: %v", err)
	}

	for _, stmt := range []string{
		`INSERT OR IGNORE INTO groups (id, name, speciality) VALUES ('g1', 'ИВТ-21', 'Информационные системы и технологии')`,
		`INSERT OR IGNORE INTO groups (id, name, speciality) VALUES ('g2', 'ПИ-20', 'Программная инженерия')`,
		`INSERT OR IGNORE INTO students (id, name, group_id, course) VALUES ('s1', 'Иван Петров Иванович', 'g1', 2)`,
		`INSERT OR IGNORE INTO students (id, name, group_id, course) VALUES ('s2', 'Мария Сидорова Ивановна', 'g2', 3)`,
	} {
		if _, err := db.ExecContext(context.Background(), stmt); err != nil {
			t.Fatalf("insert data: %v", err)
		}
	}

	return cfg, db
}

func TestConcurrency_FileBased_HeavyLoad(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping heavy concurrent test in short mode")
	}
	cfg, db := loadConcurrencyTestDb(t)
	defer db.Close() //nolint:errcheck

	ts := buildTestRouter(t, cfg, db)

	const (
		totalReqs        = 500
		concurrentClient = 20
	)

	var (
		ok2xx atomic.Int64
		ok4xx atomic.Int64
		ok5xx atomic.Int64
	)

	wg := sync.WaitGroup{}
	start := time.Now()
	for c := 0; c < concurrentClient; c++ {
		c := c
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < totalReqs/concurrentClient; i++ {
				// 50/50 микс: get_by_id существующего и несуществующего
				id := "s1"
				if (i+c)%2 == 0 {
					id = "nonexistent_" + fmt.Sprintf("%d", i)
				}
				resp, err := http.Get(ts.URL + "/students/" + id)
				if err != nil {
					continue
				}
				resp.Body.Close()
				switch {
				case resp.StatusCode < 300:
					ok2xx.Add(1)
				case resp.StatusCode < 500:
					ok4xx.Add(1)
				default:
					ok5xx.Add(1)
				}
			}
		}()
	}
	wg.Wait()
	elapsed := time.Since(start)

	t.Logf("concurrent: %d reqs in %v (%.0f req/s)",
		totalReqs, elapsed, float64(totalReqs)/elapsed.Seconds())
	t.Logf("results: 2xx=%d 4xx=%d 5xx=%d",
		ok2xx.Load(), ok4xx.Load(), ok5xx.Load())

	// На file-based SQLite с WAL успешность должна быть высокой.
	if ok5xx.Load() > int64(totalReqs/10) {
		t.Errorf("слишком много 5xx под нагрузкой: %d/%d", ok5xx.Load(), totalReqs)
	}

	// После нагрузки сервер должен ещё работать.
	resp, err := http.Get(ts.URL + "/health")
	if err != nil {
		t.Fatalf("health после нагрузки: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("сервер упал после нагрузки: /health=%d", resp.StatusCode)
	}
}

func TestConcurrency_ConcurrentReadsOnDifferentIDs(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping heavy concurrent test in short mode")
	}

	_, db := loadConcurrencyTestDb(t)
	defer db.Close() //nolint:errcheck

	var wg sync.WaitGroup
	const goroutines = 50
	const reqsPer = 20
	var ok atomic.Int64
	var fail atomic.Int64

	for g := 0; g < goroutines; g++ {
		g := g
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < reqsPer; i++ {
				row := db.QueryRow("SELECT id, name FROM students WHERE id = ?", fmt.Sprintf("s%d", (g+i)%2+1))
				var id, name string
				if err := row.Scan(&id, &name); err != nil {
					fail.Add(1)
				} else {
					ok.Add(1)
				}
			}
		}()
	}
	wg.Wait()

	t.Logf("concurrent reads: %d OK, %d FAIL", ok.Load(), fail.Load())
	if fail.Load() > 0 {
		t.Errorf("concurrent reads failed: %d", fail.Load())
	}
}
