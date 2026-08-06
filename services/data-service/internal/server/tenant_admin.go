// ── Admin Router ──

package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/trash2bin/helperium/data-service/internal/configgen"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Admin Router ──

// BuildAdminRouter creates the chi sub-router for /admin/* endpoints.
func (ts *TenantStore) BuildAdminRouter(adapter datasource.Adapter, configPath string, adminCtx *AdminContext, cfg *config.Config) http.Handler {
	// H-3: единый роутер админки. Два пути записи конфига на диск:
	//   1. POST /admin/config (PUT-style)  — point-fix: пишет newCfg как есть (SaveTenantConfig).
	//      Оператор может править Entities/Endpoints вручную — регенерация не выполняется.
	//   2. POST /admin/config/rewrite      — регенерация: ExtractIntent → Hydrate(schema),
	//      intent — единственный источник правды, Entities/Endpoints/MCPTools пересобираются.
	//      Любые point-фиксы, внесённые через PUT, НЕ переживают rewrite —
	//      их надо повторять после rewrite (или хранить в intent-совместимых полях).
	r := chi.NewRouter()

	// All admin endpoints require ADMIN_TOKEN
	r.Use(AdminAuthMiddleware)
	// Ограничиваем размер тела (POST /tenants, /config, /config/rewrite читают
	// тело в память целиком). До фикса middleware был определён, но не подключён →
	// OOM-риск на неограниченном теле. Лимит из cfg.Server.BodyLimitMB / env DS_BODY_LIMIT_MB.
	r.Use(BodyLimitMiddleware(ResolveBodyLimit(cfg)))
	r.Use(AdminRateLimitMiddleware())

	// Tenant management
	r.Post("/tenants", ts.adminAddTenantHandler)
	r.Get("/tenants", ts.adminListTenantsHandler)
	r.Get("/tenants/{id}", ts.adminGetTenantHandler)
	r.Delete("/tenants/{id}", ts.adminRemoveTenantHandler)

	// Config management (operates on current tenant)
	r.Get("/config", ts.adminConfigHandler)
	r.Post("/config", ts.adminConfigUpdateHandler)
	r.Post("/config/reload", ts.adminConfigReloadHandler)
	r.Get("/config/versions", ts.adminConfigVersionsHandler)
	r.Post("/config/rewrite", ts.adminRewriteHandler(adapter, configPath))

	// Schema discovery (operates on current tenant)
	if adapter != nil {
		r.Get("/discover", ts.adminDiscoverHandler(adapter))
	}

	ts.adminRouter = r
	return r
}

// ── Admin Tenant Management Handlers ──

type addTenantRequest struct {
	ID         string          `json:"id"`
	Config     json.RawMessage `json:"config"`
	ConfigPath string          `json:"config_path,omitempty"`
}

type tenantResponse struct {
	ID        string `json:"id"`
	Driver    string `json:"driver"`
	Entities  int    `json:"entities"`
	Endpoints int    `json:"endpoints"`
	Healthy   bool   `json:"healthy"`
	Error     string `json:"error,omitempty"`
	CreatedAt string `json:"created_at"`
}

func tenantResponseFromInstance(inst *TenantInstance) tenantResponse {
	inst.healthMu.Lock()
	healthy := inst.Healthy
	lastErr := inst.LastError
	inst.healthMu.Unlock()

	return tenantResponse{
		ID:        inst.ID,
		Driver:    string(inst.Config.DataSource.Driver),
		Entities:  len(inst.Config.Entities),
		Endpoints: len(inst.Config.Endpoints),
		Healthy:   healthy,
		Error:     lastErr,
		CreatedAt: inst.CreatedAt.UTC().Format(time.RFC3339),
	}
}

// snapshotTenantResponse builds a tenantResponse for the given id while holding
// ts.mu.RLock for the whole read, so a concurrent ReloadTenant (which swaps
// inst.Config under ts.mu.Lock) cannot race with the Config-field reads.
func (ts *TenantStore) snapshotTenantResponse(id string) (tenantResponse, bool) {
	ts.mu.RLock()
	inst, ok := ts.tenants[id]
	if !ok || inst.Config == nil {
		ts.mu.RUnlock()
		return tenantResponse{}, false
	}
	driver := string(inst.Config.DataSource.Driver)
	entities := len(inst.Config.Entities)
	endpoints := len(inst.Config.Endpoints)
	ts.mu.RUnlock()

	inst.healthMu.Lock()
	healthy := inst.Healthy
	lastErr := inst.LastError
	inst.healthMu.Unlock()

	return tenantResponse{
		ID:        inst.ID,
		Driver:    driver,
		Entities:  entities,
		Endpoints: endpoints,
		Healthy:   healthy,
		Error:     lastErr,
		CreatedAt: inst.CreatedAt.UTC().Format(time.RFC3339),
	}, true
}

