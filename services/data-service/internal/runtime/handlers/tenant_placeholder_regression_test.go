package handlers

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// pgStrategyAdapter — тестовый адаптер с PG-плейсхолдерами ($N).
// Используется для проверки, что tenant-плейсхолдер НЕ коллизирует
// с WHERE-аргументами (C2: существующий баг — existingArgCount=0).
//
// БД — sqlite, но перед исполнением $N подменяются на ? (pg → sqlite),
// чтобы реально проверить семантику запроса, сохранив структуру SQL.
type pgStrategyAdapter struct {
	db *sql.DB

	mu      sync.Mutex
	queries []string
}

func (a *pgStrategyAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	a.mu.Lock()
	a.queries = append(a.queries, query)
	a.mu.Unlock()
	// Подмена $N → ? для исполнения на sqlite (modernc требует '?').
	adapted := pgToSQLitePlaceholders(query)
	return a.db.QueryContext(ctx, adapted, args...)
}

// pgToSQLitePlaceholders заменяет $1, $2, ... на ? (для исполнения на sqlite).
func pgToSQLitePlaceholders(query string) string {
	var sb strings.Builder
	for i := 0; i < len(query); i++ {
		ch := query[i]
		if ch == '$' && i+1 < len(query) && query[i+1] >= '0' && query[i+1] <= '9' {
			sb.WriteByte('?')
			// пропускаем цифры
			for i+1 < len(query) && query[i+1] >= '0' && query[i+1] <= '9' {
				i++
			}
			continue
		}
		sb.WriteByte(ch)
	}
	return sb.String()
}

func (a *pgStrategyAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }
func (a *pgStrategyAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (a *pgStrategyAdapter) TranslatePlaceholder(index int) string { return "$" + itoa(index) }

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	neg := i < 0
	if neg {
		i = -i
	}
	var b [20]byte
	pos := len(b)
	for i > 0 {
		pos--
		b[pos] = byte('0' + i%10)
		i /= 10
	}
	if neg {
		pos--
		b[pos] = '-'
	}
	return string(b[pos:])
}

// TestStrategyHandler_TenantPlaceholder_PG_NoCollision (C2) — на PG-адаптере
// tenant-плейсхолдер должен быть $N+1 (после WHERE-аргументов), а не $1.
//
// Баг: tenantFilter вызывался с existingArgCount=0 → tenant получал $1,
// коллизия с WHERE $1..$n → pgx ошибка или неверные результаты.
func TestStrategyHandler_TenantPlaceholder_PG_NoCollision(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			category TEXT NOT NULL DEFAULT '',
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'A', 'cat1', 'tenant-a');
		INSERT INTO products VALUES (2, 'B', 'cat2', 'tenant-a');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &pgStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "category", Column: "category", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "category", Column: "category", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	strategy := search.NewFilterStrategy("id", "name")
	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)
	req := httptest.NewRequest(http.MethodGet, "/products/filter?category=cat1&limit=5", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// Проверяем SQL: tenant-плейсхолдер должен быть $3 (после category=$1, limit=$2),
	// а НЕ $1 (коллизия). Ищем SELECT-запрос (не count).
	var selectSQL string
	adapter.mu.Lock()
	for _, q := range adapter.queries {
		t.Logf("captured query: %s", q)
		// SELECT с явным списком колонок (не COUNT) — data-запрос.
		if strings.Contains(q, "SELECT \"id\"") {
			selectSQL = q
			break
		}
	}
	adapter.mu.Unlock()

	if selectSQL == "" {
		t.Fatal("no SELECT query captured")
	}

	// WHERE "category" = $1 AND "tenant_id" = $2 LIMIT $3 — порядок args:
	// category=$1, tenant=$2, limit=$3. Если tenant всё ещё $1 — коллизия.
	if strings.Contains(selectSQL, `"tenant_id" = $1`) {
		t.Errorf("BUG C2: tenant placeholder collides with WHERE ($1). SQL: %s", selectSQL)
	}
	if !strings.Contains(selectSQL, `"tenant_id" = $2`) {
		t.Errorf("expected tenant placeholder $2 after category=$1. SQL: %s", selectSQL)
	}
	if !strings.Contains(selectSQL, "LIMIT $3") {
		t.Errorf("expected LIMIT $3 after tenant=$2. SQL: %s", selectSQL)
	}
}

