// Package server_test — scenario-based test helpers (seedgen-free).
//
// Replaces the old scenario_test_helpers.go which depended on seedgen.
// Now only opens pre-built data.db files directly.
package server_test

import (
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/server"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// loadScenario opens a pre-built data.db from a scenario directory.
//
// The scenario dir must contain:
//   - config.json  (entity + endpoint definitions)
//   - data.db      (pre-materialized SQLite database)
//
// For scenarios where data.db is too large (e.g. big-testseed 368KB),
// use the pre-built file. For scenarios without data.db, use the
// in-memory testDB() helper from server_test.go instead.
func loadScenario(t testing.TB, dir string) (*config.Config, *sql.DB) {
	t.Helper()

	cfg, err := config.Load(filepath.Join(dir, "config.json"))
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	dbPath := filepath.Join(dir, "data.db")
	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		t.Fatalf("scenario %q has no data.db — cannot load", dir)
	}

	absDB, _ := filepath.Abs(dbPath)
	dsn := fmt.Sprintf("file:%s?_journal_mode=WAL&_foreign_keys=on", absDB)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}

	cfg.DataSource.DSN = dsn
	return cfg, db
}

// buildTestRouter creates a httptest.Server from config + *sql.DB.
func buildTestRouter(t testing.TB, cfg *config.Config, db *sql.DB) *httptest.Server {
	t.Helper()
	adapter := &testSQLite{db: db}
	store := server.NewTenantStore(datasource.NewDefaultRegistry(), "")
	router, err := server.NewRouterFromConfig(store, cfg, adapter, nil)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}
	// Register pre-built instance directly — skip AddTenant which would open a new
	// connection, losing the already-seeded in-memory DB.
	inst := &server.TenantInstance{
		ID:         "default",
		Config:     cfg,
		AdapterSub: adapter,
		Router:     router,
	}
	if err := store.RegisterTenantInstance(inst); err != nil {
		t.Fatalf("RegisterTenantInstance: %v", err)
	}
	// Wrap with middleware that injects X-Tenant-ID: default for tests that don't set it.
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Tenant-ID") == "" && r.URL.Query().Get("tenant") == "" {
			r.Header.Set("X-Tenant-ID", "default")
		}
		server.TenantIDMiddleware("X-Tenant-ID")(store).ServeHTTP(w, r)
	})
	ts := httptest.NewServer(handler)
	t.Cleanup(func() { ts.Close() })
	return ts
}
