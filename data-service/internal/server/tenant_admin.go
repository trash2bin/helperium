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
	r := chi.NewRouter()

	// All admin endpoints require ADMIN_TOKEN
	r.Use(AdminAuthMiddleware)
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

	// Per-tenant tool management: read-only approval flow
	r.Get("/tenants/{id}/tools/pending", ts.adminTenantPendingToolsHandler)
	r.Post("/tenants/{id}/tools/{toolName}/approve", ts.adminTenantApproveToolHandler)

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
		inst.ConfigPath = persistedPath
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
	resp := make([]tenantResponse, len(instances))
	for i, inst := range instances {
		resp[i] = tenantResponseFromInstance(inst)
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
	handlers.RespondJSON(w, http.StatusOK, tenantResponseFromInstance(inst))
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
	_, err := NewRouterFromConfig(ts, &newCfg, inst.AdapterSub, inst.AdapterSub, inst.Adapter, targetPath, nil, nil)
	if err != nil {
		handlers.RespondError(w, http.StatusBadRequest, "build_error",
			fmt.Sprintf("router build failed: %v", err))
		return
	}

	// Persist via TenantStore (always writes to TenantsDir)
	persistedPath := ts.SaveTenantConfig(inst.ID, &newCfg)
	if persistedPath != "" {
		inst.ConfigPath = persistedPath
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

	if err := ts.ReloadTenant(ctx, inst.ID, ""); err != nil {
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

		// Cache schema for /mcp/schema endpoint
		inst.IntrospectedSchema = schema

		// Cache schema for /mcp/schema endpoint
		inst.IntrospectedSchema = schema

		genCfg := &config.Config{
			DataSource:          inst.Config.DataSource,
			SkipRules:           inst.Config.SkipRules,
			DisplayPrefixes:     inst.Config.DisplayPrefixes,
			CustomPlurals:       inst.Config.CustomPlurals,
			DisabledDefaultRules: inst.Config.DisabledDefaultRules,
		}
		newCfg := configgen.Generate(schema, genCfg)

		// Preserve custom configgen fields on the generated config
		newCfg.SkipRules = genCfg.SkipRules
		newCfg.DisplayPrefixes = genCfg.DisplayPrefixes
		newCfg.CustomPlurals = genCfg.CustomPlurals
		newCfg.DisabledDefaultRules = genCfg.DisabledDefaultRules
		newCfg.ApprovedTools = inst.Config.ApprovedTools

		// Save tenant config to canonical location (TenantsDir/{id}.json)
		persistedPath := ts.SaveTenantConfig(inst.ID, newCfg)
		if persistedPath == "" {
			handlers.RespondError(w, http.StatusInternalServerError, "persist_error",
				"failed to persist tenant config (TenantsDir not configured)")
			return
		}
		// Update instance config path so future writes go to the right file
		inst.ConfigPath = persistedPath

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

		genCfg := &config.Config{
			DataSource:          inst.Config.DataSource,
			SkipRules:           inst.Config.SkipRules,
			DisplayPrefixes:     inst.Config.DisplayPrefixes,
			CustomPlurals:       inst.Config.CustomPlurals,
			DisabledDefaultRules: inst.Config.DisabledDefaultRules,
		}
		cfg := configgen.Generate(schema, genCfg)

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
		Version:        cfg.Version,
		Driver:         cfg.DataSource.Driver,
		DataSource:     responseFromDataSource(cfg.DataSource),
		Entities:       cfg.Entities,
		Endpoints:      cfg.Endpoints,
		CustomQueries:  cfg.CustomQueries,
		Stats:          cfg.Stats,
		Auth:           cfg.Auth,
		MCPTools:       cfg.MCPTools,
		Introspection:  cfg.Introspection,
		SkipRules:      cfg.SkipRules,
		DisplayPrefixes: cfg.DisplayPrefixes,
		CustomPlurals:  cfg.CustomPlurals,
		ApprovedTools:  cfg.ApprovedTools,
		DisabledDefaultRules: cfg.DisabledDefaultRules,
	}
}

// SetHasAdmin sets whether an introspect adapter is available (for /openapi.json).
func (ts *TenantStore) SetHasAdmin(hasAdmin bool) {
	ts.mu.Lock()
	ts.hasAdmin = hasAdmin
	ts.mu.Unlock()
}

// ── Per-Tenant Tool Approval Handlers ──

// adminTenantPendingToolsHandler returns pending write tools for a specific tenant.
// GET /admin/tenants/{id}/tools/pending
func (ts *TenantStore) adminTenantPendingToolsHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	inst, ok := ts.GetTenant(id)
	if !ok {
		handlers.RespondError(w, http.StatusNotFound, "not_found",
			fmt.Sprintf("tenant %q not found", id))
		return
	}

	approvedTools := inst.ApprovedTools
	if approvedTools == nil {
		approvedTools = make(map[string]bool)
	}

	adminPendingToolsHandler(inst.Config, approvedTools).ServeHTTP(w, r)
}

// adminTenantApproveToolHandler approves a write tool for a specific tenant.
// POST /admin/tenants/{id}/tools/{toolName}/approve
func (ts *TenantStore) adminTenantApproveToolHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	inst, ok := ts.GetTenant(id)
	if !ok {
		handlers.RespondError(w, http.StatusNotFound, "not_found",
			fmt.Sprintf("tenant %q not found", id))
		return
	}

	// Create a persist function that saves the approved tools back to config
	persistFn := func() error {
		// Sync ApprovedTools map back to Config.ApprovedTools list
		paths := make([]string, 0, len(inst.ApprovedTools))
		for p := range inst.ApprovedTools {
			paths = append(paths, p)
		}
		inst.Config.ApprovedTools = paths

		// Persist to disk via SaveTenantConfig
		savedPath := ts.SaveTenantConfig(id, inst.Config)
		if savedPath == "" {
			return fmt.Errorf("failed to save tenant config for %q", id)
		}
		inst.ConfigPath = savedPath

		// Rebuild router with updated approved tools
		approvedTools := make(map[string]bool)
		for _, p := range inst.Config.ApprovedTools {
			approvedTools[p] = true
		}
		newRouter, err := NewRouterFromConfig(ts, inst.Config, inst.AdapterSub, inst.AdapterSub, inst.Adapter, inst.ConfigPath, nil, approvedTools)
		if err != nil {
			return fmt.Errorf("rebuild router after approval: %w", err)
		}
		inst.Router = newRouter
		inst.ApprovedTools = approvedTools

		return nil
	}

	adminApproveToolHandler(inst.Config, inst.ApprovedTools, persistFn).ServeHTTP(w, r)
}
