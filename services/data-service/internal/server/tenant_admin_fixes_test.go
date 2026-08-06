package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// --- Задача 1: POST /admin/config/reload всегда 500 с пустым путём ---

func TestTenantAdmin_ConfigReload_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	// Tenant создан без configPath; конфиг ещё не на диске.
	// Сохраняем его, чтобы ReloadTenant мог прочитать файл.
	if p := ts.SaveTenantConfig("test-tenant", inst.Config); p == "" {
		t.Fatal("SaveTenantConfig returned empty path")
	}

	req := httptest.NewRequest(http.MethodPost, "/admin/config/reload", nil)
	req.Header.Set("X-Tenant-ID", "test-tenant")
	rec := httptest.NewRecorder()
	ts.adminConfigReloadHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if body["status"] != "reloaded" {
		t.Errorf("expected status=reloaded, got %v", body["status"])
	}
}

// --- Задача 2: атомарная запись конфига (write temp + rename) ---

func TestSaveTenantConfig_AtomicWrite(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()

	cfg := &config.Config{Version: 1}
	path := ts.SaveTenantConfig("atomic-tenant", cfg)
	if path == "" {
		t.Fatal("SaveTenantConfig returned empty path")
	}

	// Файл должен существовать и парситься.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	var got config.Config
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.Version != 1 {
		t.Errorf("version = %d, want 1", got.Version)
	}

	// Не должно остаться временных файлов.
	entries, err := os.ReadDir(ts.TenantsDir)
	if err != nil {
		t.Fatalf("readdir: %v", err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") || strings.Contains(e.Name(), "~") {
			t.Errorf("leftover temp file: %s", e.Name())
		}
	}
}

// --- Задача 3: ReadonlyDSN round-trip через GET/PUT ---

func TestAdminConfig_ReadonlyDSN_RoundTrip(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	inst.Config.DataSource.ReadonlyDSN = "file:readonly.db"

	// GET /admin/config должен отдать readonly_dsn (маскированный или полный).
	getReq := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	getReq.Header.Set("X-Tenant-ID", "test-tenant")
	getRec := httptest.NewRecorder()
	ts.adminConfigHandler(getRec, getReq)

	if getRec.Code != http.StatusOK {
		t.Fatalf("GET: expected 200, got %d: %s", getRec.Code, getRec.Body.String())
	}
	var resp map[string]any
	if err := json.Unmarshal(getRec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal GET: %v", err)
	}
	ds, _ := resp["data_source"].(map[string]any)
	if ds == nil {
		t.Fatal("data_source missing in GET response")
	}
	if ds["has_readonly_dsn"] != true {
		t.Errorf("has_readonly_dsn = %v, want true", ds["has_readonly_dsn"])
	}

	// PUT с телом, где readonly_dsn пуст — не должен стереть сохранённый.
	body := map[string]any{
		"version": 1,
		"data_source": map[string]any{
			"driver":           "sqlite",
			"dsn":              inst.Config.DataSource.DSN,
			"read_only":        true,
			"readonly_dsn":     "",
			"has_readonly_dsn": false,
		},
		"entities":  inst.Config.Entities,
		"endpoints": inst.Config.Endpoints,
	}
	raw, _ := json.Marshal(body)
	putReq := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader(string(raw)))
	putReq.Header.Set("X-Tenant-ID", "test-tenant")
	putRec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(putRec, putReq)
	if putRec.Code != http.StatusOK {
		t.Fatalf("PUT: expected 200, got %d: %s", putRec.Code, putRec.Body.String())
	}

	inst2, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found after PUT")
	}
	if inst2.Config.DataSource.ReadonlyDSN != "file:readonly.db" {
		t.Errorf("ReadonlyDSN lost after PUT: %q", inst2.Config.DataSource.ReadonlyDSN)
	}
}

// --- Задача 4: /admin/config/versions архивирует при PUT ---

func TestAdminConfig_Versions_AfterPut(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	if p := ts.SaveTenantConfig("test-tenant", inst.Config); p == "" {
		t.Fatal("SaveTenantConfig returned empty path")
	}

	// Первый PUT — архив старой версии.
	body := map[string]any{
		"version": 1,
		"data_source": map[string]any{
			"driver": "sqlite",
			"dsn":    inst.Config.DataSource.DSN,
		},
		"entities":  inst.Config.Entities,
		"endpoints": inst.Config.Endpoints,
	}
	raw, _ := json.Marshal(body)
	putReq := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader(string(raw)))
	putReq.Header.Set("X-Tenant-ID", "test-tenant")
	putRec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(putRec, putReq)
	if putRec.Code != http.StatusOK {
		t.Fatalf("PUT: expected 200, got %d: %s", putRec.Code, putRec.Body.String())
	}

	// Versions должен содержать хотя бы один config.*.json.
	verReq := httptest.NewRequest(http.MethodGet, "/admin/config/versions", nil)
	verReq.Header.Set("X-Tenant-ID", "test-tenant")
	verRec := httptest.NewRecorder()
	ts.adminConfigVersionsHandler(verRec, verReq)
	if verRec.Code != http.StatusOK {
		t.Fatalf("versions: expected 200, got %d: %s", verRec.Code, verRec.Body.String())
	}
	var versions []map[string]any
	if err := json.Unmarshal(verRec.Body.Bytes(), &versions); err != nil {
		t.Fatalf("unmarshal versions: %v", err)
	}
	if len(versions) == 0 {
		t.Error("expected at least one archived version after PUT, got none")
	}
}

// --- Helpers ---
