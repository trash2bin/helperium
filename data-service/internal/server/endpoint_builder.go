package server

import (
	"fmt"
	"net/http"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
)

// NewRouterFromConfig создаёт chi-роутер на основе конфигурации.
//
// Это точка входа для config-driven data-service (фаза 3.2+).
// Заменяет собой хардкодные routes из NewRouter.
func NewRouterFromConfig(cfg *config.Config, db runtime.AdapterSubset, adapter runtime.AdapterSubset) (http.Handler, error) {
	// 1. Конвертируем типы
	entities := runtime.ConfigToEntities(cfg.Entities)
	customQueries := runtime.ConfigToCustomQueries(cfg.CustomQueries)

	// 2. Создаём resolver
	resolver, err := runtime.NewEntityResolver(entities)
	if err != nil {
		return nil, fmt.Errorf("config router: entity resolver: %w", err)
	}

	// 3. Создаём query builder
	builder := runtime.NewBuilder(adapter)

	// 4. Контекст для хендлеров
	ctx := &handlers.Context{
		DB:            db,
		Adapter:       adapter,
		Builder:       builder,
		Resolver:      resolver,
		CustomQueries: customQueries,
		URLParam:      chi.URLParam,
	}

	// 5. Строим роутер по конфигу
	r := chi.NewRouter()

	// Middleware (как в NewRouter)
	r.Use(RecoveryMiddleware)
	r.Use(RequestIDMiddleware)
	r.Use(StructuredLoggingMiddleware)
	r.Use(chimw.RealIP)

	for _, ep := range cfg.Endpoints {
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
			params := make([]config.EndpointParam, 0)
			for _, p := range ep.Params {
				params = append(params, p)
			}
			h = handlers.CustomQueryHandler(ctx, ep.QueryID, params)
		default:
			return nil, fmt.Errorf("endpoint %q: unsupported op %q", ep.Path, ep.Op)
		}

		// Регистрация метода
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

	// Стандартные 404 и 405
	r.NotFound(handlers.NotFoundHandler)
	r.MethodNotAllowed(handlers.MethodNotAllowedHandler)

	return r, nil
}