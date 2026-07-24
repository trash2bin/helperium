package server

import (
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/configgen"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// NewRouterFromConfig creates a chi router from a tenant config.
//
// Parameters:
//   - ts: TenantStore for resolving tenant state (mcp/schema, openapi.json)
//   - cfg: the tenant's config
//   - adapter: AdapterSubset for query builder and handlers (includes metrics/logging)
//   - approvedTools: map of write endpoint paths that are approved in read-only mode
func NewRouterFromConfig(ts *TenantStore, cfg *config.Config, adapter runtime.AdapterSubset, approvedTools map[string]bool) (http.Handler, error) {
	entities := runtime.ConfigToEntities(cfg.Entities)
	customQueries := runtime.ConfigToCustomQueries(cfg.CustomQueries)

	resolver, err := runtime.NewEntityResolver(entities)
	if err != nil {
		return nil, fmt.Errorf("config router: entity resolver: %w", err)
	}

	builder := runtime.NewBuilder(adapter)

	// Per-query timeout: default 30s, overridable via env QUERY_TIMEOUT_SECONDS
	queryTimeout := 30 * time.Second
	if envTO := os.Getenv("QUERY_TIMEOUT_SECONDS"); envTO != "" {
		if t, err := strconv.Atoi(envTO); err == nil && t > 0 {
			queryTimeout = time.Duration(t) * time.Second
		}
	}

	ctx := &handlers.Context{
		DB:            adapter,
		Adapter:       adapter,
		Builder:       builder,
		Resolver:      resolver,
		CustomQueries: customQueries,
		URLParam:      chi.URLParam,
		Auth:          cfg.Auth,
		TenantIDFunc:  tenantIDFromContext,
		QueryTimeout:  queryTimeout,
	}

	r := chi.NewRouter()
	r.Use(RecoveryMiddleware)
	r.Use(RequestIDMiddleware)
	r.Use(StructuredLoggingMiddleware)
	r.Use(chimw.Timeout(time.Duration(ResolveRequestTimeout(cfg)) * time.Second))

	// Multi-tenancy: X-Tenant-ID middleware (when auth is configured)
	if cfg.Auth != nil && cfg.Auth.Strategy == config.AuthStrategyHeader {
		tenantHeader := cfg.Auth.TenantHeader
		if tenantHeader == "" {
			tenantHeader = "X-Tenant-ID"
		}
		r.Use(TenantIDMiddleware(tenantHeader))
	}

	// System endpoints (always available)
	r.Get("/docs", SwaggerHandler)
	r.Get("/openapi.json", NewOpenAPIHandler(ts, false))

	// MCP-манифест — единственный source of truth для mcp-gateway
	r.Get("/mcp/manifest", handlers.MCPManifestHandler(cfg))

	// MCP-схема — обселиченное описание БД для LLM-агента
	// (требуется предварительный introspect через POST /admin/config/rewrite)
	r.Get("/mcp/schema", func(w http.ResponseWriter, r *http.Request) {
		inst := ts.resolveTenant(r)
		if inst == nil {
			handlers.RespondError(w, http.StatusBadRequest, "missing_tenant",
				"please specify a tenant identifier via X-Tenant-ID header or ?tenant= query parameter")
			return
		}
		if inst.IntrospectedSchema == nil {
			handlers.RespondError(w, http.StatusServiceUnavailable, "schema_not_available",
				"schema not yet introspected — please call POST /admin/config/rewrite first")
			return
		}
		result := configgen.GenerateSchemaForLLM(inst.IntrospectedSchema, inst.Config)
		handlers.RespondJSON(w, http.StatusOK, result)
	})

	// Prometheus metrics — доступно всегда, без аутентификации
	r.Handle("/metrics", promhttp.Handler())

	// /admin/* — admin endpoints are mounted EXCLUSIVELY via TenantStore.BuildAdminRouter()
	// in main.go (rootRouter.Mount("/admin", adminRouter)). The per-tenant router from
	// NewRouterFromConfig does NOT register /admin/* — it is served by the top-level mount.

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

	// Build entity name map for strategy routing
	entityMap := make(map[string]config.Entity, len(cfg.Entities))
	for i := range cfg.Entities {
		entityMap[cfg.Entities[i].Name] = cfg.Entities[i]
	}

	// Build DataSource for DataSource-based methods (schema).
	// Использует runtime.AdapterSubset как Querier (QueryContext) + adapter bridge.
	var dataSource *datasource.SQLDataSource
	if adapter != nil {
		dsAdapter := &runtime.AdapterToQuery{Inner: adapter}
		dataSource = datasource.NewSQLDataSource(adapter, dsAdapter, cfg.Entities, queryTimeout)
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

		// Strategy-based routing (search strategies: grep, filter, schema)
		// Takes precedence over Op-based routing for strategy endpoints.
		if ep.Strategy != "" {
			entityConfig, ok := entityMap[ep.Entity]
			if !ok {
				return nil, fmt.Errorf("endpoint %q: entity %q not found for strategy %q", ep.Path, ep.Entity, ep.Strategy)
			}
			idCol := entityConfig.IDColumnOrDefault()
			nameCol := entityConfig.FirstStringFieldColumn()

			// Schema strategy — uses DataSource directly (not the legacy Strategy pipeline).
			if ep.Strategy == "schema" {
				if dataSource != nil {
					h = handlers.NewSchemaHandler(dataSource, ep.Entity).ServeHTTP
				} else {
					// Fallback: legacy SchemaStrategy (без dataSource).
					strategy := search.NewSchemaStrategy(idCol, nameCol)
					h = handlers.NewStrategySchemaHandler(ctx, strategy, entityConfig).ServeHTTP
				}
			} else {
				var strategy search.Strategy
				switch ep.Strategy {
				case "grep":
					strategy = search.NewGrepStrategy(idCol, nameCol)
				case "filter":
					strategy = search.NewFilterStrategy(idCol, nameCol)
				default:
					return nil, fmt.Errorf("endpoint %q: unknown strategy %q", ep.Path, ep.Strategy)
				}
				h = handlers.NewStrategyHandler(ctx, strategy, ep.Entity, entityConfig)
			}
		} else {
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
			case "distinct":
				if ep.Entity == "" {
					return nil, fmt.Errorf("endpoint %q: op distinct requires entity", ep.Path)
				}
				h = handlers.DistinctHandler(ctx, ep.Entity)
			case "count":
				if ep.Entity == "" {
					return nil, fmt.Errorf("endpoint %q: op count requires entity", ep.Path)
				}
				h = handlers.CountHandler(ctx, ep.Entity)
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
