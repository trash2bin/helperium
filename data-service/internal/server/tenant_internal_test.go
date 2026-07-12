package server

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestSetHasAdmin(t *testing.T) {
	ts := &TenantStore{}
	ts.SetHasAdmin(true)
	ts.mu.RLock()
	if !ts.hasAdmin {
		t.Error("SetHasAdmin(true) expected hasAdmin=true")
	}
	ts.mu.RUnlock()

	ts.SetHasAdmin(false)
	ts.mu.RLock()
	if ts.hasAdmin {
		t.Error("SetHasAdmin(false) expected hasAdmin=false")
	}
	ts.mu.RUnlock()
}

func TestAdminConfigResponseFromConfig(t *testing.T) {
	cfg := &config.Config{
		Version: 3,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    "file:test.db",
		},
		Entities: []config.Entity{
			{Name: "student", Table: "students"},
		},
	}
	resp := adminConfigResponseFromConfig(cfg)
	if resp.Version != 3 {
		t.Errorf("Version = %d, want 3", resp.Version)
	}
	if resp.Driver != "sqlite" {
		t.Errorf("Driver = %q, want 'sqlite'", resp.Driver)
	}
	if len(resp.Entities) != 1 {
		t.Errorf("Entities count = %d, want 1", len(resp.Entities))
	}
}
