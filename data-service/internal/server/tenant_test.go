package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// ── Helpers ──

func newTestRegistry(t *testing.T) *datasource.Registry {
	t.Helper()
	return datasource.NewDefaultRegistry()
}

func newInMemoryConfig(t *testing.T) *config.Config {
	t.Helper()

	tmpDir := t.TempDir()
	dbPath := tmpDir + "/test.db"

	db, err := sql.Open("sqlite", dbPath+"?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		t.Fatalf("open test db: %v", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.ExecContext(t.Context(),
		"CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT);"+
			"CREATE TABLE courses (id TEXT PRIMARY KEY, name TEXT);"); err != nil {
		_ = db.Close()
		t.Fatalf("create schema: %v", err)
	}
	_ = db.Close()

	return &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: config.DriverSQLite,
			DSN:    dbPath,
		},
		Entities: []config.Entity{
			{Name: "group", Table: "groups", IDColumn: "id", Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeString},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
			}},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: config.OpBuiltinHealth},
			{Method: "GET", Path: "/groups", Op: config.OpList, Entity: "group"},
			{Method: "GET", Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}
}

func newTestTenantStore(t *testing.T) *TenantStore {
	t.Helper()
	registry := newTestRegistry(t)
	return NewTenantStore(registry, "")
}

func addDefaultTenant(t *testing.T, ts *TenantStore) {
	t.Helper()
	cfg := newInMemoryConfig(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "default", cfg, ""); err != nil {
		t.Fatalf("AddTenant default: %v", err)
	}
}

// ── TenantStore Construction ──

func TestTenantStore_NewEmpty(t *testing.T) {
	ts := newTestTenantStore(t)
	if len(ts.ListTenants()) != 0 {
		t.Error("expected zero tenants")
	}
}

// ── AddTenant / RemoveTenant ──

func TestTenantStore_AddTenant(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	cfg := newInMemoryConfig(t)
	cfg.Entities = []config.Entity{
		{Name: "course", Table: "courses", IDColumn: "id", Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeString},
		}},
	}

	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	inst, err := ts.AddTenant(ctx, "tenant-b", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}
	if inst.ID != "tenant-b" {
		t.Errorf("id = %q", inst.ID)
	}

	all := ts.ListTenants()
	if len(all) != 2 {
		t.Fatalf("expected 2 tenants, got %d", len(all))
	}
}

func TestTenantStore_AddTenant_DuplicateID(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	cfg := newInMemoryConfig(t)
	_, err := ts.AddTenant(ctx, "default", cfg, "")
	if err == nil {
		t.Error("expected error on duplicate tenant")
	}
}

func TestTenantStore_RemoveTenant(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	cfg := newInMemoryConfig(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	if _, err := ts.AddTenant(ctx, "to-remove", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}
	cancel()

	ctx2, cancel2 := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel2()
	if err := ts.RemoveTenant(ctx2, "to-remove"); err != nil {
		t.Fatalf("RemoveTenant: %v", err)
	}

	if _, ok := ts.GetTenant("to-remove"); ok {
		t.Error("to-remove should be removed")
	}
}

func TestTenantStore_RemoveTenant_NotFound(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	err := ts.RemoveTenant(ctx, "nonexistent")
	if err == nil {
		t.Error("expected error")
	}
}

// ── ListTenants ordering ──

func TestTenantStore_ListTenants_SortedByCreatedAt(t *testing.T) {
	ts := newTestTenantStore(t)
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()

	cfg1 := newInMemoryConfig(t)
	if _, err := ts.AddTenant(ctx, "default", cfg1, ""); err != nil {
		t.Fatal(err)
	}

	time.Sleep(10 * time.Millisecond)

	cfg2 := newInMemoryConfig(t)
	if _, err := ts.AddTenant(ctx, "b", cfg2, ""); err != nil {
		t.Fatal(err)
	}

	time.Sleep(10 * time.Millisecond)

	cfg3 := newInMemoryConfig(t)
	if _, err := ts.AddTenant(ctx, "c", cfg3, ""); err != nil {
		t.Fatal(err)
	}

	all := ts.ListTenants()
	if len(all) != 3 {
		t.Fatalf("expected 3 tenants, got %d", len(all))
	}

	for i := 1; i < len(all); i++ {
		if all[i].CreatedAt.Before(all[i-1].CreatedAt) {
			t.Errorf("tenants not sorted: %s before %s",
				all[i-1].ID, all[i].ID)
		}
	}
}

// ── HealthCheck ──

func TestTenantStore_HealthCheck(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	health := ts.HealthCheck(t.Context())
	if len(health) != 1 {
		t.Fatalf("expected 1 health entry, got %d", len(health))
	}
	if health[0].Status != "healthy" {
		t.Errorf("expected healthy, got %s: %s", health[0].Status, health[0].Error)
	}
}

func TestTenantStore_HealthCheck_Empty(t *testing.T) {
	ts := newTestTenantStore(t)
	health := ts.HealthCheck(t.Context())
	if len(health) != 0 {
		t.Errorf("expected 0 entries, got %d", len(health))
	}
}

func TestTenantStore_HealthCheck_Degraded(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	cfg := newInMemoryConfig(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	inst, err := ts.AddTenant(ctx, "to-break", cfg, "")
	cancel()
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	_ = inst.Conn.Close()

	health := ts.HealthCheck(t.Context())
	if len(health) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(health))
	}

	sort.Slice(health, func(i, j int) bool { return health[i].ID < health[j].ID })

	if health[1].Status != "unhealthy" {
		t.Errorf("to-break should be unhealthy after close, got %s: %s", health[1].Status, health[1].Error)
	}
	if health[0].Status != "healthy" {
		t.Errorf("default should be healthy, got %s", health[0].Status)
	}
}

// ── ServeHTTP: Routing ──

func TestTenantStore_ServeHTTP_RoutesToTenant(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	// Wrap with TenantIDMiddleware to extract tenant from header
	handler := TenantIDMiddleware("X-Tenant-ID")(ts)

	req := httptest.NewRequest(http.MethodGet, "/students", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	// Should be 404 since no tenant provided
	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404 without tenant, got %d", rec.Code)
	}

	req2 := httptest.NewRequest(http.MethodGet, "/students", nil)
	req2.Header.Set("X-Tenant-ID", "default")
	rec2 := httptest.NewRecorder()
	handler.ServeHTTP(rec2, req2)

	// With tenant header, should reach the tenant's router (404 if no such endpoint)
	if rec2.Code == http.StatusNotFound {
		// The default tenant router might not have /students endpoint in test config
		// That's OK - we just verify it doesn't return 'tenant_not_found' error
		var body map[string]string
		if err := json.NewDecoder(rec2.Body).Decode(&body); err == nil {
			t.Logf("Response body: %v", body)
			if body["error"] == "tenant_not_found" {
				t.Errorf("should not return tenant_not_found with valid tenant header")
			}
		}
	}
}

func TestTenantStore_ServeHTTP_TenantNotFound(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	req := httptest.NewRequest(http.MethodGet, "/groups", nil)
	req.Header.Set("X-Tenant-ID", "nonexistent")

	rec := httptest.NewRecorder()
	ts.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rec.Code)
	}
}
