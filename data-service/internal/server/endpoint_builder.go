package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"

	"github.com/agent-tutor/data-service/internal/config"
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
func NewRouterFromConfig(cfg *config.Config, db runtime.AdapterSubset, adapter runtime.AdapterSubset, introspectAdapter datasource.Adapter, configPath string) (http.Handler, error) {
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
	}

	r := chi.NewRouter()
	r.Use(RecoveryMiddleware)
	r.Use(RequestIDMiddleware)
	r.Use(StructuredLoggingMiddleware)
	r.Use(chimw.RealIP)

	// Системные эндпоинты (всегда)
	r.Get("/docs", swaggerHandler)
	hasAdmin := introspectAdapter != nil
	r.Get("/openapi.json", NewOpenAPIHandler(cfg, hasAdmin))

	// /admin/* — админские эндпоинты (если адаптер передан)
	if introspectAdapter != nil {
		r.Get("/admin/discover", discoverHandler(introspectAdapter, cfg.DataSource))
		r.Post("/admin/config/rewrite", rewriteHandler(introspectAdapter, cfg.DataSource, configPath))
	}

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
		defer conn.Close()

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

// rewriteHandler — POST /admin/config/rewrite: перегенерировать конфиг из БД и сохранить в файл.
func rewriteHandler(adp datasource.Adapter, ds config.DataSourceConfig, configPath string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		conn, err := adp.Connect(r.Context(), ds.DSN)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "connect_error", err.Error())
			return
		}
		defer conn.Close()

		schema, err := adp.Introspect(r.Context(), conn)
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "introspect_error", err.Error())
			return
		}

		dsConfig := config.DataSourceConfig{Driver: ds.Driver, DSN: ds.DSN}
		newCfg := configgen.Generate(schema, dsConfig, nil)

		data, err := json.MarshalIndent(newCfg, "", "  ")
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "marshal_error", err.Error())
			return
		}

		if configPath == "" {
			handlers.RespondError(w, http.StatusBadRequest, "no_config_path", "configPath not configured on this instance")
			return
		}

		if err := os.WriteFile(configPath, data, 0644); err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "write_error", err.Error())
			return
		}

		slog.Info("config rewritten from DB schema",
			"path", configPath,
			"entities", len(newCfg.Entities),
			"endpoints", len(newCfg.Endpoints),
		)

		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"status":    "ok",
			"path":      configPath,
			"entities":  len(newCfg.Entities),
			"endpoints": len(newCfg.Endpoints),
			"note":      "Конфиг сохранён. Перезапусти сервис чтобы применить.",
		})
	}
}