func (ts *TenantStore) adminAddTenantHandler(w http.ResponseWriter, r *http.Request) {
	var req addTenantRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "invalid_json",
			fmt.Sprintf("failed to parse body: %v", err))
		return
	}

	if req.ID == "" {
		handlers.RespondError(w, http.StatusBadRequest, "missing_id", "id is required")
		return
	}

	// Parse config (decode does basic type validation)
	var cfg config.Config
	if err := json.Unmarshal(req.Config, &cfg); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "invalid_config",
			fmt.Sprintf("failed to decode config: %v", err))
		return
	}

	// Validate config via Go types
	if err := config.Validate(req.Config); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "validation_error", err.Error())
		return
	}

	// Add tenant first (no config file yet — will persist after)
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	inst, err := ts.AddTenant(ctx, req.ID, &cfg, "")
	if err != nil {
		if _, exists := ts.GetTenant(req.ID); exists {
			handlers.RespondError(w, http.StatusConflict, "duplicate", err.Error())
		} else {
			handlers.RespondError(w, http.StatusInternalServerError, "add_failed", err.Error())
		}
		return
	}

	// Persist config to TenantsDir (canonical location)
	persistedPath := ts.SaveTenantConfig(req.ID, &cfg)
	if persistedPath != "" {
		ts.mu.Lock()
		inst.ConfigPath = persistedPath
		ts.mu.Unlock()
	}

	handlers.RespondJSON(w, http.StatusCreated, map[string]any{
		"status": "created",
		"tenant": tenantResponseFromInstance(inst),
	})
}

func (ts *TenantStore) adminRemoveTenantHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if id == "" {
		handlers.RespondError(w, http.StatusBadRequest, "missing_id", "tenant id is required")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	if err := ts.RemoveTenant(ctx, id); err != nil {
		handlers.RespondError(w, http.StatusNotFound, "not_found", err.Error())
		return
	}

	// Remove persisted config file
	if ts.TenantsDir != "" {
		configPath := filepath.Join(ts.TenantsDir, id+".json")
		if err := os.Remove(configPath); err != nil && !os.IsNotExist(err) {
			slog.Warn("failed to remove tenant config file", "tenant", id, "error", err)
		}
	}

	handlers.RespondJSON(w, http.StatusOK, map[string]string{
		"status": "removed",
		"id":     id,
	})
}

func (ts *TenantStore) adminListTenantsHandler(w http.ResponseWriter, r *http.Request) {
	instances := ts.ListTenants()
	resp := make([]tenantResponse, 0, len(instances))
	for _, inst := range instances {
		if tr, ok := ts.snapshotTenantResponse(inst.ID); ok {
			resp = append(resp, tr)
		}
	}
	handlers.RespondJSON(w, http.StatusOK, map[string]any{"tenants": resp})
}

func (ts *TenantStore) adminGetTenantHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	inst, ok := ts.GetTenant(id)
	if !ok {
		handlers.RespondError(w, http.StatusNotFound, "not_found",
			fmt.Sprintf("tenant %q not found", id))
		return
	}
	tr, ok := ts.snapshotTenantResponse(inst.ID)
	if !ok {
		handlers.RespondError(w, http.StatusNotFound, "not_found",
			fmt.Sprintf("tenant %q not found", id))
		return
	}
	handlers.RespondJSON(w, http.StatusOK, tr)
}

func (ts *TenantStore) adminConfigHandler(w http.ResponseWriter, r *http.Request) {
	inst := ts.resolveTenant(r)
	if inst == nil {
		handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
			"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
		return
	}

	resp := adminConfigResponseFromConfig(inst.Config)
	handlers.RespondJSON(w, http.StatusOK, resp)
}

