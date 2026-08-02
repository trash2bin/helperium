package server

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestApproveToolDoesNotRegenerateEntities — регресс конфликта шага 5 и пункта 6:
// approve-tool хендлер НЕ должен регенерировать Entities/Endpoints через Hydrate,
// потому что это стирает ручные point-фиксы (Description эндпоинта, поправленный
// через PUT /admin/config). approve-tool меняет только ApprovedTools.
//
// Сценарий:
//  1. Tenant с закэшированной схемой (как после rewrite)
//  2. Ручная правка Description одного Endpoint (легитимный point-fix)
//  3. approve одного write-тула
//  4. reload конфига с диска (RegenerateAndPersistTenantConfig пишет на диск — проверяем то,
//     что реально подхватит сервер после reload)
//  5. assert: Description пережил approve
func TestApproveToolDoesNotRegenerateEntities(t *testing.T) {
	// 1. Файловая БД + схема с FK (чтобы была регенерация возможна).
	dbPath := filepath.Join(t.TempDir(), "approve_test.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
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

	// 2. Tenant с полным конфигом + кэш схемы (имитируем состояние после rewrite).
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   config.DriverSQLite,
			DSN:      dbPath,
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{
			{Name: "products", Table: "products", IDColumn: "id", Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
			}},
		},
		Endpoints: []config.Endpoint{
			// Ручная правка Description — легитимный point-fix.
			{Method: "GET", Path: "/products/grep", Op: config.OpStrategy, Strategy: "grep", Entity: "products",
				Description: "РУЧНОЕ описание grep products"},
			// Write endpoint для approve: метод POST + custom_query (валидный write-тул).
			{Method: "POST", Path: "/products/create", Op: config.OpCustomQuery, QueryID: "create_product", Entity: "products",
				Description: "Write endpoint для approve"},
		},
		CustomQueries: map[string]config.CustomQuery{
			"create_product": {
				SQL:           "INSERT INTO products (name) VALUES (?)",
				Params:        []string{"name"},
				MaxRows:       1,
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}

	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	ts.TenantsDir = t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	if _, err := ts.AddTenant(ctx, "test-approve", cfg, ""); err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Кэшируем схему (как это делает adminRewriteHandler после интроспекции).
	ts.SaveTenantSchema("test-approve", &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "brands", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "int"}, {Name: "name", Type: "string"},
			}},
			{Name: "products", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "int"}, {Name: "name", Type: "string"}, {Name: "brand_id", Type: "int"},
			}, ForeignKeys: []datasource.ForeignKey{
				{Name: "fk_products_brand", Columns: []string{"brand_id"}, ReferencedTable: "brands", ReferencedColumns: []string{"id"}},
			}},
		},
	})

	_, ok := ts.GetTenant("test-approve")
	if !ok {
		t.Fatal("tenant not found")
	}

	// 3. Approve write-тула.
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants/test-approve/tools/products/approve", nil)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "test-approve")
	rctx.URLParams.Add("toolName", "query_create_product")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminTenantApproveToolHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("approve: status %d, body %s", rec.Code, rec.Body.String())
	}

	// 4. reload конфига с диска: RegenerateAndPersistTenantConfig пишет регенерированный файл,
	// сервер подхватит его при reload — проверяем именно то, что увидит сервер.
	if err := ts.ReloadTenant(ctx, "test-approve", ts.TenantConfigPath("test-approve")); err != nil {
		t.Fatalf("reload after approve: %v", err)
	}

	// 5. Assert: ручной Description эндпоинта пережил approve (после reload с диска).
	inst, ok := ts.GetTenant("test-approve")
	if !ok {
		t.Fatal("tenant not found after reload")
	}
	found := false
	for _, ep := range inst.Config.Endpoints {
		if ep.Path == "/products/grep" && ep.Description == "РУЧНОЕ описание grep products" {
			found = true
			break
		}
	}
	if !found {
		t.Error("ручная правка Description эндпоинта стёрта approve-tool (RegenerateAndPersistTenantConfig регенерировал Entities/Endpoints)")
	}

	// ApprovedTools должен быть записан.
	if len(inst.Config.ApprovedTools) == 0 {
		t.Error("ApprovedTools не сохранён после approve")
	}
}
