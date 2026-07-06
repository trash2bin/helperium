// Package server — admin API handlers (фаза 3.7).
//
// Admin endpoints защищены ADMIN_TOKEN (Bearer-токен или env).
// Операции:
//   GET  /admin/config           — текущий конфиг (DSN скрыт)
//   POST /admin/config           — загрузить новый конфиг + валидация + hot reload
//   POST /admin/config/reload    — force перезагрузка с диска
//   GET  /admin/config/versions  — история версий (timestamp-based)
//   POST /admin/config/rewrite   — re-generate из БД (dev-only, уже был)
//
// Все операции работают без рестарта сервиса.
package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/configgen"
	"github.com/agent-tutor/data-service/internal/datasource"
	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// AdminContext — состояние, нужное admin-endpoint'ам для операций с конфигом.
type AdminContext struct {
	ConfigPath   string
	AtomicRouter *atomic.Value
	Adapter      datasource.Adapter
	DB           runtime.AdapterSubset
	Router       runtime.AdapterSubset // same as DB; separate for clarity

	// reloadFn — колбэк для hot reload (из main.go).
	// Вызывается после записи нового конфига на диск, чтобы атомарно
	// перестроить роутер.
	ReloadFn func(configPath string) error

	// ApprovedWriteEndpoints — множество утверждённых write-эндпоинтов
	// (ключ = path эндпоинта). Используется в read-only режиме для
	// выборочного разрешения мутирующих операций через admin API.
	ApprovedWriteEndpoints map[string]bool
}

// adminConfigResponse — DTO для GET /admin/config (без DSN).
type adminConfigResponse struct {
	Version       int                              `json:"version"`
	Driver        config.Driver                    `json:"driver"`
	Entities      []config.Entity                  `json:"entities,omitempty"`
	Endpoints     []config.Endpoint                `json:"endpoints,omitempty"`
	CustomQueries map[string]config.CustomQuery    `json:"custom_queries,omitempty"`
	Stats         *config.StatsConfig              `json:"stats,omitempty"`
	Auth          *config.AuthConfig               `json:"auth,omitempty"`
	MCPTools      []config.MCPTool                 `json:"mcp_tools,omitempty"`
	Introspection *config.IntrospectionConfig      `json:"introspection,omitempty"`
}

// ── Auth middleware ──

// AdminAuthMiddleware проверяет Authorization: Bearer <token>.
// Токен читается из ADMIN_TOKEN (env). Если ADMIN_TOKEN не задан —
// admin API запрещён (401).
func AdminAuthMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := os.Getenv("ADMIN_TOKEN")
		if token == "" {
			handlers.RespondError(w, http.StatusUnauthorized, "admin_disabled",
				"ADMIN_TOKEN not configured")
			return
		}

		auth := r.Header.Get("Authorization")
		if auth == "" {
			handlers.RespondError(w, http.StatusUnauthorized, "auth_required",
				"Authorization header required")
			return
		}

		if !strings.HasPrefix(auth, "Bearer ") {
			handlers.RespondError(w, http.StatusUnauthorized, "auth_malformed",
				"Authorization must be Bearer <token>")
			return
		}

		provided := strings.TrimSpace(strings.TrimPrefix(auth, "Bearer "))
		if provided != token {
			handlers.RespondError(w, http.StatusUnauthorized, "auth_invalid",
				"Invalid admin token")
			return
		}

		next.ServeHTTP(w, r)
	})
}

// ── Handlers ──

// adminConfigHandler возвращает текущий конфиг без DSN.
func adminConfigHandler(cfg *config.Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		resp := adminConfigResponse{
			Version:       cfg.Version,
			Driver:        cfg.DataSource.Driver,
			Entities:      cfg.Entities,
			Endpoints:     cfg.Endpoints,
			CustomQueries: cfg.CustomQueries,
			Stats:         cfg.Stats,
			Auth:          cfg.Auth,
			MCPTools:      cfg.MCPTools,
			Introspection: cfg.Introspection,
		}
		handlers.RespondJSON(w, http.StatusOK, resp)
	}
}