func (ts *TenantStore) adminConfigUpdateHandler(w http.ResponseWriter, r *http.Request) {
	inst := ts.resolveTenant(r)
	if inst == nil {
		handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
			"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
		return
	}

	// Parse body
	var raw json.RawMessage
	if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "invalid_json",
			fmt.Sprintf("failed to parse body: %v", err))
		return
	}

	var newCfg config.Config
	if err := json.Unmarshal(raw, &newCfg); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "invalid_config",
			fmt.Sprintf("failed to unmarshal config: %v", err))
		return
	}

	// Merge DSN from stored config if incoming doesn't have it
	// (GET /admin/config redacts DSN for security — PUT should preserve it)
	if newCfg.DataSource.DSN == "" && inst.Config.DataSource.DSN != "" {
		newCfg.DataSource.DSN = inst.Config.DataSource.DSN
		// Re-marshal raw with merged DSN for Validate()
		merged, _ := json.Marshal(newCfg)
		raw = json.RawMessage(merged)
	}

	// Merge ReadonlyDSN так же: если входящий конфиг не содержит readonly_dsn
	// (GET-ответ мог его не отдать или админ не менял), сохраняем прежний.
	// Без этого PUT с любым телом стирал readonly_dsn (тихая потеря read-only пула).
	if newCfg.DataSource.ReadonlyDSN == "" && inst.Config.DataSource.ReadonlyDSN != "" {
		newCfg.DataSource.ReadonlyDSN = inst.Config.DataSource.ReadonlyDSN
		merged, _ := json.Marshal(newCfg)
		raw = json.RawMessage(merged)
	}

	// Validate via Go types
	if err := config.Validate(raw); err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "validation_error", err.Error())
		return
	}

	// Dry-run build
	targetPath := inst.ConfigPath
	if targetPath == "" {
		targetPath = ts.TenantConfigPath(inst.ID)
	}
	_, err := NewRouterFromConfig(ts, &newCfg, inst.AdapterSub)
	if err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "build_error",
			fmt.Sprintf("router build failed: %v", err))
		return
	}

	// Архив текущей версии перед перезаписью — иначе /admin/config/versions
	// всегда пуст (архивирует только мёртвый пакетный admin.go handler).
	// Путь берём из inst.ConfigPath или канонического пути тенанта.
	currentPath := inst.ConfigPath
	if currentPath == "" {
		currentPath = ts.TenantConfigPath(inst.ID)
	}
	if currentPath != "" {
		if _, err := os.Stat(currentPath); err == nil {
			if err := archiveCurrentConfig(currentPath); err != nil {
				slog.Warn("admin config: archive before update failed", "tenant", inst.ID, "error", err)
			}
		}
	}

	// Persist via TenantStore (always writes to TenantsDir)
	persistedPath := ts.SaveTenantConfig(inst.ID, &newCfg)
	if persistedPath != "" {
		ts.mu.Lock()
		inst.ConfigPath = persistedPath
		ts.mu.Unlock()
	}

	// Reload tenant
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	reloadPath := inst.ConfigPath
	if reloadPath == "" {
		reloadPath = targetPath
	}
	if err := ts.ReloadTenant(ctx, inst.ID, reloadPath); err != nil {
		handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
			fmt.Sprintf("config saved but reload failed: %v", err))
		return
	}

	handlers.RespondJSON(w, http.StatusOK, map[string]any{
		"status":    "applied",
		"path":      reloadPath,
		"entities":  len(newCfg.Entities),
		"endpoints": len(newCfg.Endpoints),
	})
}

func (ts *TenantStore) adminConfigReloadHandler(w http.ResponseWriter, r *http.Request) {
	inst := ts.resolveTenant(r)
	if inst == nil {
		handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
			"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	// ReloadTenant требует валидный путь к файлу конфига. Если inst.ConfigPath
	// ещё не задан (тенант добавлен с пустым configPath) — берём канонический
	// путь из TenantsDir. Раньше сюда передавалась пустая строка, и каждый
	// reload падал с 500 (load "": no such file).
	configPath := inst.ConfigPath
	if configPath == "" {
		configPath = ts.TenantConfigPath(inst.ID)
	}
	if configPath == "" {
		handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
			"tenant config path is not configured")
		return
	}

	if err := ts.ReloadTenant(ctx, inst.ID, configPath); err != nil {
		handlers.RespondError(w, http.StatusInternalServerError, "reload_error", err.Error())
		return
	}
	handlers.RespondJSON(w, http.StatusOK, map[string]string{
		"status": "reloaded",
	})
}

func (ts *TenantStore) adminConfigVersionsHandler(w http.ResponseWriter, r *http.Request) {
	inst := ts.resolveTenant(r)
	if inst == nil {
		handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
			"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
		return
	}
	versionsDir := filepath.Join(filepath.Dir(inst.ConfigPath), "config_versions")
	entries, err := os.ReadDir(versionsDir)
	if err != nil {
		if os.IsNotExist(err) {
			handlers.RespondJSON(w, http.StatusOK, []string{})
			return
		}
		handlers.RespondError(w, http.StatusInternalServerError, "readdir_error", err.Error())
		return
	}

	type versionInfo struct {
		Name    string `json:"name"`
		Size    int64  `json:"size_bytes"`
		ModTime string `json:"mod_time"`
	}
	versions := make([]versionInfo, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() || !strings.HasPrefix(e.Name(), "config.") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		versions = append(versions, versionInfo{
			Name:    e.Name(),
			Size:    info.Size(),
			ModTime: info.ModTime().UTC().Format(time.RFC3339),
		})
	}
	sort.Slice(versions, func(i, j int) bool { return versions[i].Name > versions[j].Name })
	handlers.RespondJSON(w, http.StatusOK, versions)
}

