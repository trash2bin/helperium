package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/configgen"
	"github.com/agent-tutor/data-service/internal/datasource"
	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
)

// NewRouterFromConfig создаёт chi-роутер на основе конфигурации.
//
// Если introspectAdapter не nil — регистрируются /admin/* endpoint'ы.
// configPath — путь к файлу конфига (для /admin/config/rewrite).
// adminCtx — опциональный контекст для admin API (hot reload и версионирование).
// Если nil — admin endpoints не регистрируются.
//
// Если cfg.DataSource.ReadOnly == true (по умолчанию), любые мутирующие HTTP-методы
// (POST, PUT, PATCH, DELETE) не регистрируются — агент может только читать данные.
func NewRouterFromConfig(ts *TenantStore, cfg *config.Config, db runtime.AdapterSubset, adapter runtime.AdapterSubset, introspectAdapter datasource.Adapter, configPath string, adminCtx *AdminContext, approvedTools map[string]bool) (http.Handler, error) {
	entities := runtime.ConfigToEntities(cfg.Entities)
	customQueries := runtime.ConfigToCustomQueries(cfg.CustomQueries)

	resolver, err := runtime.NewEntityResolver(entities)
	if err != nil {
		return nil, fmt.Errorf("config router: entity resolver: %w", err)
	}

	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:            db,
		Adapter:       adapter,
		Builder:       builder,
		Resolver:      resolver,
		CustomQueries: customQueries,
		URLParam:      chi.URLParam,
		Auth:          cfg.Auth,
		TenantIDFunc:  tenantIDFromContext,
	}

	r := chi.NewRouter()
	r.Use(RecoveryMiddleware)
	r.Use(RequestIDMiddleware)
	r.Use(StructuredLoggingMiddleware)
	r.Use(chimw.RealIP)
	r.Use(chimw.Timeout(time.Duration(ResolveRequestTimeout(cfg)) * time.Second))

	// Multi-tenancy: X-Tenant-ID middleware (если auth настроен)
	if cfg.Auth != nil && cfg.Auth.Strategy == config.AuthStrategyHeader {
		tenantHeader := cfg.Auth.TenantHeader
		if tenantHeader == "" {
			tenantHeader = "X-Tenant-ID"
		}
		r.Use(TenantIDMiddleware(tenantHeader))
	}

	// Системные эндпоинты (всегда)
	r.Get("/docs", SwaggerHandler)
	r.Get("/openapi.json", NewOpenAPIHandler(ts, introspectAdapter != nil))

	// MCP-манифест — единственный source of truth для mcp-gateway
	r.Get("/mcp/manifest", handlers.MCPManifestHandler(cfg))

	// /admin/* — admin endpoints (protect ADMIN_TOKEN, фаза 3.7)
	if introspectAdapter != nil && adminCtx != nil {
		r.Route("/admin", func(r chi.Router) {
			r.Use(AdminAuthMiddleware)
			r.Use(AdminRateLimitMiddleware())
			r.Get("/config", adminConfigHandler(cfg))
			r.Post("/config", adminConfigUpdateHandler(adminCtx))
			r.Post("/config/reload", adminConfigReloadHandler(adminCtx))
			r.Get("/config/versions", adminConfigVersionsHandler(adminCtx))
			r.Post("/config/rewrite", adminRewriteHandler(introspectAdapter, cfg.DataSource, configPath, adminCtx))
			r.Get("/discover", discoverHandler(introspectAdapter, cfg.DataSource))
			// Tool management: read-only approval flow (legacy — for backward compat)
			// Per-tenant approval is handled via /admin/tenants/{id}/tools/* in BuildAdminRouter
			r.Get("/tools/pending", adminPendingToolsHandler(cfg, approvedTools))
			r.Post("/tools/{toolName}/approve", adminApproveToolHandler(cfg, approvedTools, nil))
		})
	}

	// Read-only guard: если DataSource.ReadOnly == true (по умолчанию),
	// мутирующие методы (POST, PUT, PATCH, DELETE) не регистрируются,
	// за исключением эндпоинтов, явно подтверждённых через admin API
	// (POST /admin/tools/{toolName}/approve).
	readOnly := false
	if cfg.DataSource.ReadOnly != nil && *cfg.DataSource.ReadOnly {
		readOnly = true
		if approvedTools == nil {
			approvedTools = make(map[string]bool)
		}
		slog.Info("read-only mode enabled — write methods are blocked by default",
			"endpoints", len(cfg.Endpoints),
			"approved_tools", len(approvedTools))
	}

	for _, ep := range cfg.Endpoints {
		// Read-only guard: пропускаем write-методы, если они не утверждены
		if readOnly && isWriteMethod(ep.Method) {
			if approvedTools[ep.Path] {
				slog.Debug("approved write endpoint allowed in read-only mode",
					"method", ep.Method, "path", ep.Path, "op", ep.Op)
			} else {
				slog.Debug("skipping write endpoint in read-only mode",
					"method", ep.Method, "path", ep.Path, "op", ep.Op)
				continue
			}
		}

		var h http.HandlerFunc

		switch ep.Op {
		case "builtin_health":
			h = handlers.HealthHandler(ctx)
		case "builtin_stats":
			h = handlers.StatsHandler(ctx, cfg)
		case "get_by_id":
			if ep.Entity == "" {
				return nil, fmt.Errorf("endpoint %q: op get_by_id requires entity", ep.Path)
			}
			h = handlers.GetByIDHandler(ctx, ep.Entity)
		case "find":
			if ep.Entity == "" {
				return nil, fmt.Errorf("endpoint %q: op find requires entity", ep.Path)
			}
			h = handlers.FindHandler(ctx, ep.Entity, ep.SearchField, ep.QueryParam)
		case "list":
			if ep.Entity == "" {
				return nil, fmt.Errorf("endpoint %q: op list requires entity", ep.Path)
			}
			h = handlers.ListHandler(ctx, ep.Entity)
		case "custom_query":
			if ep.QueryID == "" {
				return nil, fmt.Errorf("endpoint %q: op custom_query requires query_id", ep.Path)
			}
			params := make([]config.EndpointParam, 0, len(ep.Params))
			params = append(params, ep.Params...)
			h = handlers.CustomQueryHandler(ctx, ep.QueryID, params)
		default:
			return nil, fmt.Errorf("endpoint %q: unsupported op %q", ep.Path, ep.Op)
		}

		switch ep.Method {
		case "GET":
			r.Get(ep.Path, h)
		case "POST":
			r.Post(ep.Path, h)
		case "PUT":
			r.Put(ep.Path, h)
		case "PATCH":
			r.Patch(ep.Path, h)
		case "DELETE":
			r.Delete(ep.Path, h)
		default:
			return nil, fmt.Errorf("endpoint %q: unsupported method %q", ep.Path, ep.Method)
		}
	}

	r.NotFound(handlers.NotFoundHandler)
	r.MethodNotAllowed(handlers.MethodNotAllowedHandler)

	return r, nil
}

// discoverHandler — GET /admin/discover: сгенерировать конфиг из схемы БД.
func discoverHandler(adp datasource.Adapter, ds config.DataSourceConfig) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		conn, err := adp.Connect(r.Context(), ds.DSN)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "connect_error", err.Error())
			return
		}
		defer conn.Close() //nolint:errcheck

		schema, err := adp.Introspect(r.Context(), conn)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "introspect_error", err.Error())
			return
		}

		dsConfig := config.DataSourceConfig{Driver: ds.Driver, DSN: ds.DSN}
		cfg := configgen.Generate(schema, dsConfig, nil)

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

// isWriteMethod возвращает true для мутирующих HTTP-методов.
func isWriteMethod(method config.HTTPMethod) bool {
	switch method {
	case config.MethodPOST, config.MethodPUT, config.MethodPATCH, config.MethodDELETE:
		return true
	}
	return false
}

// tenantIDFromContext — реализация handlers.Context.TenantIDFunc.
// Извлекает tenant_id из контекста HTTP-запроса, помещённый TenantIDMiddleware.
func tenantIDFromContext(r *http.Request) string {
	v, _ := r.Context().Value(tenantIDKey).(string)
	return v
}