// adminConfigUpdateHandler принимает новый конфиг JSON, валидирует,
// сохраняет на диск, инициирует hot reload.
func adminConfigUpdateHandler(ctx *AdminContext) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// 1. Read raw body
		var raw json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
			handlers.RespondError(w, http.StatusBadRequest, "invalid_json",
				fmt.Sprintf("failed to parse body: %v", err))
			return
		}

		// 2. Parse into config (no envsubst — admin sends final values)
		var newCfg config.Config
		if err := json.Unmarshal(raw, &newCfg); err != nil {
			handlers.RespondError(w, http.StatusBadRequest, "invalid_config",
				fmt.Sprintf("failed to unmarshal config: %v", err))
			return
		}

		// 3. Validate against schema
		if err := config.Validate(raw, ""); err != nil {
			handlers.RespondError(w, http.StatusBadRequest, "validation_error",
				fmt.Sprintf("config validation failed: %v", err))
			return
		}

		// 4. Try to build router (dry-run — ловим runtime ошибки)
		if _, err := buildRouterFromConfig(&newCfg, ctx); err != nil {
			handlers.RespondError(w, http.StatusBadRequest, "build_error",
				fmt.Sprintf("router build failed: %v", err))
			return
		}

		// 5. Сохраняем текущий конфиг как версию (backup)
		if err := archiveCurrentConfig(ctx.ConfigPath); err != nil {
			slog.Warn("admin config: failed to archive current config", "error", err)
			// Не фатально — продолжаем
		}

		// 6. Записываем новый конфиг на диск
		prettyJSON, err := json.MarshalIndent(newCfg, "", "  ")
		if err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "marshal_error",
				fmt.Sprintf("failed to marshal config: %v", err))
			return
		}
		if err := os.WriteFile(ctx.ConfigPath, prettyJSON, 0644); err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "write_error",
				fmt.Sprintf("failed to write config: %v", err))
			return
		}

		slog.Info("admin config: written to disk", "path", ctx.ConfigPath)

		// 7. Hot reload — перестроить роутер
		if ctx.ReloadFn != nil {
			if err := ctx.ReloadFn(ctx.ConfigPath); err != nil {
				handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
					fmt.Sprintf("config saved but reload failed: %v", err))
				return
			}
		} else {
			slog.Warn("admin config: no reload function set — config saved but router not rebuilt")
		}

		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"status":    "applied",
			"path":      ctx.ConfigPath,
			"entities":  len(newCfg.Entities),
			"endpoints": len(newCfg.Endpoints),
		})
	}
}

// adminConfigReloadHandler force-перезагружает конфиг с диска.
func adminConfigReloadHandler(ctx *AdminContext) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if ctx.ReloadFn == nil {
			handlers.RespondError(w, http.StatusInternalServerError, "reload_disabled",
				"reload function not configured")
			return
		}

		if err := ctx.ReloadFn(ctx.ConfigPath); err != nil {
			handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
				fmt.Sprintf("reload failed: %v", err))
			return
		}

		handlers.RespondJSON(w, http.StatusOK, map[string]string{
			"status": "reloaded",
			"path":   ctx.ConfigPath,
		})
	}
}

// adminConfigVersionsHandler возвращает список версий конфига (backup'ов).
func adminConfigVersionsHandler(ctx *AdminContext) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		versionsDir := filepath.Join(filepath.Dir(ctx.ConfigPath), "config_versions")
		entries, err := os.ReadDir(versionsDir)
		if err != nil {
			if os.IsNotExist(err) {
				handlers.RespondJSON(w, http.StatusOK, []string{})
				return
			}
			handlers.RespondError(w, http.StatusInternalServerError, "readdir_error",
				fmt.Sprintf("failed to read versions: %v", err))
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

		// Most recent first
		sort.Slice(versions, func(i, j int) bool {
			return versions[i].Name > versions[j].Name
		})

		handlers.RespondJSON(w, http.StatusOK, versions)
	}
}

// ── Helpers ──

// buildRouterFromConfig собирает роутер из конфига (dry-run).
func buildRouterFromConfig(cfg *config.Config, ctx *AdminContext) (http.Handler, error) {
	return NewRouterFromConfig(nil, cfg, ctx.DB, ctx.Router, ctx.Adapter, ctx.ConfigPath, nil)
}

// archiveCurrentConfig сохраняет текущий config.json как config.{ts}.json.
func archiveCurrentConfig(configPath string) error {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read config: %w", err)
	}

	versionsDir := filepath.Join(filepath.Dir(configPath), "config_versions")
	if err := os.MkdirAll(versionsDir, 0755); err != nil {
		return fmt.Errorf("create versions dir: %w", err)
	}

	ts := time.Now().UTC().Format("2006-01-02T150405")
	archivePath := filepath.Join(versionsDir, fmt.Sprintf("config.%s.json", ts))
	if err := os.WriteFile(archivePath, data, 0644); err != nil {
		return fmt.Errorf("write archive: %w", err)
	}

	slog.Info("admin config: archived", "archive", archivePath)
	return nil
}

// ── MCP Tool Management (read-only одобрение write-тулов) ──

// adminPendingToolsHandler возвращает список write-эндпоинтов, ожидающих подтверждения.
func adminPendingToolsHandler(cfg *config.Config, ctx *AdminContext) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		readOnly := cfg.DataSource.ReadOnly != nil && *cfg.DataSource.ReadOnly
		if !readOnly {
			handlers.RespondJSON(w, http.StatusOK, map[string]any{
				"mode":  "read_write",
				"tools": []string{},
				"note":  "Read-only mode is OFF — all tools are active",
			})
			return
		}

		type pendingTool struct {
			Name     string `json:"name"`
			Method   string `json:"method"`
			Path     string `json:"path"`
			Approved bool   `json:"approved"`
		}

		pending := make([]pendingTool, 0)
		toolNames := deriveToolNames(cfg.Endpoints)
		for _, ep := range cfg.Endpoints {
			if isWriteMethod(ep.Method) {
				name := toolNames[ep.Path]
				pending = append(pending, pendingTool{
					Name:     name,
					Method:   string(ep.Method),
					Path:     ep.Path,
					Approved: ctx.ApprovedWriteEndpoints[ep.Path],
				})
			}
		}

		// Считаем approved и pending на месте
		approvedCount := 0
		pendingCount := 0
		for _, t := range pending {
			if t.Approved {
				approvedCount++
			} else {
				pendingCount++
			}
		}

		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"mode":     "read_only",
			"tools":    pending,
			"approved": approvedCount,
			"pending":  pendingCount,
		})
	}
}

