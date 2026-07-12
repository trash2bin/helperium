package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// ── Config Persistence Unit Tests ──

func TestTenantStore_SaveTenantConfig_PersistsToTenantsDir(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	cfg := newInMemoryConfig(t)
	cfg.Entities = append(cfg.Entities, config.Entity{
		Name: "course", Table: "courses", IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeString},
		},
	})

	savedPath := ts.SaveTenantConfig("test-tenant", cfg)
	expectedPath := filepath.Join(tenantsDir, "test-tenant.json")
	if savedPath != expectedPath {
		t.Errorf("path = %q, want %q", savedPath, expectedPath)
	}

	if _, err := os.Stat(savedPath); os.IsNotExist(err) {
		t.Fatalf("config file not created at %s", savedPath)
	}

	data, err := os.ReadFile(savedPath)
	if err != nil {
		t.Fatal(err)
	}
	var loaded config.Config
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if loaded.Version != 1 {
		t.Errorf("version = %d, want 1", loaded.Version)
	}
	if len(loaded.Entities) != 2 {
		t.Errorf("entities = %d, want 2", len(loaded.Entities))
	}
	if len(loaded.Endpoints) != 3 {
		t.Errorf("endpoints = %d, want 3", len(loaded.Endpoints))
	}
}

func TestTenantStore_SaveTenantConfig_ReturnsEmptyWhenNoDir(t *testing.T) {
	ts := newTestTenantStore(t)
	path := ts.SaveTenantConfig("x", newInMemoryConfig(t))
	if path != "" {
		t.Errorf("expected empty path when TenantsDir is empty, got %q", path)
	}
}

func TestTenantStore_TenantConfigPath(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	path := ts.TenantConfigPath("my-tenant")
	expected := filepath.Join(tenantsDir, "my-tenant.json")
	if path != expected {
		t.Errorf("path = %q, want %q", path, expected)
	}

	ts.TenantsDir = ""
	path = ts.TenantConfigPath("x")
	if path != "" {
		t.Errorf("expected empty, got %q", path)
	}
}

func TestTenantStore_DeleteTenantConfig(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	configPath := filepath.Join(tenantsDir, "to-delete.json")
	if err := os.WriteFile(configPath, []byte("{}"), 0644); err != nil {
		t.Fatal(err)
	}

	ts.DeleteTenantConfig("to-delete")
	if _, err := os.Stat(configPath); !os.IsNotExist(err) {
		t.Errorf("file should be deleted")
	}

	// Deleting non-existent should not error
	ts.DeleteTenantConfig("nobody")
}

func TestTenantStore_DeleteTenantConfig_NoDir(t *testing.T) {
	ts := newTestTenantStore(t)
	// Should not panic when TenantsDir is empty
	ts.DeleteTenantConfig("nobody")
}

// ── Admin: adminAddTenantHandler — именно этот хендлер вызывается
// через admin dashboard когда администратор добавляет нового клиента ──

func TestAdminAddTenantHandler_PersistsToTenantsDir(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	body := `{
		"id": "persist-test",
		"config": {
			"version": 1,
			"data_source": {
				"driver": "sqlite",
				"dsn": ":memory:"
			}
		}
	}`

	req := httptest.NewRequest(http.MethodPost, "/admin/tenants", strings.NewReader(body))
	rec := httptest.NewRecorder()
	ts.adminAddTenantHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", rec.Code, rec.Body.String())
	}

	// Verify config persisted to TenantsDir
	configPath := filepath.Join(tenantsDir, "persist-test.json")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		t.Fatalf("tenant config not persisted at %s", configPath)
	}

	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	var loaded config.Config
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if loaded.Version != 1 {
		t.Errorf("version = %d, want 1", loaded.Version)
	}
	if loaded.DataSource.Driver != "sqlite" {
		t.Errorf("driver = %q", loaded.DataSource.Driver)
	}

	// Tenant should be live
	_, ok := ts.GetTenant("persist-test")
	if !ok {
		t.Error("tenant should be registered")
	}

	// ConfigPath on instance should point to TenantsDir
	inst, _ := ts.GetTenant("persist-test")
	expectedPath := filepath.Join(tenantsDir, "persist-test.json")
	if inst.ConfigPath != expectedPath {
		t.Errorf("inst.ConfigPath = %q, want %q", inst.ConfigPath, expectedPath)
	}
}

func TestAdminAddTenantHandler_Duplicate(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	// Add tenant once
	cfg := newInMemoryConfig(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "dup-test", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Try adding duplicate via handler
	body := `{
		"id": "dup-test",
		"config": {
			"version": 1,
			"data_source": { "driver": "sqlite", "dsn": ":memory:" }
		}
	}`
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants", strings.NewReader(body))
	rec := httptest.NewRecorder()
	ts.adminAddTenantHandler(rec, req)

	if rec.Code != http.StatusConflict {
		t.Errorf("expected 409, got %d: %s", rec.Code, rec.Body.String())
	}
}

// ── Admin: adminConfigUpdateHandler ──

// setConfigSchemaForTest sets CONFIG_SCHEMA to point to the real schema.
// Runs from data-service/internal/server/, need ../../../specs/config.schema.json.
func setConfigSchemaForTest(t *testing.T) {
	t.Helper()
	orig := os.Getenv("CONFIG_SCHEMA")
	// Relative from test working directory (package dir)
	_ = os.Setenv("CONFIG_SCHEMA", "../../../specs/config.schema.json")
	t.Cleanup(func() {
		if orig != "" {
			_ = os.Setenv("CONFIG_SCHEMA", orig)
		} else {
			_ = os.Unsetenv("CONFIG_SCHEMA")
		}
	})
}

func TestAdminConfigUpdateHandler_SavesToTenantsDir(t *testing.T) {
	setConfigSchemaForTest(t)
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	// Create a real SQLite DB file for the updated config
	dbDir := t.TempDir()
	dbPath := filepath.Join(dbDir, "updated.db")
	createTestDBSchema(t, dbPath)

	// Add tenant with initial config
	cfg := newInMemoryConfig(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	inst, err := ts.AddTenant(ctx, "update-test", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}
	// Seed a config file on disk so the handler has somewhere to write
	_ = ts.SaveTenantConfig("update-test", cfg)
	inst.ConfigPath = filepath.Join(tenantsDir, "update-test.json")

	// Update via adminConfigUpdateHandler (pointing to the real DB)
	updateBody := `{"version":1,"data_source":{"driver":"sqlite","dsn":"` + dbPath + `"}}`

	req := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader(updateBody))
	req.Header.Set("X-Tenant-ID", "update-test")
	rec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}

	// Verify config saved to TenantsDir
	configPath := filepath.Join(tenantsDir, "update-test.json")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		t.Fatalf("updated config not persisted at %s", configPath)
	}

	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	var loaded config.Config
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if loaded.DataSource.DSN != dbPath {
		t.Errorf("dsn = %q, want %q", loaded.DataSource.DSN, dbPath)
	}
}

