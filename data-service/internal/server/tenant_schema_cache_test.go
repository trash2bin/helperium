package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// readAndUnmarshal читает файл и парсит JSON в out.
func readAndUnmarshal(t *testing.T, path string, out any) error {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, out)
}

func boolPtrTrue() *bool { b := true; return &b }

// TestRegenerateAndPersistTenantConfig_NoSchema_Fallback — L-3: без закэшированной схемы
// RegenerateAndPersistTenantConfig сохраняет конфиг как есть (fallback), не регенерируя.
func TestRegenerateAndPersistTenantConfig_NoSchema_Fallback(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "x.db", ReadOnly: boolPtrTrue()},
		Entities:   []config.Entity{{Name: "products"}},
	}
	path := ts.RegenerateAndPersistTenantConfig("t-no-schema", cfg)
	if path == "" {
		t.Fatal("RegenerateAndPersistTenantConfig returned empty path")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read persisted config: %v", err)
	}
	// Entities сохранены как были (не перегенерированы — схемы нет).
	var saved config.Config
	if err := json.Unmarshal(data, &saved); err != nil {
		t.Fatalf("unmarshal saved config: %v", err)
	}
	if len(saved.Entities) != 1 || saved.Entities[0].Name != "products" {
		t.Errorf("fallback changed entities: %+v", saved.Entities)
	}
}

// TestRegenerateAndPersistTenantConfig_CorruptSchema_Fallback — L-3: битый кэш схемы
// → LoadTenantSchema ошибка → RegenerateAndRegenerateAndPersistTenantConfig сохраняет как есть.
func TestRegenerateAndPersistTenantConfig_CorruptSchema_Fallback(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	if err := os.WriteFile(ts.TenantSchemaPath("t-corrupt"), []byte("{invalid json"), 0644); err != nil {
		t.Fatalf("write corrupt schema: %v", err)
	}

	if _, err := ts.LoadTenantSchema("t-corrupt"); err == nil {
		t.Fatal("LoadTenantSchema: expected error for corrupt cache, got nil")
	}

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "x.db", ReadOnly: boolPtrTrue()},
		Entities:   []config.Entity{{Name: "products"}},
	}
	path := ts.RegenerateAndPersistTenantConfig("t-corrupt", cfg)
	if path == "" {
		t.Fatal("RegenerateAndPersistTenantConfig returned empty path")
	}
	var saved config.Config
	if err := readAndUnmarshal(t, path, &saved); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(saved.Entities) != 1 {
		t.Errorf("corrupt schema should fallback to as-is persist, entities=%d", len(saved.Entities))
	}
}

// TestRegenerateAndPersistTenantConfig_WithSchema_Regenerates — PersistTenantConfig с валидным
// кэшем схемы перегенерирует Entities/Endpoints из intent+schema.
func TestRegenerateAndPersistTenantConfig_WithSchema_Regenerates(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()

	schema := &datasource.Schema{
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "INTEGER"},
					{Name: "name", Type: "TEXT"},
				},
			},
		},
	}
	ts.SaveTenantSchema("t-with-schema", schema)

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "x.db", ReadOnly: boolPtrTrue()},
		// Намеренно не содержат Entities — Hydrate должен их сгенерировать из схемы.
	}
	path := ts.RegenerateAndPersistTenantConfig("t-with-schema", cfg)
	if path == "" {
		t.Fatal("RegenerateAndPersistTenantConfig returned empty path")
	}
	var saved config.Config
	if err := readAndUnmarshal(t, path, &saved); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(saved.Entities) == 0 {
		t.Fatal("schema cache present but entities not regenerated")
	}
	found := false
	for _, e := range saved.Entities {
		if e.Name == "products" {
			found = true
		}
	}
	if !found {
		t.Errorf("products entity not generated from cached schema: %+v", saved.Entities)
	}
}

// TestLoadTenantSchema_Missing_ReturnsNil — отсутствие файла кэша → (nil, nil).
func TestLoadTenantSchema_Missing_ReturnsNil(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	s, err := ts.LoadTenantSchema("no-such")
	if err != nil {
		t.Fatalf("expected nil error for missing cache, got %v", err)
	}
	if s != nil {
		t.Fatalf("expected nil schema for missing cache, got %+v", s)
	}
}

// Real sqlite round-trip: схема из живой БД → SaveTenantSchema → RegenerateAndPersistTenantConfig.
func TestRegenerateAndPersistTenantConfig_SQLiteRoundTrip(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "schema_rt.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	if _, err := db.ExecContext(context.Background(),
		`CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL)`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}
	_ = db.Close()

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()

	// Интроспекция живой БД через sqlite adapter.
	adapter, ok := ts.registry.Get("sqlite")
	if !ok {
		t.Fatal("sqlite adapter not registered")
	}
	conn, err := adapter.Connect(context.Background(), dbPath)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	schema, err := adapter.Introspect(context.Background(), conn)
	conn.Close() //nolint:errcheck
	if err != nil {
		t.Fatalf("introspect: %v", err)
	}

	ts.SaveTenantSchema("t-rt", schema)

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: dbPath, ReadOnly: boolPtrTrue()},
	}
	path := ts.RegenerateAndPersistTenantConfig("t-rt", cfg)
	if path == "" {
		t.Fatal("RegenerateAndPersistTenantConfig returned empty path")
	}
	var saved config.Config
	if err := readAndUnmarshal(t, path, &saved); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(saved.Entities) == 0 {
		t.Fatal("entities not regenerated from real sqlite schema")
	}
	// Валидность результирующего конфига.
	if err := saved.Validate(); err != nil {
		t.Errorf("persisted config invalid: %v", err)
	}
}
