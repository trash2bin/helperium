package server

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestAddTenant_Duplicate_ClosesBothPools — регресс утечки read-only пула.
//
// Проблема: AddTenant при double-check (тенант уже существует) закрывал только
// inst.Conn, оставляя inst.ReadonlyConn (readonly_dsn) открытым → утечка пула.
//
// Проверяем на внутреннем уровне: строим инстанс через buildTenantInstance
// (как это делает AddTenant перед double-check), затем закрываем через
// closeTenantConns (тот же helper, что вызывает AddTenant) и убеждаемся,
// что оба пула закрыты.
func TestAddTenant_Duplicate_ClosesBothPools(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/dup.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:      config.DriverSQLite,
			DSN:         dbPath,
			ReadonlyDSN: dbPath, // тот же файл — открывает второй пул
			ReadOnly:    boolPtr(true),
		},
		Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	inst, err := buildTenantInstance(ctx, ts, ts.registry, "dup-tenant", cfg, "")
	if err != nil {
		t.Fatalf("buildTenantInstance: %v", err)
	}
	if inst.ReadonlyConn == nil {
		t.Skip("readonly conn not opened")
	}

	// Предусловие: оба пула открыты.
	if err := inst.Conn.PingContext(ctx); err != nil {
		t.Fatalf("precondition: main pool should be open: %v", err)
	}
	if err := inst.ReadonlyConn.PingContext(ctx); err != nil {
		t.Fatalf("precondition: readonly pool should be open: %v", err)
	}

	// Демонстрация бага: старый double-check закрывал ТОЛЬКО inst.Conn,
	// оставляя ReadonlyConn открытым. Это доказывает, что близкий к старому
	// код (Close только основного пула) НЕ закрывает readonly-пул.
	_ = inst.Conn.Close()
	if err := inst.Conn.PingContext(ctx); err == nil {
		t.Error("main pool close should have closed main conn")
	}
	if err := inst.ReadonlyConn.PingContext(ctx); err != nil {
		t.Fatalf("bug repro precondition: readonly pool should still be open after main-only close: %v", err)
	}

	// Фикс: closeTenantConns (тот же helper, что вызывает AddTenant в
	// double-check) закрывает ОБА пула.
	closeTenantConns(inst)

	if err := inst.ReadonlyConn.PingContext(ctx); err == nil {
		t.Error("readonly conn pool leaked after closeTenantConns")
	}
}

func TestRemoveTenant_ClosesBothPools(t *testing.T) {
	ts := newTestTenantStore(t)

	dbPath := t.TempDir() + "/rm.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(t.Context(), "CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT)"); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	_ = db.Close()

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:      config.DriverSQLite,
			DSN:         dbPath,
			ReadonlyDSN: dbPath,
			ReadOnly:    boolPtr(true),
		},
		Entities: []config.Entity{{Name: "group", Table: "groups", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: http.MethodGet, Path: "/groups/{id}", Op: config.OpGetByID, Entity: "group"},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	inst, err := ts.AddTenant(ctx, "rm-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}
	if inst.ReadonlyConn == nil {
		t.Skip("readonly conn not opened")
	}

	if err := ts.RemoveTenant(ctx, "rm-tenant"); err != nil {
		t.Fatalf("RemoveTenant: %v", err)
	}

	// Оба пула закрыты после удаления.
	if err := inst.Conn.PingContext(ctx); err == nil {
		t.Error("main conn pool still open after RemoveTenant")
	}
	if err := inst.ReadonlyConn.PingContext(ctx); err == nil {
		t.Error("readonly conn pool still open after RemoveTenant")
	}

	// Повторный запрос к удалённому тенанту — 404.
	req := httptest.NewRequest(http.MethodGet, "/groups/x", nil)
	req.Header.Set("X-Tenant-ID", "rm-tenant")
	rec := httptest.NewRecorder()
	ts.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("request to removed tenant: status = %d, want 404", rec.Code)
	}
}

func TestBuildTenantInstance_SQLiteReadonlyURIRejectsWrites(t *testing.T) {
	ts := newTestTenantStore(t)
	dir := t.TempDir()
	dbPath := dir + "/demo.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("INSERT INTO products (name) VALUES ('Demo product')"); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:      config.DriverSQLite,
			DSN:         "demo.db",
			ReadonlyDSN: "file:demo.db?mode=ro&immutable=1",
			ReadOnly:    boolPtr(true),
		},
		Entities:  []config.Entity{{Name: "product", Table: "products", IDColumn: "id"}},
		Endpoints: []config.Endpoint{{Method: http.MethodGet, Path: "/products/{id}", Op: config.OpGetByID, Entity: "product"}},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	inst, err := buildTenantInstance(ctx, ts, ts.registry, "demo", cfg, dir+"/demo.json")
	if err != nil {
		t.Fatalf("buildTenantInstance: %v", err)
	}
	t.Cleanup(func() { closeTenantConns(inst) })
	if inst.ReadonlyConn == nil {
		t.Fatal("readonly connection was not created")
	}
	if _, err := inst.ReadonlyConn.ExecContext(ctx, "INSERT INTO products (name) VALUES ('Must fail')"); err == nil {
		t.Fatal("write via readonly SQLite URI unexpectedly succeeded")
	}
}