func createTestDBSchema(t *testing.T, path string) {
	t.Helper()
	db, err := sql.Open("sqlite", path+"?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		t.Fatalf("open test db: %v", err)
	}
	defer db.Close() //nolint:errcheck
	if _, err := db.ExecContext(t.Context(),
		"CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT);"+
			"CREATE TABLE courses (id TEXT PRIMARY KEY, name TEXT);"); err != nil {
		t.Fatalf("create schema: %v", err)
	}
}

func TestAdminConfigUpdateHandler_InvalidJSON(t *testing.T) {
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	req := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader("not json"))
	req.Header.Set("X-Tenant-ID", "default")
	rec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rec.Code)
	}
}

// ── Full persistence cycle: write → read back from disk ──

func TestTenantPersistence_WriteAndReadBack(t *testing.T) {
	ts := newTestTenantStore(t)
	tenantsDir := t.TempDir()
	ts.TenantsDir = tenantsDir

	cfg := newInMemoryConfig(t)
	cfg.Entities = []config.Entity{
		{Name: "group", Table: "groups", IDColumn: "id", Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeString},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "size", Column: "size", Type: config.FieldTypeInt},
		}},
	}

	// Write
	savedPath := ts.SaveTenantConfig("full-test", cfg)
	if savedPath == "" {
		t.Fatal("SaveTenantConfig returned empty path")
	}

	// Read back and verify all fields survived
	data, err := os.ReadFile(savedPath)
	if err != nil {
		t.Fatal(err)
	}
	var loaded config.Config
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if loaded.Version != cfg.Version {
		t.Errorf("version: got %d, want %d", loaded.Version, cfg.Version)
	}
	if loaded.DataSource.Driver != cfg.DataSource.Driver {
		t.Errorf("driver: got %q, want %q", loaded.DataSource.Driver, cfg.DataSource.Driver)
	}
	if len(loaded.Entities) != len(cfg.Entities) {
		t.Fatalf("entities: got %d, want %d", len(loaded.Entities), len(cfg.Entities))
	}
	if loaded.Entities[0].Name != "group" {
		t.Errorf("entity name: got %q", loaded.Entities[0].Name)
	}
	if len(loaded.Entities[0].Fields) != 3 {
		t.Errorf("fields: got %d, want 3", len(loaded.Entities[0].Fields))
	}
}

// TestApprovedToolsInConfig проверяет, что approved_tools хранятся в tenant config'e
// и переживают round-trip через SaveTenantConfig → config.Load
func TestApprovedToolsInConfig(t *testing.T) {
	tenantsDir := t.TempDir()
	reg := datasource.NewDefaultRegistry()
	ts := NewTenantStore(reg, tenantsDir)

	cfg := newInMemoryConfig(t)
	ro := readOnlyPtr()
	cfg.DataSource.ReadOnly = ro
	cfg.ApprovedTools = []string{"/orders", "/orders/{id}"}

	// Add tenant
	ctx := context.Background()
	inst, err := ts.AddTenant(ctx, "test-shop", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Verify ApprovedTools are set on instance
	if len(inst.ApprovedTools) != 2 {
		t.Fatalf("expected 2 approved tools on instance, got %d", len(inst.ApprovedTools))
	}
	if !inst.ApprovedTools["/orders"] {
		t.Error("expected /orders to be approved")
	}
	if !inst.ApprovedTools["/orders/{id}"] {
		t.Error("expected /orders/{id} to be approved")
	}

	// Persist config
	persistedPath := ts.SaveTenantConfig("test-shop", cfg)
	if persistedPath == "" {
		t.Fatal("SaveTenantConfig returned empty path")
	}

	// Load back from disk (use json.Unmarshal — config.Load requires schema)
	data2, err := os.ReadFile(persistedPath)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	var loadedCfg config.Config
	if err := json.Unmarshal(data2, &loadedCfg); err != nil {
		t.Fatalf("json.Unmarshal: %v", err)
	}
	if len(loadedCfg.ApprovedTools) != 2 {
		t.Fatalf("expected 2 approved_tools in loaded config, got %d: %v", len(loadedCfg.ApprovedTools), loadedCfg.ApprovedTools)
	}

	found := false
	for _, p := range loadedCfg.ApprovedTools {
		if p == "/orders" {
			found = true
		}
	}
	if !found {
		t.Error("expected /orders in loaded config approved_tools")
	}
}

func readOnlyPtr() *bool {
	v := true
	return &v
}
