package server

import (
	"bytes"
	"context"
	"database/sql"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestBuildTenantInstance_DoesNotLogReadonlyDSN(t *testing.T) {
	ts := newTestTenantStore(t)
	dir := filepath.Join(t.TempDir(), "secret-bearing-readonly-dsn")
	dbPath := filepath.Join(dir, "tenant.db")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("create database directory: %v", err)
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:      config.DriverSQLite,
			DSN:         dbPath,
			ReadonlyDSN: "file:" + dbPath + "?mode=ro&immutable=1",
			ReadOnly:    boolPtr(true),
		},
		Entities:  []config.Entity{{Name: "product", Table: "products", IDColumn: "id"}},
		Endpoints: []config.Endpoint{{Method: http.MethodGet, Path: "/products/{id}", Op: config.OpGetByID, Entity: "product"}},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	inst, err := buildTenantInstance(ctx, ts, ts.registry, "log-safe-tenant", cfg, "")
	if err != nil {
		t.Fatalf("buildTenantInstance: %v", err)
	}
	t.Cleanup(func() { closeTenantConns(inst) })

	output := logs.String()
	if !strings.Contains(output, "read-only connection established") {
		t.Fatalf("expected lifecycle event in logs, got %q", output)
	}
	if strings.Contains(output, "secret-bearing-readonly-dsn") || strings.Contains(output, "readonly_dsn=") {
		t.Fatalf("readonly DSN leaked into lifecycle logs: %q", output)
	}
}
