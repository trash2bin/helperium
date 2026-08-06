package server

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestRemoveTenant_NoPanicDuringConcurrentRequests — Задача 1 (drain in-flight).
//
// Гоняет N горутин запросов к тенанту параллельно с RemoveTenant (в цикле).
// Ожидаем ТОЛЬКО {200, 404, ошибка БД ("database is closed"/sql.ErrConnDone)},
// никогда панику. Под -race не должно быть data race.
func TestRemoveTenant_NoPanicDuringConcurrentRequests(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/race.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "INSERT INTO groups (id, name) VALUES ('g1', 'alpha'), ('g2', 'beta')"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: http.MethodGet, Path: "/groups", Op: config.OpStrategy, Entity: "group", Strategy: "grep"},
			{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if _, err := ts.AddTenant(ctx, "race-tenant", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Запрос, который держит коннект дольше: grep с пагинацией.
	req := func() *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/groups?pattern=alpha&limit=100", nil)
		r.Header.Set("X-Tenant-ID", "race-tenant")
		return r
	}

	var wg sync.WaitGroup
	errCh := make(chan error, 2048) // 8 горутин × 25 запросов × возможные ошибки

	// Горутины запросов.
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 25; j++ {
				rec := httptest.NewRecorder()
				ts.ServeHTTP(rec, req())
				code := rec.Code
				switch code {
				case http.StatusOK, http.StatusNotFound:
					// ок: либо успели до удаления, либо тенант уже удалён
				case http.StatusInternalServerError, http.StatusServiceUnavailable:
					body := rec.Body.String()
					// Ошибка БД из-за закрытого пула — допустима (запрос начался до
					// Close, но коннект не успел взяться). Всё, что не паника — ок.
					if !strings.Contains(body, "closed") &&
						!strings.Contains(body, "database is") &&
						!strings.Contains(body, "SQL logic error") {
						errCh <- fmt.Errorf("unexpected 5xx: code=%d body=%s", code, body)
					}
				default:
					errCh <- fmt.Errorf("unexpected status %d body=%s", code, rec.Body.String())
				}
			}
		}()
	}

	// Удаляем тенант посреди запросов (несколько раз — повторный удаление даёт 404).
	time.Sleep(2 * time.Millisecond)
	for i := 0; i < 3; i++ {
		_ = ts.RemoveTenant(ctx, "race-tenant")
		time.Sleep(1 * time.Millisecond)
	}

	wg.Wait()
	close(errCh)
	for err := range errCh {
		t.Error(err)
	}
}

// TestRemoveTenant_MarksRemoving_NewRequests404 — после RemoveTenant новые
// запросы (даже пока пул ещё не закрыт) получают 404, а не работают с
// полузакрытым инстансом.
func TestRemoveTenant_MarksRemoving_NewRequests404(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/removing.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	inst, err := ts.AddTenant(ctx, "removing-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Помечаем removing вручную (как это делает RemoveTenant в фазе 1) —
	// ещё ДО закрытия пула, чтобы проверить, что resolveTenant отдаёт nil.
	inst.removing.Store(true)

	req := httptest.NewRequest(http.MethodGet, "/groups/g1", nil)
	req.Header.Set("X-Tenant-ID", "removing-tenant")
	rec := httptest.NewRecorder()
	ts.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("request after removing mark: status=%d, want 404", rec.Code)
	}

	// resolveTenant напрямую тоже должен вернуть nil.
	if got := ts.resolveTenant(req); got != nil {
		t.Error("resolveTenant returned instance for removing tenant")
	}
}

// TestHealthCheck_RaceWithReload — Задача 2 (гонка HealthCheck на ti.Config).
//
// Параллельно дёргаем ReloadTenant (меняет inst.Config под ts.mu.Lock) и
// HealthCheck (читает Config-поля). Под -race должно быть 0 race.
func TestHealthCheck_RaceWithReload(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/health.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	mkCfg := func(name string) *config.Config {
		return &config.Config{
			Version: 1,
			DataSource: config.DataSourceConfig{
				Driver:   config.DriverSQLite,
				DSN:      dbPath,
				ReadOnly: boolPtr(true),
			},
			Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
			Endpoints: []config.Endpoint{
				{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
			},
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if _, err := ts.AddTenant(ctx, "health-tenant", mkCfg("v1"), ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	path := ts.TenantConfigPath("health-tenant")

	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(2)

		// Релоадер: меняет Config/Router под ts.mu.Lock.
		go func() {
			defer wg.Done()
			_ = ts.ReloadTenant(ctx, "health-tenant", path)
		}()

		// Читатель: HealthCheck — снапшот полей + пинги.
		go func() {
			defer wg.Done()
			_ = ts.HealthCheck(ctx)
		}()
	}

	wg.Wait()

	// HealthCheck должен вернуть тенанта (не пропасть из-за гонки).
	health := ts.HealthCheck(ctx)
	if len(health) != 1 || health[0].ID != "health-tenant" {
		t.Errorf("HealthCheck after race: %+v", health)
	}
}

// TestHealthCheck_SnapshotResponse_Race — tenantResponse через snapshotTenantResponse
// под конкурентным ReloadTenant — 0 race и корректный результат.
func TestHealthCheck_SnapshotResponse_Race(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/resp.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if _, err := ts.AddTenant(ctx, "resp-tenant", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	path := ts.TenantConfigPath("resp-tenant")

	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(2)
		go func() {
			defer wg.Done()
			_ = ts.ReloadTenant(ctx, "resp-tenant", path)
		}()
		go func() {
			defer wg.Done()
			tr, ok := ts.snapshotTenantResponse("resp-tenant")
			if !ok {
				t.Error("snapshotTenantResponse: tenant not found")
				return
			}
			if tr.ID != "resp-tenant" {
				t.Errorf("snapshot: unexpected id %q", tr.ID)
			}
		}()
	}
	wg.Wait()
}
