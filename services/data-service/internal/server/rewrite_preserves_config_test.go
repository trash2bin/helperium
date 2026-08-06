package server

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestRewrite_PreservesCustomConfig — регресс-тест бага:
// POST /admin/config/rewrite стирал кастомные настройки tenant'а
// (FilterableRules, SearchableRules, EnumRules, DisabledDefault*,
// CustomShortNames, явные CustomQueries), т.к. genCfg собирался
// только из 5 полей.
//
// Проверяет, что после rewrite все кастомизации переживают.
func TestRewrite_PreservesCustomConfig(t *testing.T) {
	// 1. Файловая БД (memory нельзя — rewrite делает новый Connect+Introspect).
	dbPath := filepath.Join(t.TempDir(), "rewrite_test.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			category TEXT,
			price REAL,
			secret_note TEXT
		)`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.ExecContext(t.Context(),
		`INSERT INTO products (name, category, price, secret_note) VALUES
		 ('Глушитель BMW', 'Выхлопная система', 28500, 's1'),
		 ('Масло 5W30', 'Масла', 3200, 's2')`); err != nil {
		_ = db.Close()
		t.Fatalf("insert: %v", err)
	}
	_ = db.Close()

	// 2. Кастомные правила, которые ДОЛЖНЫ пережить rewrite.
	customFilterable := []config.FieldRule{
		{AllowNames: []string{"secret_note"}, Reason: "User rule: secret_note filterable"},
	}
	customSearchable := []config.FieldRule{
		{BlockContains: []string{"secret"}, Reason: "User rule: block secret"},
	}
	customEnum := []config.FieldRule{
		{AllowContains: []string{"category"}, Reason: "User rule: category enum"},
	}
	customShortNames := map[string]string{"products": "Product catalog"}

	// Явный custom query (не FK-производный).
	explicitQueries := map[string]config.CustomQuery{
		"expensive_parts": {
			SQL:         "SELECT name, price FROM products WHERE price > ?",
			Params:      []string{"min_price"},
			MaxRows:     10,
			Description: "Дорогие запчасти",
		},
	}

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		FilterableRules:                customFilterable,
		SearchableRules:                customSearchable,
		EnumRules:                      customEnum,
		DisabledDefaultFilterableRules: []string{"filterable.common"},
		CustomShortNames:               customShortNames,
		CustomQueries:                  explicitQueries,
	}

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "test-rewrite", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// 3. Вызов adminRewriteHandler (это и есть /admin/config/rewrite).
	req := httptest.NewRequest(http.MethodPost, "/admin/config/rewrite?tenant=test-rewrite", nil)
	req.Header.Set("X-Tenant-ID", "test-rewrite")
	rec := httptest.NewRecorder()
	ts.adminRewriteHandler(nil, "")(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("rewrite: status %d, body %s", rec.Code, rec.Body.String())
	}

	// 4. Перечитываем сохранённый конфиг и проверяем сохранность кастомизаций.
	persisted, ok := ts.GetTenant("test-rewrite")
	if !ok {
		t.Fatal("tenant not found after rewrite")
	}
	got := persisted.Config

	// FilterableRules: кастомное правило должно остаться.
	if len(got.FilterableRules) == 0 {
		t.Fatal("FilterableRules lost after rewrite")
	}
	foundCustom := false
	for _, r := range got.FilterableRules {
		if r.Reason == "User rule: secret_note filterable" {
			foundCustom = true
		}
	}
	if !foundCustom {
		t.Errorf("custom FilterableRule lost after rewrite: %+v", got.FilterableRules)
	}
	// DisabledDefaultFilterableRules тоже.
	if len(got.DisabledDefaultFilterableRules) == 0 {
		t.Error("DisabledDefaultFilterableRules lost after rewrite")
	}

	// SearchableRules: блок secret.
	if len(got.SearchableRules) == 0 {
		t.Error("SearchableRules lost after rewrite")
	} else {
		found := false
		for _, r := range got.SearchableRules {
			if r.Reason == "User rule: block secret" {
				found = true
			}
		}
		if !found {
			t.Errorf("custom SearchableRule lost: %+v", got.SearchableRules)
		}
	}

	// EnumRules: категория.
	if len(got.EnumRules) == 0 {
		t.Error("EnumRules lost after rewrite")
	} else {
		found := false
		for _, r := range got.EnumRules {
			if r.Reason == "User rule: category enum" {
				found = true
			}
		}
		if !found {
			t.Errorf("custom EnumRule lost: %+v", got.EnumRules)
		}
	}

	// CustomShortNames.
	if got.CustomShortNames["products"] != "Product catalog" {
		t.Errorf("CustomShortNames lost: %v", got.CustomShortNames)
	}

	// Явный CustomQuery пережил rewrite.
	if _, ok := got.CustomQueries["expensive_parts"]; !ok {
		t.Errorf("explicit CustomQuery lost after rewrite; got: %v", keys(got.CustomQueries))
	}
}

// TestRewrite_CustomQueries_Idempotent — повторный rewrite не дублирует
// явные custom queries (они не должны быть перезаписаны FK-производными,
// а FK-производные не должны затираться).
func TestRewrite_CustomQueries_Idempotent(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "rewrite_idem.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	// Таблица с FK, чтобы buildNavigationEndpoints что-то сгенерил.
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE brands (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
		 CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			brand_id INTEGER,
			FOREIGN KEY (brand_id) REFERENCES brands(id)
		 )`); err != nil {
		_ = db.Close()
		t.Fatalf("create tables: %v", err)
	}
	_ = db.Close()

	explicit := map[string]config.CustomQuery{
		"expensive_parts": {
			SQL:         "SELECT name, price FROM products WHERE price > ?",
			Params:      []string{"min_price"},
			MaxRows:     10,
			Description: "Дорогие запчасти",
		},
	}

	cfg := &config.Config{
		DataSource:    config.DataSourceConfig{Driver: config.DriverSQLite, DSN: dbPath, ReadOnly: boolPtr(true)},
		CustomQueries: explicit,
	}

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "test-idem", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// rewrite ×2 — идемпотентность.
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodPost, "/admin/config/rewrite?tenant=test-idem", nil)
		req.Header.Set("X-Tenant-ID", "test-idem")
		rec := httptest.NewRecorder()
		ts.adminRewriteHandler(nil, "")(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("rewrite #%d: status %d, body %s", i+1, rec.Code, rec.Body.String())
		}
	}

	got, ok := ts.GetTenant("test-idem")
	if !ok {
		t.Fatal("tenant not found after rewrite")
	}

	// Явный query есть ровно один раз.
	if _, ok := got.Config.CustomQueries["expensive_parts"]; !ok {
		t.Errorf("explicit CustomQuery lost: %v", keys(got.Config.CustomQueries))
	}
	// FK-производный (products_by_brands) есть.
	navFound := false
	for id := range got.Config.CustomQueries {
		if strings.Contains(id, "by_brands") || strings.Contains(id, "by_brand") {
			navFound = true
		}
	}
	if !navFound {
		t.Errorf("nav custom_query not generated: %v", keys(got.Config.CustomQueries))
	}
}

func boolPtr(b bool) *bool { return &b }

func keys(m map[string]config.CustomQuery) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