// logEndpointCustomizationsDropped логирует endpoint-level кастомизации
// (Description/Params), которые rewrite перезапишет авто-версиями из Hydrate.
// Intent не несёт endpoint-level правок — это осознанный tradeoff (M-1), но
// потеря должна быть видна в логах. Сравниваем описание до/после регенерации.
func logEndpointCustomizationsDropped(tenantID string, old []config.Endpoint, schema *datasource.Schema) {
	// Старые описания по path.
	oldDesc := make(map[string]string, len(old))
	oldParams := make(map[string]int, len(old))
	for _, ep := range old {
		oldDesc[ep.Path] = ep.Description
		oldParams[ep.Path] = len(ep.Params)
	}

	// Новые описания из intent (без интроспекции — используем текущие entities
	// конфига, которые Hydrate перегенерирует из intent+схемы). Приближение:
	// для rewrite мы не можем дёшево сгенерить новые endpoints здесь (это делает
	// Hydrate в adminRewriteHandler), поэтому сигнализируем только фактом
	// ручных правок в старом конфиге, которые будут перезаписаны.
	// Полная сверка возможна после Hydrate в caller'е.
	_ = schema
	manual := 0
	for path, desc := range oldDesc {
		if desc != "" {
			manual++
			slog.Info("rewrite: endpoint has custom description, will be regenerated from intent",
				"tenant", tenantID, "path", path)
		}
	}
	_ = oldParams
	if manual > 0 {
		slog.Warn("rewrite: N endpoint descriptions will be overwritten by auto-generated ones",
			"tenant", tenantID, "count", manual)
	}
}

func (ts *TenantStore) adminRewriteHandler(_ datasource.Adapter, _ string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		inst := ts.resolveTenant(r)
		if inst == nil {
			handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
				"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
			return
		}

		// Resolve the correct adapter for this tenant's driver (SQLite, PostgreSQL, etc.)
		adapter, ok := ts.registry.Get(string(inst.Config.DataSource.Driver))
		if !ok || adapter == nil {
			handlers.RespondError(w, http.StatusServiceUnavailable, "unavailable",
				fmt.Sprintf("adapter not available for driver %q", inst.Config.DataSource.Driver))
			return
		}

		conn, err := adapter.Connect(r.Context(), inst.Config.DataSource.DSN)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "connect_error", err.Error())
			return
		}
		defer conn.Close() //nolint:errcheck

		schema, err := adapter.Introspect(r.Context(), conn)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "introspect_error", err.Error())
			return
		}

		// Cache schema for /mcp/schema endpoint + persist для RegenerateAndPersistTenantConfig.
		// Запись под schemaMu — /mcp/schema читает это поле из другого goroutine.
		inst.schemaMu.Lock()
		inst.IntrospectedSchema = schema
		inst.schemaMu.Unlock()
		ts.SaveTenantSchema(inst.ID, schema)

		// M-1: rewrite пересобирает эндпоинты из intent — ручные endpoint-level
		// правки (Description, Params) не входят в intent и будут стёрты.
		// Это осознанный tradeoff (intent не несёт endpoint-level кастомизаций),
		// но фиксируем в лог, чтобы потерю было видно.
		logEndpointCustomizationsDropped(inst.ID, inst.Config.Endpoints, schema)

		// Пересборка из intent (единственного источника правды) + свежей схемы.
		// ExtractIntent вытаскивает все кастомизации (FieldRules, CustomShortNames,
		// explicit CustomQueries и т.д.), Hydrate генерирует Entities/Endpoints/MCPTools
		// и возвращает их обратно. Так rewrite не теряет настройки.
		newCfg := configgen.Hydrate(configgen.ExtractIntent(inst.Config), schema)

		// Валидируем ДО записи на диск: если регенерация дала невалидный конфиг
		// (напр. Stats.Counter ссылается на удалённую сущность), отдаём 400 и
		// НЕ трогаем файл — иначе получим half-applied состояние (файл новый,
		// а reload/restart подхватит его и тенант умрёт).
		if err := newCfg.Validate(); err != nil {
			handlers.RespondError(w, http.StatusBadRequest, "invalid_generated_config",
				fmt.Sprintf("rewrite produced invalid config: %v", err))
			return
		}

		// Save tenant config to canonical location (TenantsDir/{id}.json)
		persistedPath := ts.SaveTenantConfig(inst.ID, newCfg)
		if persistedPath == "" {
			handlers.RespondError(w, http.StatusInternalServerError, "persist_error",
				"failed to persist tenant config (TenantsDir not configured)")
			return
		}
		// Update instance config path so future writes go to the right file
		ts.mu.Lock()
		inst.ConfigPath = persistedPath
		ts.mu.Unlock()

		// Reload
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		if err := ts.ReloadTenant(ctx, inst.ID, persistedPath); err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
				fmt.Sprintf("config saved but reload failed: %v", err))
			return
		}

		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"status":    "ok",
			"path":      persistedPath,
			"entities":  len(newCfg.Entities),
			"endpoints": len(newCfg.Endpoints),
			"note":      "Конфиг сохранён и применён без рестарта.",
		})
	}
}

