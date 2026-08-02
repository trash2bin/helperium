// Package server — multi-tenant support (фаза 3.7).
//
// TenantStore manages N configurations, N database connections, and N routers
// per process. Implements http.Handler — routing by X-Tenant-ID from context.
//
// Architecture:
//
//	                   ┌─ tenant-a → config_a → pg_a → router_a
//	X-Tenant-ID: a ────┤
//	                   ├─ tenant-b → config_b → pg_b → router_b
//	X-Tenant-ID: b ────┤
//	                   └─ default (no header)
//
// Lifecycle:
//   - SetDefault bootstraps the fallback tenant (no X-Tenant-ID → default)
//   - AddTenant adds new tenants at runtime via admin API
//   - RemoveTenant closes connections and removes from map
//   - ReloadTenant rebuilds router for a tenant from updated config
package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/configgen"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── TenantInstance ──

// TenantInstance holds all state for one tenant: config, DB connection, and router.
type TenantInstance struct {
	ID           string                // tenant identifier (matches X-Tenant-ID header)
	Config       *config.Config        // loaded and validated
	Conn         datasource.Conn       // tenant's main DB connection pool (readwrite DSN)
	ReadonlyConn datasource.Conn       // tenant's read-only DB connection (when readonly_dsn is set; nil otherwise)
	Adapter      datasource.Adapter    // full adapter for admin/introspection
	AdapterSub   runtime.AdapterSubset // Conn+Adapter wrapper for handlers — wraps ReadonlyConn if set, else Conn
	Router       http.Handler          // built chi router for this tenant
	ConfigPath   string                // path to the JSON config file (for hot reload)
	CreatedAt    time.Time

	// healthMu guards Healthy and LastError — health check goroutines write,
	// admin handlers read. Both must acquire healthMu.Lock() before access.
	// Pointer to prevent data races when TenantInstance is inadvertently copied.
	healthMu           *sync.Mutex
	Healthy            bool               // (guarded by healthMu) last health ping result
	LastError          string             // (guarded by healthMu) last error message if unhealthy
	IntrospectedSchema *datasource.Schema // cached result of last Introspect (set by /admin/config/rewrite)

	// schemaMu guards IntrospectedSchema: adminRewriteHandler writes it
	// (tenant_admin.go), /mcp/schema handler reads it (endpoint_builder.go).
	// Pointer to prevent data races when TenantInstance is inadvertently copied.
	schemaMu *sync.RWMutex

	// removing marks that RemoveTenant has begun deregistering this instance.
	// Set under ts.mu.Lock before the connection pools are drained/closed, so
	// resolveTenant/resolveTenantAndLock return nil for it immediately and new
	// requests never touch a half-closed instance.
	removing atomic.Bool
}

// ── TenantStore ──

// TenantStore manages multiple TenantInstances with RWMutex and
// implements http.Handler — routing by X-Tenant-ID from context.
type TenantStore struct {
	mu      sync.RWMutex
	tenants map[string]*TenantInstance

	registry *datasource.Registry // all registered datasource.Adapter drivers

	adminRouter http.Handler // chi sub-router for /admin/* (built once)
	hasAdmin    bool         // true when introspect adapter is available (for /openapi.json)

	TenantsDir string // directory for persisting tenant configs (.data/tenants/)
}

// NewTenantStore creates an empty TenantStore with the given registry.
func NewTenantStore(registry *datasource.Registry, tenantsDir string) *TenantStore {
	return &TenantStore{
		tenants:    make(map[string]*TenantInstance),
		registry:   registry,
		TenantsDir: tenantsDir,
	}
}

// ── Config Persistence ──

// TenantConfigPath returns the filesystem path for persisting this tenant's config.
// Uses TenantsDir/{id}.json. Creates the directory if needed.
func (ts *TenantStore) TenantConfigPath(id string) string {
	if ts.TenantsDir == "" {
		return ""
	}
	return filepath.Join(ts.TenantsDir, id+".json")
}

// SaveTenantConfig persists the tenant config to disk and returns the config path.
// Returns empty string if TenantsDir is not configured.
// Запись атомарная: пишем во временный файл в той же директории, затем
// os.Rename. При крэше в середине записи на диске остаётся либо старый,
// либо новый файл — никогда битый JSON.
func (ts *TenantStore) SaveTenantConfig(id string, cfg *config.Config) string {
	if ts.TenantsDir == "" {
		return ""
	}
	if err := os.MkdirAll(ts.TenantsDir, 0755); err != nil {
		slog.Warn("save config: failed to create tenants directory", "tenant", id, "error", err)
		return ""
	}
	persistPath := filepath.Join(ts.TenantsDir, id+".json")
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		slog.Warn("save config: marshal error", "tenant", id, "error", err)
		return ""
	}
	// Temp-файл в той же директории (обязательно, иначе Rename не атомарный
	// при переходе через файловую систему).
	tmp, err := os.CreateTemp(ts.TenantsDir, id+".json.tmp*")
	if err != nil {
		slog.Warn("save config: create temp error", "tenant", id, "error", err)
		return ""
	}
	tmpPath := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		slog.Warn("save config: temp write error", "tenant", id, "error", err)
		return ""
	}
	if err := tmp.Chmod(0644); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		slog.Warn("save config: chmod error", "tenant", id, "error", err)
		return ""
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpPath)
		slog.Warn("save config: temp close error", "tenant", id, "error", err)
		return ""
	}
	if err := os.Rename(tmpPath, persistPath); err != nil {
		_ = os.Remove(tmpPath)
		slog.Warn("save config: rename error", "tenant", id, "path", persistPath, "error", err)
		return ""
	}
	slog.Info("save config: persisted", "tenant", id, "path", persistPath)
	return persistPath
}

