// Package configgen генерирует конфиг data-service из интроспекции БД.
//
// Берёт datasource.Schema (таблицы, колонки, FK) и превращает в готовый
// config.Config с entities, endpoint'ами и stats. Без custom_queries —
// их пишет клиент под свою бизнес-логику.
//
// Использование:
//
//	adapter := datasource.SqliteAdapter{}
//	conn, _ := adapter.Connect(ctx, "university.db")
//	schema, _ := adapter.Introspect(ctx, conn)
//	cfg := configgen.Generate(schema, datasourceConfig, nil)
//	json.NewEncoder(os.Stdout).Encode(cfg)
package configgen

import (
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Skip rules ──

// DefaultSkipRules returns framework-agnostic rules for system tables.
// Used by Generate to filter out Django, Laravel, Rails, and DB-internal tables.
func DefaultSkipRules() []config.SkipRule {
	return []config.SkipRule{
		// SQLite internals
		{Prefix: "sqlite_", Reason: "SQLite: internal schema tables (sqlite_sequence, sqlite_stat1, etc.) — not user data"},
		// PostgreSQL internals
		{Prefix: "pg_", Reason: "PostgreSQL: internal system catalogs (pg_type, pg_class, pg_attribute) — not user tables"},
		{Prefix: "pg_catalog", Reason: "PostgreSQL: system catalog schema with all internal types, functions, and meta"},
		{Prefix: "information_schema", Reason: "SQL standard: read-only system views describing database structure"},
		// Django framework
		{Prefix: "auth_", Reason: "Django: built-in auth tables (auth_user, auth_group, auth_permission) — not business data"},
		{Prefix: "django_", Reason: "Django: framework metadata (django_migrations, django_content_type, django_admin_log)"},
		// RAG internal — document_chunks однозначно служебная; сами documents
		// НЕ скипаем (P1-4): имя слишком общеупотребимо для бизнес-таблиц
		// (договоры, документы).
		{Prefix: "document_chunks", Reason: "Helperium RAG: internal document chunks"},
		// Laravel (future)
		{Prefix: "migrations", Reason: "Laravel: framework migration tracking, not user data"},
		{Prefix: "failed_jobs", Reason: "Laravel: queue failure log — operational, not business"},
		// Rails (future)
		{Prefix: "schema_migrations", Reason: "Rails: migration version tracking — framework internals"},
		{Prefix: "ar_internal_metadata", Reason: "Rails: ActiveRecord internal environment and schema metadata"},
	}
}

// shouldSkip checks if a table name matches any skip rule.
// If skipRules is provided, uses structured SkipRule matching.
// Otherwise falls back to legacy prefix-only matching.
func shouldSkip(name string, skipRules []config.SkipRule, legacyPrefixes []string) bool {
	// For schema-qualified names (e.g. "public.auth_group"),
	// match against both the full name and the short name (after last dot).
	shortName := name
	if idx := strings.LastIndex(name, "."); idx >= 0 {
		shortName = name[idx+1:]
	}

	for _, rule := range skipRules {
		if rule.Matches(name) || rule.Matches(shortName) {
			return true
		}
	}
	for _, p := range legacyPrefixes {
		if strings.HasPrefix(name, p) || strings.HasPrefix(shortName, p) {
			return true
		}
	}
	return false
}

// Generate создаёт *config.Config из интроспекции схемы БД.
//
// Параметры:
//   - schema — результат Introspect адаптера
//   - cfg — конфиг с DataSource, SkipRules, DisplayPrefixes, CustomPlurals настройками
func Generate(schema *datasource.Schema, cfg *config.Config) *config.Config {
	skipRules := DefaultSkipRules()
	// Фильтруем отключённые дефолтные правила
	if len(cfg.DisabledDefaultRules) > 0 {
		disabled := make(map[string]bool, len(cfg.DisabledDefaultRules))
		for _, prefix := range cfg.DisabledDefaultRules {
			disabled[prefix] = true
		}
		var filtered []config.SkipRule
		for _, rule := range skipRules {
			if !disabled[rule.Prefix] {
				filtered = append(filtered, rule)
			}
		}
		skipRules = filtered
	}
	skipRules = append(skipRules, cfg.SkipRules...)

	// DisplayPrefixes — override если заданы
	displayPrefixes := DefaultDisplayPrefixes()
	if len(cfg.DisplayPrefixes) > 0 {
		displayPrefixes = cfg.DisplayPrefixes
	}

	// CustomPlurals from config
	customPlurals := cfg.CustomPlurals
	if customPlurals == nil {
		customPlurals = make(map[string]string)
	}

	// Read-only by default — НЕ мутируем входной cfg (README: «чистая функция»).
	// Раньше писали cfg.DataSource.ReadOnly = &readOnly прямо во входной указатель:
	// при параллельном Generate на общем шаблонном cfg это data race (P0-3).
	// Копируем DataSource локально и выставляем дефолт только в копии.
	readOnly := true
	dataSource := cfg.DataSource
	if dataSource.ReadOnly == nil {
		dataSource.ReadOnly = &readOnly
	}

	result := &config.Config{
		Version:    config.CurrentConfigVersion,
		DataSource: dataSource,
		Meta: &config.ConfigMeta{
			ConfigVersion:    config.CurrentConfigVersion,
			GeneratedAt:      time.Now().UTC().Format(time.RFC3339),
			GeneratorVersion: "", // filled by build system
		},
	}

	// Сортируем таблицы для детерминизма
	tables := append([]datasource.Table{}, schema.Tables...)
	sort.Slice(tables, func(i, j int) bool {
		return tables[i].Name < tables[j].Name
	})

	var entities []config.Entity
	for _, tbl := range tables {
		if shouldSkip(tbl.Name, skipRules, nil) {
			// P1-4: предупреждаем о скипе — DefaultSkipRules содержит
			// общеупотребимые слова (documents, jobs, session, migrations),
			// которые могут быть бизнес-таблицами (договоры, вакансии).
			// Логируем имя таблицы и число колонок, чтобы при онбординге
			// было видно, что таблица реально пропущена.
			slog.Warn("configgen: skipping table", "table", tbl.Name, "columns", len(tbl.Columns))
			continue
		}
		entities = append(entities, tableToEntity(tbl, displayPrefixes))
	}

	// Field rules: filterable, searchable, enum
	filterableRules := resolveFieldRules(DefaultFilterableFieldRules(), cfg.DisabledDefaultFilterableRules, cfg.FilterableRules)
	searchableRules := resolveFieldRules(DefaultSearchableFieldRules(), cfg.DisabledDefaultSearchableRules, cfg.SearchableRules)
	enumRules := resolveFieldRules(DefaultEnumFieldRules(), cfg.DisabledDefaultEnumRules, cfg.EnumRules)

	endpoints := buildCRUDEndpoints(entities, filterableRules, searchableRules, enumRules)
	navEndpoints, customQueries := buildNavigationEndpoints(entities)
	endpoints = append(endpoints, navEndpoints...)

	// Системные эндпоинты
	endpoints = append(endpoints, config.Endpoint{
		Method: config.MethodGET,
		Path:   "/health",
		Op:     config.OpBuiltinHealth,
	})
	endpoints = append(endpoints, config.Endpoint{
		Method: config.MethodGET,
		Path:   "/stats",
		Op:     config.OpBuiltinStats,
	})

	// /q/* — консолидированный LLM-диспетчер (Фаза 2).
	// Системные endpoints: указывают на /q/* хендлеры, которые регистрирует
	// NewRouterFromConfig отдельно от cfg.Endpoints (см. endpoint_builder.go).
	// Здесь они нужны, чтобы валидация mcp_tools[].endpoint (см. types.go)
	// находила "/q/map" и т.д. в cfg.Endpoints.
	for _, qPath := range []string{
		"/q/map", "/q/describe", "/q/search", "/q/filter", "/q/get", "/q/related",
	} {
		endpoints = append(endpoints, config.Endpoint{
			Method:      config.MethodGET,
			Path:        qPath,
			Op:          config.OpQDispatch,
			Description: "Consolidated LLM dispatch endpoint",
		})
	}

	result.Entities = entities
	result.Endpoints = endpoints
	result.Stats = &config.StatsConfig{Counters: buildCounters(entities)}

	// Persist resolved field rules so they survive config reload
	// Persist resolved field rules so they survive config reload
	result.FilterableRules = filterableRules
	result.DisabledDefaultFilterableRules = cfg.DisabledDefaultFilterableRules
	result.SearchableRules = searchableRules
	result.DisabledDefaultSearchableRules = cfg.DisabledDefaultSearchableRules
	result.EnumRules = enumRules
	result.DisabledDefaultEnumRules = cfg.DisabledDefaultEnumRules
	result.CustomShortNames = cfg.CustomShortNames

	if len(customQueries) > 0 {
		result.CustomQueries = customQueries
	}

	result.MCPTools = GenerateMCPTools(endpoints, entities, displayPrefixes, customPlurals, filterableRules, searchableRules, cfg.LLMToolPolicy)

	return result
}

// ── CRUD endpoint generation ──

// buildCRUDEndpoints creates CRUD endpoints (get_by_id, grep, filter, schema, distinct, count)
// for each entity based on its table structure.
// ResolveFieldRules resolves effective field rules from defaults, disabled IDs, and custom rules.
// Exported для runtime-путей (endpoint_builder, mcp_manifest), чтобы кастомные
// FilterableRules/DisabledDefault* доходили до search-стратегий и MCP-манифеста.
// Pattern: Default*() → filter out disabled by stable ID → append custom from config.
func ResolveFieldRules(defaults []config.FieldRule, disabledIDs []string, custom []config.FieldRule) []config.FieldRule {
	return resolveFieldRules(defaults, disabledIDs, custom)
}

// resolveFieldRules resolves effective field rules from defaults, disabled IDs, and custom rules.
// Pattern: Default*() → filter out disabled by stable ID (exact match) → append custom.
// Reason НЕ используется для матчинга (нестабильная человекочитаемая строка).
//
// Идемпотентность (M7-фикс): resolved-дефолт с ID, попавший в custom при следующем
// rewrite (через ExtractIntent→Hydrate), отфильтровывается по ID — иначе дефолт
// добавлялся поверх себя (rewrite1→default×2, rewrite2→default×3).
func resolveFieldRules(defaults []config.FieldRule, disabledIDs []string, custom []config.FieldRule) []config.FieldRule {
	if len(disabledIDs) > 0 {
		disabled := make(map[string]bool, len(disabledIDs))
		for _, id := range disabledIDs {
			disabled[id] = true
		}
		var filtered []config.FieldRule
		for _, rule := range defaults {
			// Правило без ID никогда не отключается (защита от случайного
			// отключения custom-правил и тихого возврата дефолта без ID).
			if rule.ID == "" || !disabled[rule.ID] {
				filtered = append(filtered, rule)
			}
		}
		defaults = filtered
		// Custom-часть: resolved-дефолты (с ID) отфильтровываем по тем же disabled ID.
		var filteredCustom []config.FieldRule
		for _, rule := range custom {
			if rule.ID == "" || !disabled[rule.ID] {
				filteredCustom = append(filteredCustom, rule)
			}
		}
		custom = filteredCustom
	}

	// M7: дедупликация — custom не должен содержать правил с ID, уже
	// присутствующим в defaults. Без этого rewrite дрейфует: Generate пишет
	// resolved-список (defaults+custom) обратно в конфиг, Hydrate передаёт его
	// как custom, и при следующем Generate дефолт растёт поверх себя
	// (rewrite1→default×2, rewrite2→default×3).
	// Правила БЕЗ ID считаются уникальными custom-правилами и всегда сохраняются.
	if len(custom) > 0 {
		defaultIDs := make(map[string]bool, len(defaults))
		for _, d := range defaults {
			if d.ID != "" {
				defaultIDs[d.ID] = true
			}
		}
		var deduped []config.FieldRule
		for _, rule := range custom {
			if rule.ID != "" && defaultIDs[rule.ID] {
				continue // дрейф-дефолт в custom — пропускаем
			}
			deduped = append(deduped, rule)
		}
		custom = deduped
	}

	return append(defaults, custom...)
}

func buildCRUDEndpoints(entities []config.Entity, filterableRules, searchableRules, enumRules []config.FieldRule) []config.Endpoint {
	var endpoints []config.Endpoint

	for _, entity := range entities {
		// get_by_id (по entity.IDColumn)
		if entity.IDColumn != "" {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/{%s}", entity.Name, entity.IDColumn),
				Op:          config.OpGetByID,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns %s by identifier", entity.Name),
			})
		}

		// distinct endpoint — enum-колонки
		enumCols := findEnumColumnsFromEntity(entity, enumRules)
		if len(enumCols) > 0 {
			required := true
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/distinct", entity.Name),
				Op:          config.OpDistinct,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns unique values for enum columns in %s", entity.Name),
				Params: []config.EndpointParam{
					{
						Name:     "column",
						In:       config.ParamInQuery,
						Type:     config.ParamTypeString,
						Required: &required,
						Description: fmt.Sprintf(
							"Column name to get distinct values from. Available columns: %s",
							strings.Join(enumCols, ", ")),
					},
				},
			})
		}

		// count endpoint — только если есть не-PK поля
		if hasDataFields(entity) {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/count", entity.Name),
				Op:          config.OpCount,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Counts %s records matching filters", entity.Name),
			})
		}

		// grep endpoint (text search) — только если есть searchable поля
		if hasSearchableFields(entity, searchableRules) {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/grep", entity.Name),
				Op:          config.OpStrategy,
				Strategy:    "grep",
				Entity:      entity.Name,
				Description: fmt.Sprintf("Search %s by text query. Pass 'pattern' parameter for text search.", entity.Name),
			})
		}

		// filter endpoint (field-based filtering) — только если есть filterable поля
		if hasFilterableFields(entity, filterableRules) {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/filter", entity.Name),
				Op:          config.OpStrategy,
				Strategy:    "filter",
				Entity:      entity.Name,
				Description: fmt.Sprintf("Filter %s by field values. Pass field__op parameters.", entity.Name),
			})
		}

		// schema endpoint — metadata discovery (всегда)
		endpoints = append(endpoints, config.Endpoint{
			Method:      config.MethodGET,
			Path:        fmt.Sprintf("/%s/schema", entity.Name),
			Op:          config.OpStrategy,
			Strategy:    "schema",
			Entity:      entity.Name,
			Description: fmt.Sprintf("Get metadata about %s: total count, field types, distinct values, numeric ranges.", entity.Name),
		})
	}

	return endpoints
}

// buildCounters creates stats counters for each entity.
func buildCounters(entities []config.Entity) []config.Counter {
	counters := make([]config.Counter, 0, len(entities))
	for _, entity := range entities {
		counters = append(counters, config.Counter{
			Name:   entity.Name,
			Entity: entity.Name,
		})
	}
	return counters
}