func (ts *TenantStore) adminDiscoverHandler(_ datasource.Adapter) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		inst := ts.resolveTenant(r)
		if inst == nil {
			handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
				"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
			return
		}

		// Resolve the correct adapter for this tenant's driver (SQLite, PostgreSQL, etc.)
		adapter, ok := ts.registry.Get(string(inst.Config.DataSource.Driver))
		if !ok || adapter == nil {
			handlers.RespondError(w, http.StatusServiceUnavailable, "unavailable",
				fmt.Sprintf("adapter not available for driver %q", inst.Config.DataSource.Driver))
			return
		}

		conn, err := adapter.Connect(r.Context(), inst.Config.DataSource.DSN)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "connect_error", err.Error())
			return
		}
		defer conn.Close() //nolint:errcheck

		schema, err := adapter.Introspect(r.Context(), conn)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "introspect_error", err.Error())
			return
		}

		// L-4: discover — read-only, но кэшируем свежую схему, чтобы
		// RegenerateAndPersistTenantConfig не регенерировал из устаревшего кэша.
		ts.SaveTenantSchema(inst.ID, schema)

		cfg := configgen.Hydrate(configgen.ExtractIntent(inst.Config), schema)

		slog.Info("config generated via /admin/discover",
			"entities", len(cfg.Entities),
			"endpoints", len(cfg.Endpoints),
		)

		if r.URL.Query().Get("raw") == "true" {
			data, err := json.MarshalIndent(cfg, "", "  ")
			if err != nil {
				handlers.RespondError(w, http.StatusInternalServerError, "marshal_error", err.Error())
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write(data)
			return
		}

		handlers.RespondJSON(w, http.StatusOK, cfg)
	}
}

// adminConfigResponseFromConfig converts config.Config to admin-safe DTO.
func adminConfigResponseFromConfig(cfg *config.Config) adminConfigResponse {
	return adminConfigResponse{
		Version:                        cfg.Version,
		Driver:                         cfg.DataSource.Driver,
		DataSource:                     responseFromDataSource(cfg.DataSource),
		Entities:                       cfg.Entities,
		Endpoints:                      cfg.Endpoints,
		CustomQueries:                  cfg.CustomQueries,
		Stats:                          cfg.Stats,
		Auth:                           cfg.Auth,
		MCPTools:                       cfg.MCPTools,
		Introspection:                  cfg.Introspection,
		SkipRules:                      cfg.SkipRules,
		DisplayPrefixes:                cfg.DisplayPrefixes,
		CustomPlurals:                  cfg.CustomPlurals,
		DisabledDefaultRules:           cfg.DisabledDefaultRules,
		FilterableRules:                cfg.FilterableRules,
		SearchableRules:                cfg.SearchableRules,
		EnumRules:                      cfg.EnumRules,
		DisabledDefaultFilterableRules: cfg.DisabledDefaultFilterableRules,
		DisabledDefaultSearchableRules: cfg.DisabledDefaultSearchableRules,
		DisabledDefaultEnumRules:       cfg.DisabledDefaultEnumRules,
		CustomShortNames:               cfg.CustomShortNames,
	}
}

// SetHasAdmin sets whether an introspect adapter is available (for /openapi.json).
func (ts *TenantStore) SetHasAdmin(hasAdmin bool) {
	ts.mu.Lock()
	ts.hasAdmin = hasAdmin
	ts.mu.Unlock()
}
