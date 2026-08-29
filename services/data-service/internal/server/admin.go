// Package server — admin API handlers (фаза 3.7).
//
// Admin endpoints защищены ADMIN_TOKEN (Bearer-токен или env).
// Операции:
//
//	GET  /admin/config           — текущий конфиг (DSN скрыт)
//	POST /admin/config           — загрузить новый конфиг + валидация + hot reload
//	POST /admin/config/reload    — force перезагрузка с диска
//	GET  /admin/config/versions  — история версий (timestamp-based)
//	POST /admin/config/rewrite   — re-generate из БД (dev-only, уже был)
//
// Все операции работают без рестарта сервиса.
package server

import (
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// AdminContext — состояние, нужное admin-endpoint'ам для операций с конфигом.
// Поля, которые использовались только мёртвыми package-level обработчиками
// (AtomicRouter, Adapter, DB, Router, ReloadFn), удалены вместе с ними.
type AdminContext struct {
	ConfigPath string
}

// adminConfigResponse — DTO для GET /admin/config (DSN скрыт).
type adminConfigResponse struct {
	Version              int                           `json:"version"`
	Driver               config.Driver                 `json:"driver"`
	DataSource           *adminDataSourceResponse      `json:"data_source,omitempty"`
	Entities             []config.Entity               `json:"entities,omitempty"`
	Endpoints            []config.Endpoint             `json:"endpoints,omitempty"`
	CustomQueries        map[string]config.CustomQuery `json:"custom_queries,omitempty"`
	Stats                *config.StatsConfig           `json:"stats,omitempty"`
	Auth                 *config.AuthConfig            `json:"auth,omitempty"`
	MCPTools             []config.MCPTool              `json:"mcp_tools,omitempty"`
	Introspection        *config.IntrospectionConfig   `json:"introspection,omitempty"`
	SkipRules            []config.SkipRule             `json:"skip_rules,omitempty"`
	DisplayPrefixes      []string                      `json:"display_prefixes,omitempty"`
	CustomPlurals        map[string]string             `json:"custom_plurals,omitempty"`
	DisabledDefaultRules []string                      `json:"disabled_default_rules,omitempty"`

	// Field-level rules — те же поля, что в config.Config, чтобы админка
	// могла их читать и возвращать в PUT без потерь (round-trip).
	FilterableRules                []config.FieldRule `json:"filterable_rules,omitempty"`
	SearchableRules                []config.FieldRule `json:"searchable_rules,omitempty"`
	EnumRules                      []config.FieldRule `json:"enum_rules,omitempty"`
	DisabledDefaultFilterableRules []string           `json:"disabled_default_filterable_rules,omitempty"`
	DisabledDefaultSearchableRules []string           `json:"disabled_default_searchable_rules,omitempty"`
	DisabledDefaultEnumRules       []string           `json:"disabled_default_enum_rules,omitempty"`
	CustomShortNames               map[string]string  `json:"custom_short_names,omitempty"`
}

// adminDataSourceResponse — часть конфига.
// DSN намеренно не отдаётся в DTO (секрет); вместо него HasReadonlyDSN.
// ReadonlyDSN тоже не отдаётся: он секрет уровня DSN. Round-trip PUT не
// требует эха — adminConfigUpdateHandler сохраняет прежнее значение, когда
// входящий readonly_dsn пуст (empty-means-preserve).
type adminDataSourceResponse struct {
	Driver         config.Driver `json:"driver"`
	PoolSize       *int          `json:"pool_size,omitempty"`
	ReadOnly       *bool         `json:"read_only,omitempty"`
	HasReadonlyDSN bool          `json:"has_readonly_dsn"`
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

func responseFromDataSource(ds config.DataSourceConfig) *adminDataSourceResponse {
	return &adminDataSourceResponse{
		Driver:   ds.Driver,
		PoolSize: ds.PoolSize,
		ReadOnly: ds.ReadOnly,
		// ReadonlyDSN is intentionally NOT copied to the response DTO (secret;
		// HasReadonlyDSN is the only signal exposed).
		HasReadonlyDSN: ds.ReadonlyDSN != "",
	}
}

// ── Helpers ──

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