// adminApproveToolHandler подтверждает write-тул для использования в read-only режиме.
func adminApproveToolHandler(cfg *config.Config, ctx *AdminContext) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		toolName := chi.URLParam(r, "toolName")
		if toolName == "" {
			handlers.RespondError(w, http.StatusBadRequest, "missing_tool", "toolName is required")
			return
		}

		// Находим endpoint по имени тула
		toolNames := deriveToolNames(cfg.Endpoints)
		var epPath string
		for _, ep := range cfg.Endpoints {
			name := toolNames[ep.Path]
			if name == toolName && isWriteMethod(ep.Method) {
				epPath = ep.Path
				break
			}
		}

		if epPath == "" {
			handlers.RespondError(w, http.StatusNotFound, "tool_not_found",
				fmt.Sprintf("write tool %q not found", toolName))
			return
		}

		if ctx.ApprovedWriteEndpoints == nil {
			ctx.ApprovedWriteEndpoints = make(map[string]bool)
		}
		ctx.ApprovedWriteEndpoints[epPath] = true

		// Сохраняем на диск для persistence между рестартами
		if err := saveApprovedTools(ctx); err != nil {
			slog.Warn("admin approve: failed to persist approvals", "error", err)
		}

		slog.Info("admin approve: write tool approved", "tool", toolName, "path", epPath)
		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"status": "approved",
			"tool":   toolName,
			"path":   epPath,
		})
	}
}

// deriveToolNames создаёт map[endpointPath]toolName для быстрого lookup'а.
func deriveToolNames(endpoints []config.Endpoint) map[string]string {
	names := make(map[string]string, len(endpoints))
	for _, ep := range endpoints {
		names[ep.Path] = deriveToolName(ep)
	}
	return names
}

// deriveToolName генерирует имя MCP-тула из endpoint'а.
func deriveToolName(ep config.Endpoint) string {
	switch ep.Op {
	case config.OpBuiltinHealth:
		return "health"
	case config.OpBuiltinStats:
		return "stats"
	case config.OpGetByID:
		return "get_" + ep.Entity
	case config.OpFind:
		return "find_" + ep.Entity
	case config.OpList:
		return "list_" + ep.Entity
	case config.OpCustomQuery:
		if ep.QueryID != "" {
			return "query_" + ep.QueryID
		}
		return "query_" + strings.Trim(strings.ReplaceAll(strings.ReplaceAll(ep.Path, "{", ""), "}", ""), "/")
	default:
		return ""
	}
}

func saveApprovedTools(ctx *AdminContext) error {
	if ctx.ConfigPath == "" {
		return nil
	}
	data, err := json.MarshalIndent(ctx.ApprovedWriteEndpoints, "", "  ")
	if err != nil {
		return err
	}
	approvalsPath := filepath.Join(filepath.Dir(ctx.ConfigPath), "approved_tools.json")
	return os.WriteFile(approvalsPath, data, 0644)
}

// LoadApprovedTools загружает утверждённые write-эндпоинты из файла approved_tools.json.
func LoadApprovedTools(ctx *AdminContext) error {
	if ctx.ConfigPath == "" {
		return nil
	}
	approvalsPath := filepath.Join(filepath.Dir(ctx.ConfigPath), "approved_tools.json")
	data, err := os.ReadFile(approvalsPath)
	if err != nil {
		if os.IsNotExist(err) {
			ctx.ApprovedWriteEndpoints = make(map[string]bool)
			return nil
		}
		return err
	}
	return json.Unmarshal(data, &ctx.ApprovedWriteEndpoints)
}

// ── Dev-only: config rewrite из БД (был до фазы 3.7) ──

// adminRewriteHandler — POST /admin/config/rewrite.
// Генерирует конфиг из схемы БД и сохраняет на диск.
// Это dev-инструмент, не production admin API.
func adminRewriteHandler(adp datasource.Adapter, ds config.DataSourceConfig, configPath string, ctx *AdminContext) http.HandlerFunc {
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
			handlers.RespondError(w, http.StatusBadRequest, "no_config_path",
				"configPath not configured on this instance")
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

		// Hot reload
		if ctx.ReloadFn != nil {
			if err := ctx.ReloadFn(configPath); err != nil {
				slog.Error("admin rewrite: reload failed", "error", err)
				handlers.RespondError(w, http.StatusInternalServerError, "reload_error",
					fmt.Sprintf("config saved but reload failed: %v", err))
				return
			}
		}

		handlers.RespondJSON(w, http.StatusOK, map[string]any{
			"status":    "ok",
			"path":      configPath,
			"entities":  len(newCfg.Entities),
			"endpoints": len(newCfg.Endpoints),
			"note":      "Конфиг сохранён и применён без рестарта.",
		})
	}
}
