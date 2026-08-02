package server

import (
	"context"
	"database/sql"
	"net/http"
	"path/filepath"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestReloadTenant_DSNChanged_Reconnects — ReloadTenant с изменённым DSN должен
// ПЕРЕСОЗДАТЬ соединение (buildTenantInstance), а не переиспользовать старый
// AdapterSub. Раньше DSN-изменение молча игнорировалось: dry-run валидировал
// новый DSN, но reload работал на старом коннекте.
func TestReloadTenant_DSNChanged_Reconnects(t *testing.T) {
	ts := newTestTenantStore(t)

	dir := t.TempDir()
	db1 := filepath.Join(dir, "one.db")
	db2 := filepath.Join(dir, "two.db")
	for _, db := range []string{db1, db2} {
		conn, err := sql.Open("sqlite", db)
		if err != nil {
			t.Fatalf("open %s: %v", db, err)
		}
		if _, err := conn.Exec(`CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)`); err != nil {
			conn.Close()
			t.Fatalf("create %s: %v", db, err)
		}
		conn.Close()
	}

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: config.DriverSQLite,
			DSN:    db1,
		},
		Entities: []config.Entity{{
			Name:     "items",
			Table:    "items",
			IDColumn: "id",
			Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeString},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
			},
		}},
		Endpoints: []config.Endpoint{{Method: http.MethodGet, Path: "/items/{id}", Op: config.OpGetByID, Entity: "items"}},
	}

	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()

	if _, err := ts.AddTenant(ctx, "t", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Пишем новый конфиг с другим DSN.
	cfgPath := filepath.Join(dir, "two.json")
	newCfg := *cfg
	newCfg.DataSource.DSN = db2
	if err := writeConfigForTest(t, cfgPath, &newCfg); err != nil {
		t.Fatalf("write config: %v", err)
	}

	if err := ts.ReloadTenant(ctx, "t", cfgPath); err != nil {
		t.Fatalf("ReloadTenant: %v", err)
	}

	inst, ok := ts.GetTenant("t")
	if !ok {
		t.Fatal("tenant not found after reload")
	}
	if inst.Config == nil || inst.Config.DataSource.DSN != db2 {
		t.Fatalf("config DSN = %v, want %q", inst.Config.DataSource.DSN, db2)
	}

	// Доказательство переподключения: запрос должен идти в db2, а не db1.
	// Пишем данные в db2 и проверяем, что read идёт через новый пул.
	w2, err := sql.Open("sqlite", db2)
	if err != nil {
		t.Fatalf("open db2: %v", err)
	}
	if _, err := w2.Exec(`INSERT INTO items (id, name) VALUES (1, 'from-db2')`); err != nil {
		w2.Close()
		t.Fatalf("insert db2: %v", err)
	}
	w2.Close()

	// db1 без данных — если пул остался старым, SELECT вернёт 0 строк.
	var name string
	err = inst.Conn.QueryRowContext(ctx, `SELECT name FROM items WHERE id = 1`).Scan(&name)
	if err != nil {
		t.Fatalf("query via inst.Conn (после reload): %v", err)
	}
	if name != "from-db2" {
		t.Errorf("read через inst.Conn вернул %q, want 'from-db2' (DSN change not applied?)", name)
	}
}
