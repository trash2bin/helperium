package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// TestIntrospectedSchema_ConcurrentWriteRead — регресс data race на
// inst.IntrospectedSchema.
//
// Проблема: adminRewriteHandler писал inst.IntrospectedSchema без лока,
// а /mcp/schema handler читал его + разыменовывал (GenerateSchemaForLLM)
// тоже без лока. Два конкурентных запроса (rewrite + mcp/schema) → data race.
//
// Тест: горутина-писатель делает ровно то же, что adminRewriteHandler
// (inst.IntrospectedSchema = schema), горутины-читатели идут через настоящий
// /mcp/schema handler (роутер из NewRouterFromConfig). Под -race до фикса
// детектор ловит гонку; после фикса — чисто.
func TestIntrospectedSchema_ConcurrentWriteRead(t *testing.T) {
	ts := newTestTenantStore(t)
	cfg := newInMemoryConfig(t)

	inst, err := ts.AddTenant(context.Background(), "schema-race", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	router, err := NewRouterFromConfig(ts, inst.Config, inst.AdapterSub)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}

	schema := &datasource.Schema{
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
				},
			},
		},
	}

	// Писатель — как adminRewriteHandler после фикса: inst.IntrospectedSchema
	// пишется под schemaMu.Lock (в противном случае тест сам создаёт гонку).
	writer := func(wg *sync.WaitGroup) {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			inst.schemaMu.Lock()
			inst.IntrospectedSchema = schema
			inst.schemaMu.Unlock()
		}
	}

	// Читатели — как /mcp/schema handler через роутер.
	reader := func(wg *sync.WaitGroup) {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			req := httptest.NewRequest(http.MethodGet, "/mcp/schema", nil)
			req.Header.Set("X-Tenant-ID", "schema-race")
			w := httptest.NewRecorder()
			router.ServeHTTP(w, req)
			// 200 (схема есть) или 503 (ещё не записана) — оба допустимы;
			// главное — ни паники, ни data race.
			if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
				t.Errorf("mcp/schema: unexpected status %d", w.Code)
				return
			}
		}
	}

	var wg sync.WaitGroup
	wg.Add(1 + 4)
	go writer(&wg)
	for i := 0; i < 4; i++ {
		go reader(&wg)
	}
	wg.Wait()
}

// TestIntrospectedSchema_RaceDirect — прямой конкурентный доступ к полю
// без HTTP-слоя: дублирует структуру lock-вызовов production-путей.
// Запускается только под -race (как и весь пакет в CI).
func TestIntrospectedSchema_RaceDirect(t *testing.T) {
	ts := newTestTenantStore(t)
	cfg := newInMemoryConfig(t)
	inst, err := ts.AddTenant(context.Background(), "schema-race-2", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	schema := &datasource.Schema{
		Tables: []datasource.Table{
			{Name: "products", Columns: []datasource.Column{{Name: "id", Type: "int"}}},
		},
	}

	var wg sync.WaitGroup
	wg.Add(2)
	// Писатель: тот же паттерн, что adminRewriteHandler (schemaMu.Lock в фиксе).
	go func() {
		defer wg.Done()
		for i := 0; i < 500; i++ {
			inst.schemaMu.Lock()
			inst.IntrospectedSchema = schema
			inst.schemaMu.Unlock()
		}
	}()
	// Читатель: тот же паттерн, что /mcp/schema (schemaMu.RLock в фиксе).
	go func() {
		defer wg.Done()
		for i := 0; i < 500; i++ {
			inst.schemaMu.RLock()
			_ = inst.IntrospectedSchema
			inst.schemaMu.RUnlock()
		}
	}()
	wg.Wait()
}