// ── Schema cache ──
// Схема БД кэшируется рядом с конфигом, чтобы RegenerateAndPersistTenantConfig мог
// перегенерировать Entities/Endpoints/MCPTools из intent без повторной
// интроспекции (которая требует живого соединения с клиентской БД).

// TenantSchemaPath возвращает путь к кэшу схемы для tenant'а.
func (ts *TenantStore) TenantSchemaPath(id string) string {
	if ts.TenantsDir == "" {
		return ""
	}
	return filepath.Join(ts.TenantsDir, id+".schema.json")
}

// SaveTenantSchema кэширует интроспектированную схему на диск.
// Запись атомарная (паттерн SaveTenantConfig): temp-файл в той же директории
// + os.Rename. При крэше mid-write на диске остаётся старый или новый файл,
// но никогда битый JSON.
func (ts *TenantStore) SaveTenantSchema(id string, schema *datasource.Schema) {
	if ts.TenantsDir == "" || schema == nil {
		return
	}
	if err := os.MkdirAll(ts.TenantsDir, 0755); err != nil {
		slog.Warn("save schema: failed to create tenants directory", "tenant", id, "error", err)
		return
	}
	data, err := json.MarshalIndent(schema, "", "  ")
	if err != nil {
		slog.Warn("save schema: marshal error", "tenant", id, "error", err)
		return
	}
	persistPath := ts.TenantSchemaPath(id)
	// Temp-файл в той же директории (обязательно, иначе Rename не атомарный
	// при переходе через файловую систему).
	tmp, err := os.CreateTemp(ts.TenantsDir, id+".schema.json.tmp*")
	if err != nil {
		slog.Warn("save schema: create temp error", "tenant", id, "error", err)
		return
	}
	tmpPath := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		slog.Warn("save schema: temp write error", "tenant", id, "error", err)
		return
	}
	if err := tmp.Chmod(0644); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		slog.Warn("save schema: chmod error", "tenant", id, "error", err)
		return
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpPath)
		slog.Warn("save schema: temp close error", "tenant", id, "error", err)
		return
	}
	if err := os.Rename(tmpPath, persistPath); err != nil {
		_ = os.Remove(tmpPath)
		slog.Warn("save schema: rename error", "tenant", id, "path", persistPath, "error", err)
	}
}

// LoadTenantSchema читает кэш схемы с диска. Возвращает (nil, nil) если нет файла.
func (ts *TenantStore) LoadTenantSchema(id string) (*datasource.Schema, error) {
	path := ts.TenantSchemaPath(id)
	if path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var schema datasource.Schema
	if err := json.Unmarshal(data, &schema); err != nil {
		return nil, err
	}
	return &schema, nil
}

// RegenerateAndPersistTenantConfig — единая точка записи конфига с РЕГЕНЕРАЦИЕЙ.
// Регенерирует Entities/Endpoints/MCPTools из intent + закэшированной схемы,
// вместо доверия к тому, что вызывающий код руками собрал правильный cfg.
// Если схема ещё не закэширована — сохраняет cfg как есть.
//
// ⚠️ Использовать ТОЛЬКО там, где регенерация — буквально цель операции
// (adminRewriteHandler). НЕ использовать как дефолтный "безопасный save"
// для путей, которые пишут точечные правки (PUT /admin/config):
// Hydrate() перезапишет Entities/Endpoints/MCPTools/Stats.Counters из intent,
// уничтожив ручные правки, не выраженные в intent (напр. Description эндпоинта).
func (ts *TenantStore) RegenerateAndPersistTenantConfig(id string, cfg *config.Config) string {
	schema, err := ts.LoadTenantSchema(id)
	if err != nil {
		slog.Warn("persist config: failed to load cached schema, persisting as-is", "tenant", id, "error", err)
		return ts.SaveTenantConfig(id, cfg)
	}
	if schema == nil {
		return ts.SaveTenantConfig(id, cfg)
	}
	// H-2: кэш схемы может быть устаревшим (БД мигрировали, а rewrite не делали).
	// Логируем использование кэша, чтобы было видно в логах, что конфиг
	// пересобран из КЭША, а не из живой схемы.
	slog.Info("persist config: regenerating from cached schema", "tenant", id)
	full := configgen.Hydrate(configgen.ExtractIntent(cfg), schema)
	return ts.SaveTenantConfig(id, full)
}

