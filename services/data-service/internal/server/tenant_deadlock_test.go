package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// writeConfigForTest сериализует конфиг на диск (для ReloadTenant в тестах).
func writeConfigForTest(t *testing.T, path string, cfg *config.Config) error {
	t.Helper()
	data, err := json.Marshal(cfg)
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0644)
}

// TestServeHTTP_NestedMCPschema_NoDeadlock — регресс deadlock'а при вложенном
// resolveTenant внутри tenant-роутера.
//
// Проблема (C1): ServeHTTP держит ts.mu.RLock на весь запрос, а хендлеры
// /mcp/schema и /openapi.json вызывали ts.resolveTenant(r) → повторный RLock
// из-под уже удерживаемого RLock. При queued writer'е (ReloadTenant под
// ts.mu.Lock) Go RWMutex блокирует новых читателей → deadlock.
//
// Фикс: ServeHTTP кладёт зарезолвленный inst в контекст (tenantInstanceKey),
// хендлеры читают его оттуда и не делают второй RLock.
//
// Тест: горутина A держит RLock через ServeHTTP и внутри вызывает /mcp/schema
// (через настоящий tenant-роутер), горутина B запускает ReloadTenant (writer).
// До фикса — deadlock (зависание, тест падает по таймауту); после — проходит.
func TestServeHTTP_NestedMCPschema_NoDeadlock(t *testing.T) {
	ts := newTestTenantStore(t)
	cfg := newInMemoryConfig(t)
	inst, err := ts.AddTenant(context.Background(), "nested-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Интроспектируем схему, чтобы /mcp/schema вернул 200 (а не 503).
	inst.schemaMu.Lock()
	inst.IntrospectedSchema = &datasource.Schema{
		Tables: []datasource.Table{
			{Name: "groups", PrimaryKey: []string{"id"}, Columns: []datasource.Column{{Name: "id", Type: "int"}}},
		},
	}
	inst.schemaMu.Unlock()

	// Настоящий tenant-роутер с /mcp/schema.
	router, err := NewRouterFromConfig(ts, inst.Config, inst.AdapterSub)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}
	ts.mu.Lock()
	inst.Router = router
	ts.mu.Unlock()

	// Горутина A: ServeHTTP на /mcp/schema (внутри — чтение inst из контекста).
	// Держит RLock до завершения запроса.
	req := httptest.NewRequest(http.MethodGet, "/mcp/schema", nil)
	req.Header.Set("X-Tenant-ID", "nested-tenant")
	rec := httptest.NewRecorder()
	reqDone := make(chan struct{})
	go func() {
		ts.ServeHTTP(rec, req)
		close(reqDone)
	}()

	// Даём запросу начаться (дойти до роутера /mcp/schema).
	time.Sleep(100 * time.Millisecond)

	// Горутина B: writer (ReloadTenant под ts.mu.Lock) — блокируется, пока
	// запрос A держит RLock. Конфиг пишем на диск заранее (как в
	// TestServeHTTP_HoldsReadLockDuringRequest).
	cfgPath := filepath.Join(t.TempDir(), "nested.json")
	if err := writeConfigForTest(t, cfgPath, cfg); err != nil {
		t.Fatalf("write config: %v", err)
	}
	reloadDone := make(chan error, 1)
	go func() {
		reloadDone <- ts.ReloadTenant(context.Background(), "nested-tenant", cfgPath)
	}()

	// Если deadlock — запрос A не завершится (завис на вложенном RLock).
	// Если фикс корректен — A завершается, потом B.
	select {
	case <-reqDone:
		// Запрос завершился — deadlock'а нет.
	case <-time.After(5 * time.Second):
		t.Fatal("ServeHTTP(/mcp/schema) deadlocked under concurrent ReloadTenant — nested resolveTenant still re-locks")
	}

	// Writer должен пройти после снятия RLock.
	select {
	case err := <-reloadDone:
		if err != nil {
			t.Fatalf("ReloadTenant after request: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ReloadTenant did not complete after request finished")
	}

	if rec.Code != http.StatusOK && rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("/mcp/schema status = %d, want 200 or 503", rec.Code)
	}
}

// TestServeHTTP_OpenAPI_NestedResolve_NoDeadlock — то же для /openapi.json.
func TestServeHTTP_OpenAPI_NestedResolve_NoDeadlock(t *testing.T) {
	ts := newTestTenantStore(t)
	cfg := newInMemoryConfig(t)
	inst, err := ts.AddTenant(context.Background(), "openapi-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	router, err := NewRouterFromConfig(ts, inst.Config, inst.AdapterSub)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}
	ts.mu.Lock()
	inst.Router = router
	ts.mu.Unlock()

	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	req.Header.Set("X-Tenant-ID", "openapi-tenant")
	rec := httptest.NewRecorder()
	reqDone := make(chan struct{})
	go func() {
		ts.ServeHTTP(rec, req)
		close(reqDone)
	}()

	time.Sleep(100 * time.Millisecond)

	cfgPath := filepath.Join(t.TempDir(), "openapi.json")
	if err := writeConfigForTest(t, cfgPath, cfg); err != nil {
		t.Fatalf("write config: %v", err)
	}
	reloadDone := make(chan error, 1)
	go func() {
		reloadDone <- ts.ReloadTenant(context.Background(), "openapi-tenant", cfgPath)
	}()

	select {
	case <-reqDone:
	case <-time.After(5 * time.Second):
		t.Fatal("ServeHTTP(/openapi.json) deadlocked under concurrent ReloadTenant")
	}

	select {
	case err := <-reloadDone:
		if err != nil {
			t.Fatalf("ReloadTenant after request: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ReloadTenant did not complete after request finished")
	}

	if rec.Code != http.StatusOK {
		t.Fatalf("/openapi.json status = %d, want 200", rec.Code)
	}
}
