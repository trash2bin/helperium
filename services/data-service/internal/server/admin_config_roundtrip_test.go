package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestAdminConfig_RoundTripPreservesFieldRules — регресс-тест пробела DTO:
// GET /admin/config не отдавал FilterableRules/SearchableRules/EnumRules/
// DisabledDefault*/CustomShortNames, поэтому round-trip
// GET → PUT (сохранение тела клиентом) терял кастомизации.
//
// Требование: админка должна видеть и сохранять ВСЕ поля намерений.
func TestAdminConfig_RoundTripPreservesFieldRules(t *testing.T) {
	// 1. Tenant с кастомизациями.
	dbPath := t.TempDir() + "/roundtrip.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL, secret_note TEXT)`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}
	_ = db.Close()

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		FilterableRules: []config.FieldRule{
			{AllowNames: []string{"secret_note"}, Reason: "User rule: secret_note filterable"},
		},
		SearchableRules: []config.FieldRule{
			{BlockContains: []string{"secret"}, Reason: "User rule: block secret"},
		},
		EnumRules: []config.FieldRule{
			{AllowContains: []string{"category"}, Reason: "User rule: category enum"},
		},
		DisabledDefaultFilterableRules: []string{"filterable.common"},
		DisabledDefaultSearchableRules: []string{"searchable.block_image"},
		DisabledDefaultEnumRules:       []string{"enum.contains"},
		CustomShortNames:               map[string]string{"products": "Product catalog"},
	}

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "test-rt", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// 2. GET /admin/config → DTO.
	getReq := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	getReq.Header.Set("X-Tenant-ID", "test-rt")
	getRec := httptest.NewRecorder()
	ts.adminConfigHandler(getRec, getReq)

	if getRec.Code != http.StatusOK {
		t.Fatalf("GET config: status %d, body %s", getRec.Code, getRec.Body.String())
	}

	// 3. Проверяем, что ВСЕ поля кастомизации есть в ответе.
	var got struct {
		FilterableRules                []config.FieldRule `json:"filterable_rules"`
		SearchableRules                []config.FieldRule `json:"searchable_rules"`
		EnumRules                      []config.FieldRule `json:"enum_rules"`
		DisabledDefaultFilterableRules []string           `json:"disabled_default_filterable_rules"`
		DisabledDefaultSearchableRules []string           `json:"disabled_default_searchable_rules"`
		DisabledDefaultEnumRules       []string           `json:"disabled_default_enum_rules"`
		CustomShortNames               map[string]string  `json:"custom_short_names"`
	}
	if err := json.Unmarshal(getRec.Body.Bytes(), &got); err != nil {
		t.Fatalf("parse GET response: %v", err)
	}

	checkFieldRule := func(field, wantReason string, rules []config.FieldRule) {
		t.Helper()
		if len(rules) == 0 {
			t.Errorf("%s: пусто в ответе GET /admin/config — round-trip потеряет его", field)
			return
		}
		for _, r := range rules {
			if r.Reason == wantReason {
				return
			}
		}
		t.Errorf("%s: правило %q не найдено в ответе: %+v", field, wantReason, rules)
	}
	checkFieldRule("filterable_rules", "User rule: secret_note filterable", got.FilterableRules)
	checkFieldRule("searchable_rules", "User rule: block secret", got.SearchableRules)
	checkFieldRule("enum_rules", "User rule: category enum", got.EnumRules)

	if len(got.DisabledDefaultFilterableRules) == 0 {
		t.Error("disabled_default_filterable_rules пусто в ответе")
	}
	if len(got.DisabledDefaultSearchableRules) == 0 {
		t.Error("disabled_default_searchable_rules пусто в ответе")
	}
	if len(got.DisabledDefaultEnumRules) == 0 {
		t.Error("disabled_default_enum_rules пусто в ответе")
	}
	if got.CustomShortNames["products"] != "Product catalog" {
		t.Errorf("custom_short_names потерян: %v", got.CustomShortNames)
	}
}

// TestAdminConfig_PutRoundTrip — клиент читает GET, шлёт тело обратно в PUT,
// конфиг не должен потерять кастомизации (DSN мержится сервером).
func TestAdminConfig_PutRoundTrip(t *testing.T) {
	dbPath := t.TempDir() + "/put_rt.db"
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}
	_ = db.Close()

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		FilterableRules:  []config.FieldRule{{AllowNames: []string{"secret_note"}, Reason: "User rule"}},
		CustomShortNames: map[string]string{"products": "Product catalog"},
	}

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "test-put", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// GET.
	getReq := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	getReq.Header.Set("X-Tenant-ID", "test-put")
	getRec := httptest.NewRecorder()
	ts.adminConfigHandler(getRec, getReq)
	if getRec.Code != http.StatusOK {
		t.Fatalf("GET: %d %s", getRec.Code, getRec.Body.String())
	}

	// PUT: тело ровно то, что вернул GET (как делает админка).
	putReq := httptest.NewRequest(http.MethodPost, "/admin/config",
		strings.NewReader(getRec.Body.String()))
	putReq.Header.Set("X-Tenant-ID", "test-put")
	putRec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(putRec, putReq)
	if putRec.Code != http.StatusOK {
		t.Fatalf("PUT: %d %s", putRec.Code, putRec.Body.String())
	}

	// Проверяем сохранённый конфиг.
	inst, ok := ts.GetTenant("test-put")
	if !ok {
		t.Fatal("tenant not found")
	}
	if len(inst.Config.FilterableRules) == 0 {
		t.Error("FilterableRules потеряны после GET→PUT round-trip")
	}
	if inst.Config.CustomShortNames["products"] != "Product catalog" {
		t.Errorf("CustomShortNames потеряны после round-trip: %v", inst.Config.CustomShortNames)
	}
}