// DeleteTenantConfig removes the persisted config file for a tenant,
// and the cached introspection schema sidecar ({id}.schema.json).
func (ts *TenantStore) DeleteTenantConfig(id string) {
	if ts.TenantsDir == "" {
		return
	}
	configPath := filepath.Join(ts.TenantsDir, id+".json")
	if err := os.Remove(configPath); err != nil && !os.IsNotExist(err) {
		slog.Warn("delete config: remove error", "tenant", id, "error", err)
	}
	// Schema cache must not outlive the tenant: a re-created tenant with the same
	// id but a different DB would otherwise hydrate from a stale/foreign schema.
	schemaPath := filepath.Join(ts.TenantsDir, id+".schema.json")
	if err := os.Remove(schemaPath); err != nil && !os.IsNotExist(err) {
		slog.Warn("delete config: remove schema cache error", "tenant", id, "error", err)
	}
}

// ── http.Handler Implementation ──

// ServeHTTP implements http.Handler. Routing:
//
//	/admin/*     → adminRouter (tenant management + config)
//	/health      → multiTenantHealthHandler
//	all others   → extract tenantID from context → tenant's Router
func (ts *TenantStore) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// System endpoints (no tenant required)
	switch path {
	case "/health":
		ts.multiTenantHealthHandler(w, r)
		return
	case "/docs":
		SwaggerHandler(w, r)
		return
	case "/openapi.json":
		NewOpenAPIHandler(ts, ts.hasAdmin)(w, r)
		return
	}

	// Resolve tenant and hold the read lock for the whole request.
	// ReloadTenant/RemoveTenant rewrite inst.Router/inst.Config under ts.mu.Lock,
	// so releasing the RLock before Router.ServeHTTP would let a concurrent reload
	// swap the router mid-request → data race (request served by old router with
	// new config, or vice versa). Holding RLock pins the instance until the request
	// completes.
	inst, unlock := ts.resolveTenantAndLock(r)
	if inst == nil {
		unlock()
		handlers.RespondError(w, http.StatusNotFound, "tenant_not_found",
			"no tenant identifier provided — please use X-Tenant-ID header or ?tenant= query parameter")
		return
	}
	defer unlock()

	// Прокидываем inst в контекст: хендлеры /mcp/schema и /openapi.json внутри
	// tenant-роутера читают его отсюда, а не зовут resolveTenant(r) повторно
	// (второй RLock из-под уже удерживаемого RLock = deadlock при queued writer'е).
	ctx := context.WithValue(r.Context(), tenantInstanceKey, inst)
	inst.Router.ServeHTTP(w, r.WithContext(ctx))
}

// resolveTenant extracts tenantID from request context or query parameter, and looks up the tenant.
// Handles comma-separated X-Tenant-ID (e.g. "shop,default" from composite sessions)
// by using the first tenant in the list.
func (ts *TenantStore) resolveTenant(r *http.Request) *TenantInstance {
	tenantID := tenantIDFromRequest(r)

	ts.mu.RLock()
	inst := ts.tenants[tenantID]
	ts.mu.RUnlock()
	if inst != nil && inst.removing.Load() {
		return nil
	}
	return inst
}

// resolveTenantAndLock resolves the tenant while holding ts.mu.RLock, and returns
// the instance together with an unlock func. The caller MUST invoke unlock() after
// finishing use of the instance (typically after inst.Router.ServeHTTP completes).
// This closes the TOCTOU window between lookup and use: ReloadTenant/RemoveTenant
// write inst.Router/inst.Config under ts.mu.Lock, so holding the RLock pins the
// instance until the caller releases it.
func (ts *TenantStore) resolveTenantAndLock(r *http.Request) (*TenantInstance, func()) {
	tenantID := tenantIDFromRequest(r)

	ts.mu.RLock()
	inst := ts.tenants[tenantID]
	if inst == nil || inst.removing.Load() {
		ts.mu.RUnlock()
		return nil, func() {}
	}
	return inst, ts.mu.RUnlock
}

// tenantIDFromRequest extracts the tenant identifier from context, header, or query.
func tenantIDFromRequest(r *http.Request) string {
	// 1. Try context (populated by TenantIDMiddleware when present)
	tenantID, _ := r.Context().Value(tenantIDKey).(string)

	// 2. Fallback: direct header read (for tests / when middleware not applied)
	if tenantID == "" {
		tenantID = r.Header.Get("X-Tenant-ID")
	}

	// 3. Fallback to query parameter ?tenant=... (critical for Swagger UI / Browser)
	if tenantID == "" {
		tenantID = r.URL.Query().Get("tenant")
	}

	// Handle comma-separated tenant IDs (e.g. "shop,default" from composite MCP sessions)
	// Use the first tenant only for routing to data-service
	if tenantID != "" && strings.Contains(tenantID, ",") {
		parts := strings.Split(tenantID, ",")
		tenantID = strings.TrimSpace(parts[0])
	}
	return tenantID
}
