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
		{Prefix: "session", Reason: "Django: server-side session storage — temporary, no business value"},
		// RAG internal
		{Prefix: "documents", Reason: "Helperium RAG: internal document chunks and embeddings"},
		// Laravel (future)
		{Prefix: "migrations", Reason: "Laravel: framework migration tracking, not user data"},
		{Prefix: "jobs", Reason: "Laravel: queue job storage (horizon, failed_jobs) — operational, not business"},
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

	// Read-only by default
	readOnly := true
	if cfg.DataSource.ReadOnly == nil {
		cfg.DataSource.ReadOnly = &readOnly
	}

	result := &config.Config{
		Version:    config.CurrentConfigVersion,
		DataSource: cfg.DataSource,
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
			continue
		}
		entities = append(entities, tableToEntity(tbl, displayPrefixes))
	}

	endpoints := buildCRUDEndpoints(entities)
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

	result.Entities = entities
	result.Endpoints = endpoints
	result.Stats = &config.StatsConfig{Counters: buildCounters(entities)}

	if len(customQueries) > 0 {
		result.CustomQueries = customQueries
	}

	result.MCPTools = GenerateMCPTools(endpoints, entities, displayPrefixes, customPlurals)

	return result
}

// ── CRUD endpoint generation ──

// buildCRUDEndpoints creates CRUD endpoints (get_by_id, find, list, distinct, count)
// for each entity based on its table structure, including filter params.
func buildCRUDEndpoints(entities []config.Entity) []config.Endpoint {
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

		// find (по name-полю) — поиск по тексту + фильтры
		searchCol := findSearchFieldFromEntity(entity)
		if searchCol != "" {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpFind,
				Entity:      entity.Name,
				SearchField: searchCol,
				QueryParam:  searchCol,
				Description: fmt.Sprintf("Searches %s by name. Returns all records when no query given.", entity.Name),
				Params:      buildFilterParamsFromEntity(entity, searchCol),
			})
		} else if entity.IDColumn != "" {
			// Нет name-поля — list как fallback с фильтрами
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpList,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns all %s. Use filters to narrow results.", entity.Name),
				Params:      buildFilterParamsFromEntity(entity, ""),
			})
		}

		// distinct endpoint — enum-колонки
		enumCols := findEnumColumnsFromEntity(entity)
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

		// count endpoint
		endpoints = append(endpoints, config.Endpoint{
			Method:      config.MethodGET,
			Path:        fmt.Sprintf("/%s/count", entity.Name),
			Op:          config.OpCount,
			Entity:      entity.Name,
			Description: fmt.Sprintf("Counts %s records matching filters", entity.Name),
		})

		// grep endpoint (text search)
		endpoints = append(endpoints, config.Endpoint{
			Method:   config.MethodGET,
			Path:     fmt.Sprintf("/%s/grep", entity.Name),
			Op:       config.OpFind,
			Strategy: "grep",
			Entity:   entity.Name,
			Description: fmt.Sprintf("Search %s by text query. Pass 'pattern' parameter for text search.", entity.Name),
		})

		// filter endpoint (field-based filtering)
		endpoints = append(endpoints, config.Endpoint{
			Method:   config.MethodGET,
			Path:     fmt.Sprintf("/%s/filter", entity.Name),
			Op:       config.OpFind,
			Strategy: "filter",
			Entity:   entity.Name,
			Description: fmt.Sprintf("Filter %s by field values. Pass field__op parameters.", entity.Name),
		})

		// schema endpoint — metadata discovery
		endpoints = append(endpoints, config.Endpoint{
			Method:   config.MethodGET,
			Path:     fmt.Sprintf("/%s/schema", entity.Name),
			Op:       config.OpFind, // dummy — strategy routing заменит
			Strategy: "schema",
			Entity:   entity.Name,
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