// TestStrategyHandler_FormatCount_TenantFilter (C3) — format=count + tenant-фильтр.
//
// Баг: SELECT COUNT(*) оборачивался в подзапрос 'SELECT COUNT(*) FROM (...) AS _cnt
// WHERE tenant' — в агрегате нет колонки tenant_id → "no such column" → 500.
func TestStrategyHandler_FormatCount_TenantFilter(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			category TEXT NOT NULL DEFAULT '',
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'A', 'cat1', 'tenant-a');
		INSERT INTO products VALUES (2, 'B', 'cat1', 'tenant-a');
		INSERT INTO products VALUES (3, 'C', 'cat1', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "category", Column: "category", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "category", Column: "category", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	strategy := search.NewFilterStrategy("id", "name")
	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)
	// format=count + category=cat1 → tenant-a видит 2 строки (не 3).
	req := httptest.NewRequest(http.MethodGet, "/products/filter?category=cat1&format=count", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("format=count + tenant: expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}
	if resp["count"].(float64) != 2 {
		t.Errorf("format=count + tenant: expected count=2 (tenant-a only), got %v", resp["count"])
	}
}

// TestStrategyHandler_SortBy_TenantFilter (C4) — sort_by + tenant-фильтр.
//
// Баг: insertTenantBeforeLimit вставлял tenant-клаузу ПЕРЕД LIMIT, но ПОСЛЕ
// ORDER BY → 'ORDER BY x DESC AND tenant' → синтаксическая ошибка.
func TestStrategyHandler_SortBy_TenantFilter(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			price INTEGER NOT NULL DEFAULT 0,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'A', 100, 'tenant-a');
		INSERT INTO products VALUES (2, 'B', 200, 'tenant-a');
		INSERT INTO products VALUES (3, 'C', 300, 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "price", Column: "price", Type: "int"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "price", Column: "price", Type: config.FieldTypeInt},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	strategy := search.NewFilterStrategy("id", "name")
	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)
	// sort_by=price (desc) + tenant-фильтр: раньше ломал SQL.
	req := httptest.NewRequest(http.MethodGet, "/products/filter?price__gte=0&sort_by=price&sort_dir=desc&limit=10", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("sort_by + tenant: expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var result query.SearchResult
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}
	if result.Total != 2 {
		t.Errorf("sort_by + tenant: expected Total=2 (tenant-a only), got %d", result.Total)
	}
}

// TestStrategyHandler_GrepRawWhere_TenantFilter_NoTenantIDLeak (L2) — grep
// (RawWhere-план) + tenant-фильтр: раньше внутренний ensureColumn(tenant_id)
// протекал наружу через внешний 'SELECT * FROM (...) AS _t WHERE tenant_id = ?',
// и MapRow включал системную колонку tenant_id в JSON-ответ.
//
// После фикса внешний SELECT строит явный список колонок БЕЗ tenant_id →
// в ответе (format=full → data rows) не должно быть ключа "tenant_id".
func TestStrategyHandler_GrepRawWhere_TenantFilter_NoTenantIDLeak(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'TenantA Widget', 'tenant-a');
		INSERT INTO products VALUES (2, 'TenantB Widget', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	// Grep-стратегия → RawWhere-план (multi-token AND LIKE) → tenant-обёртка в подзапрос.
	strategy := search.NewGrepStrategy("id", "name")
	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)
	// format=full → data rows с полными колонками (там и видна утечка tenant_id).
	req := httptest.NewRequest(http.MethodGet, "/products/grep?pattern=Widget&format=full", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("grep tenant-a full response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var result query.SearchResult
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	if result.Total != 1 {
		t.Errorf("tenant-a: expected Total=1 (only tenant-a row matches Widget), got %d", result.Total)
	}
	if len(result.Data) != 1 {
		t.Fatalf("tenant-a: expected 1 full data row, got %d", len(result.Data))
	}
	for i, row := range result.Data {
		if _, ok := row["tenant_id"]; ok {
			t.Errorf("BUG L2: data row %d leaks system column tenant_id: %v", i, row)
		}
		if _, ok := row["id"]; !ok {
			t.Errorf("data row %d missing id column: %v", i, row)
		}
		if _, ok := row["name"]; !ok {
			t.Errorf("data row %d missing name column: %v", i, row)
		}
	}
}
