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

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// SkipRule defines a pattern for tables to exclude from tool generation.
// Matching is done against the table name; multiple fields are AND-ed.
type SkipRule struct {
	Prefix  string // Match table name prefix (e.g., "auth_", "django_")
	Suffix  string // Match table name suffix
	Contains string // Match substring
	Reason  string // Human-readable reason for skipping
}

// matches returns true if the table name satisfies this rule.
func (r SkipRule) matches(name string) bool {
	if r.Prefix != "" && !strings.HasPrefix(name, r.Prefix) {
		return false
	}
	if r.Suffix != "" && !strings.HasSuffix(name, r.Suffix) {
		return false
	}
	if r.Contains != "" && !strings.Contains(name, r.Contains) {
		return false
	}
	return true
}

// DefaultSkipRules returns framework-agnostic rules for system tables.
// Used by Generate to filter out Django, Laravel, Rails, and DB-internal tables.
func DefaultSkipRules() []SkipRule {
	return []SkipRule{
		// SQLite internals
		{Prefix: "sqlite_", Reason: "SQLite system table"},
		// PostgreSQL internals
		{Prefix: "pg_", Reason: "PostgreSQL system table"},
		{Prefix: "pg_catalog", Reason: "PostgreSQL catalog"},
		{Prefix: "information_schema", Reason: "SQL information schema"},
		// Django framework
		{Prefix: "auth_", Reason: "Django auth system (not business data)"},
		{Prefix: "django_", Reason: "Django framework internals"},
		{Prefix: "session", Reason: "Django session storage"},
		// RAG internal
		{Prefix: "documents", Reason: "RAG internal table"},
		// Laravel (future)
		{Prefix: "migrations", Reason: "Framework migration tracking"},
		{Prefix: "jobs", Reason: "Queue internals"},
		{Prefix: "failed_jobs", Reason: "Queue internals"},
		// Rails (future)
		{Prefix: "schema_migrations", Reason: "Rails migration tracking"},
		{Prefix: "ar_internal_metadata", Reason: "Rails internals"},
	}
}

// DisplayPrefixes are common table name prefixes to strip when generating
// human-readable display names for entities and tools.
// Change these when recompiling for a project that uses different prefixes
// (e.g. "wp_" for WordPress, "ce_" for Concrete5).
var DisplayPrefixes = []string{"catalog_", "auth_", "django_"}

// isNameField возвращает true, если колонка похожа на поисковое имя.
// Критерий: тип string, название содержит name/last_name/first_name/title.
func isNameField(col datasource.Column) bool {
	lower := strings.ToLower(col.Name)
	return col.Type == datasource.TypeString &&
		(lower == "name" ||
			strings.HasSuffix(lower, "_name") ||
			strings.HasSuffix(lower, "_title") ||
			strings.HasPrefix(lower, "name"))
}

// findSearchField ищет колонку для поиска (первую подходящую).
func findSearchField(cols []datasource.Column) (datasource.Column, bool) {
	for _, c := range cols {
		if isNameField(c) {
			return c, true
		}
	}
	return datasource.Column{}, false
}

// Generate создаёт *config.Config из интроспекции схемы БД.
//
// Параметры:
//   - schema — результат Introspect адаптера
//   - ds — data_source часть конфига (driver + dsn)
//   - skipPrefixes — дополнительные префиксы для исключения таблиц (nil = только дефолтные)
func Generate(schema *datasource.Schema, ds config.DataSourceConfig, skipPrefixes []string) *config.Config {
	skipRules := DefaultSkipRules()

	// Read-only by default: сгенерированный конфиг не должен мутировать БД.
	// Клиент может явно выставить read_only: false вручную через admin API.
	trueVal := true
	if ds.ReadOnly == nil {
		ds.ReadOnly = &trueVal
	}

	cfg := &config.Config{
		Version:    1,
		DataSource: ds,
	}

	entities := make([]config.Entity, 0)
	endpoints := make([]config.Endpoint, 0)
	counters := make([]config.Counter, 0)

	// Сортируем таблицы для детерминизма
	tables := append([]datasource.Table{}, schema.Tables...)
	sort.Slice(tables, func(i, j int) bool {
		return tables[i].Name < tables[j].Name
	})

	for _, tbl := range tables {
		if shouldSkip(tbl.Name, skipRules, skipPrefixes) {
			continue
		}

		entity := tableToEntity(tbl)
		entities = append(entities, entity)

		// get_by_id (по entity.IDColumn — реальному PK или fallback'у)
		if entity.IDColumn != "" {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/{%s}", entity.Name, entity.IDColumn),
				Op:          config.OpGetByID,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns %s by identifier", entity.Name),
			})
		}

		// find (по name-полю) — поиск по тексту + фильтры по всем полям
		if searchCol, ok := findSearchField(tbl.Columns); ok {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpFind,
				Entity:      entity.Name,
				SearchField: searchCol.Name,
				QueryParam:  searchCol.Name,
				Description: fmt.Sprintf("Searches %s by name. Returns all records when no query given.", entity.Name),
				Params:      buildFilterParams(tbl.Columns, entity, searchCol.Name),
			})
		} else if entity.IDColumn != "" {
			// Нет name-поля — list как fallback (чтобы агент мог получить все записи)
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpList,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns all %s. Use filters to narrow results.", entity.Name),
				Params:      buildFilterParams(tbl.Columns, entity, ""),
			})
		}

		// distinct endpoint — возвращает уникальные значения enum-колонок
		// (status, type, city и т.д.), чтобы агент знал допустимые значения.
		enumCols := findEnumColumns(tbl.Columns, entity)
		if len(enumCols) > 0 {
			params := make([]config.EndpointParam, 0, len(enumCols))
			params = append(params, config.EndpointParam{
				Name:     "column",
				In:       config.ParamInQuery,
				Type:     config.ParamTypeString,
				Required: boolPtr(true),
				Description: fmt.Sprintf(
					"Column name to get distinct values from. "+
						"Available columns: %s", strings.Join(enumCols, ", ")),
			})
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s/distinct", entity.Name),
				Op:          config.OpDistinct,
				Entity:      entity.Name,
				Description: fmt.Sprintf("Returns unique values for enum columns in %s", entity.Name),
				Params:      params,
			})
		}

		// count endpoint — возвращает количество записей с фильтрами
		countParams := buildFilterParams(tbl.Columns, entity, "")
		endpoints = append(endpoints, config.Endpoint{
			Method:      config.MethodGET,
			Path:        fmt.Sprintf("/%s/count", entity.Name),
			Op:          config.OpCount,
			Entity:      entity.Name,
			Description: fmt.Sprintf("Counts %s records matching filters", entity.Name),
			Params:      countParams,
		})

		// stats — Counter.Name тоже должен пройти regex ^[a-z][a-z0-9_]*$
		// (JSON Schema строже чем Entity.Name? нет — оба проверяются).
		// Используем "короткое" имя (без schema prefix).
		counters = append(counters, config.Counter{
			Name:   entity.Name,
			Entity: entity.Name,
		})
	}

	// ── Phase 2: Auto-generate Navigation Endpoints from FK Relations ──
	//
	// FK relations уже заполнены в tableToEntity (Phase 1).
	// Для каждого FK генерируем:
	//   1. CustomQuery: SELECT * FROM child_table WHERE fk = ?
	//   2. Endpoint: GET /parent/{id}/child (custom_query)
	//   3. MCP tool автоматически через GenerateMCPTools.
	//
	// Реверс-направление (child.filter=fk_value) уже покрыто фильтрами
	// из buildFilterParams — это отдельный сценарий использования.
	customQueries := make(map[string]config.CustomQuery)
	for _, entity := range entities {
		for _, rel := range entity.Relations {
			// rel.Table — parent таблица (куда ссылается FK)
			// rel.LocalFK — колонка FK в текущей (child) таблице
			// rel.Kind = many_to_one: child.fk → parent.id
			//
			// Navigation endpoint: GET /parent/{id}/child_table
			// "Show me all children for a given parent"

			// Находим parent entity по имени таблицы
			var parentEntity *config.Entity
			for j := range entities {
				if entities[j].Table == rel.Table || entities[j].Name == rel.Table {
					parentEntity = &entities[j]
					break
				}
			}
			if parentEntity == nil {
				continue
			}

			// ID колонка parent'а для {id} в URL
			parentID := parentEntity.IDColumn
			if parentID == "" {
				continue
			}

			// custom_query ID: {child_table}_by_{parent_table}_{fk_column}
			// Include FK column name to avoid collision when two FKs in the
			// same table point to the same parent (e.g. buyer_id + seller_id → users).
			queryID := fmt.Sprintf("%s_by_%s_%s", entity.Name, parentEntity.Name, rel.LocalFK)
			if _, exists := customQueries[queryID]; exists {
				continue
			}

			// SELECT * FROM child_table WHERE fk = ?
			customQueries[queryID] = config.CustomQuery{
				SQL:         fmt.Sprintf("SELECT t.* FROM %s t WHERE t.%s = ?", entity.Table, rel.LocalFK),
				Params:      []string{rel.LocalFK},
				MaxRows:     1000,
				Description: fmt.Sprintf("All %s linked to a %s", entity.Name, parentEntity.Name),
			}

			// Navigation endpoint: GET /parent/{id}/child
			navPath := fmt.Sprintf("/%s/{%s}/%s", parentEntity.Name, parentID, entity.Name)
			// Проверяем дубликат
			dup := false
			for _, ep := range endpoints {
				if ep.Path == navPath && ep.Op == config.OpCustomQuery {
					dup = true
					break
				}
			}
			if !dup {
				required := true
				endpoints = append(endpoints, config.Endpoint{
					Method:      config.MethodGET,
					Path:        navPath,
					Op:          config.OpCustomQuery,
					QueryID:     queryID,
					Entity:      entity.Name,
					Description: fmt.Sprintf("All %s for a given %s", entity.Name, parentEntity.Name),
					Params: []config.EndpointParam{
						{
							Name:     parentID,
							In:       config.ParamInPath,
							Type:     config.ParamTypeString,
							Required: &required,
							Description: fmt.Sprintf("ID of %s", parentEntity.Name),
						},
					},
				})
			}
		}
	}

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

	cfg.Entities = entities
	cfg.Endpoints = endpoints
	cfg.Stats = &config.StatsConfig{Counters: counters}

	// Привязываем сгенерированные custom queries к конфигу
	if len(customQueries) > 0 {
		cfg.CustomQueries = customQueries
	}

	// Generate MCP Tools from endpoints — с разговорными описаниями для LLM.
	cfg.MCPTools = GenerateMCPTools(endpoints, entities)

	return cfg
}

// tableToEntity конвертирует datasource.Table → config.Entity.
//
// Name в config.Entity должен проходить regex ^[a-z][a-z0-9_]*$ (JSON Schema),
// поэтому для многосхемных БД (Postgres: "public.customers") используем
// только последний сегмент (без префикса схемы). Table в config.Entity
// хранит полное имя — QueryBuilder использует его для SQL.
// Если у таблицы нет PRIMARY KEY (миграционные таблицы реальной prod-БД),
// id_column берётся как первая колонка — иначе JSON-Schema реджектит пустую.
func tableToEntity(tbl datasource.Table) config.Entity {
	shortName := tbl.Name
	if idx := strings.LastIndex(shortName, "."); idx >= 0 {
		shortName = shortName[idx+1:]
	}

	fields := make([]config.EntityField, 0, len(tbl.Columns))
	pkSet := make(map[string]bool, len(tbl.PrimaryKey))
	for _, pk := range tbl.PrimaryKey {
		pkSet[pk] = true
	}

	colNames := make([]string, 0, len(tbl.Columns))
	for _, col := range tbl.Columns {
		nullable := col.Nullable
		isPK := pkSet[col.Name]
		fields = append(fields, config.EntityField{
			Name:        col.Name,
			Column:      col.Name,
			Type:        config.FieldType(col.Type),
			Nullable:    &nullable,
			PrimaryKey:  &isPK,
			Description: col.Description,
		})
		colNames = append(colNames, col.Name)
	}

	idCol := firstPK(tbl.PrimaryKey)
	if idCol == "" && len(colNames) > 0 {
		idCol = colNames[0]
	}

	// Auto-generate Relations из ForeignKeys.
	// Каждый FK-constraint с одной колонкой → Relation (many_to_one).
	relations := make([]config.Relation, 0, len(tbl.ForeignKeys))
	for _, fk := range tbl.ForeignKeys {
		if len(fk.Columns) != 1 || len(fk.ReferencedColumns) != 1 {
			continue // composite FK пока пропускаем
		}
		targetTable := fk.ReferencedTable
		if idx := strings.LastIndex(targetTable, "."); idx >= 0 {
			targetTable = targetTable[idx+1:]
		}
		relations = append(relations, config.Relation{
			Field:   fk.Columns[0],
			Kind:    config.RelationManyToOne,
			Table:   targetTable,
			LocalFK: fk.Columns[0],
		})
	}

	return config.Entity{
		Name:      shortName,
		Table:     tbl.Name,
		IDColumn:  idCol,
		Fields:    fields,
		Relations: relations,
	}
}

// firstPK возвращает первую PK-колонку или пустую строку.
func firstPK(pk []string) string {
	if len(pk) > 0 {
		return pk[0]
	}
	return ""
}

// shouldSkip checks if a table name matches any skip rule.
// If skipRules is provided, uses structured SkipRule matching.
// Otherwise falls back to legacy prefix-only matching.
func shouldSkip(name string, skipRules []SkipRule, legacyPrefixes []string) bool {
	// For schema-qualified names (e.g. "public.auth_group"),
	// match against both the full name and the short name (after last dot).
	shortName := name
	if idx := strings.LastIndex(name, "."); idx >= 0 {
		shortName = name[idx+1:]
	}

	for _, rule := range skipRules {
		if rule.matches(name) || rule.matches(shortName) {
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

// fieldTypeToParamType конвертирует generic тип колонки в ParamType для MCP-параметров.
func fieldTypeToParamType(col datasource.Column) config.ParamType {
	switch col.Type {
	case datasource.TypeInt:
		return config.ParamTypeInt
	case datasource.TypeFloat:
		return config.ParamTypeFloat
	case datasource.TypeBool:
		return config.ParamTypeBool
	default:
		return config.ParamTypeString
	}
}

// buildFilterParams создаёт параметры фильтрации для find/list endpoints.
// Генерирует query-параметры для ВСЕХ колонок (кроме PK), чтобы агент мог
// фильтровать по любому полю, а не только по name/title.
//
// Для string-колонок: text search (LIKE)
// Для int/float-колонок: exact match
// Для bool-колонок: exact match
//
// searchCol — имя колонки для основного поиска (если есть). Она получает
// приоритетное описание "search by name". Остальные колонки — "filter by exact match".
func buildFilterParams(cols []datasource.Column, entity config.Entity, searchCol string) []config.EndpointParam {
	params := make([]config.EndpointParam, 0)

	for _, col := range cols {
		// Пропускаем PK — по нему есть get_by_id
		isPK := false
		for _, f := range entity.Fields {
			if f.Name == col.Name && f.PrimaryKey != nil && *f.PrimaryKey {
				isPK = true
				break
			}
		}
		if isPK {
			continue
		}

		paramRequired := false

		if col.Name == searchCol {
			// Search field — text LIKE search
			params = append(params, config.EndpointParam{
				Name:     col.Name,
				In:       config.ParamInQuery,
				Type:     config.ParamTypeString,
				Required: &paramRequired,
				Description: fmt.Sprintf(
					"Text search on '%s' (partial match).", col.Name),
			})
		} else if col.Type == datasource.TypeInt || col.Type == datasource.TypeFloat {
			// Numeric columns — exact match
			params = append(params, config.EndpointParam{
				Name:     col.Name,
				In:       config.ParamInQuery,
				Type:     fieldTypeToParamType(col),
				Required: &paramRequired,
				Description: fmt.Sprintf(
					"Filter by exact '%s' value.", col.Name),
			})
		} else if col.Type == datasource.TypeBool {
			// Boolean columns — exact match (true/false)
			params = append(params, config.EndpointParam{
				Name:     col.Name,
				In:       config.ParamInQuery,
				Type:     config.ParamTypeBool,
				Required: &paramRequired,
				Description: fmt.Sprintf(
					"Filter by '%s' (true/false).", col.Name),
			})
		} else if col.Type == datasource.TypeDatetime || col.Type == datasource.TypeDate {
			// Date/datetime — ISO-8601 text comparison
			params = append(params, config.EndpointParam{
				Name:     col.Name,
				In:       config.ParamInQuery,
				Type:     config.ParamTypeString,
				Required: &paramRequired,
				Description: fmt.Sprintf(
					"Filter by '%s' (ISO-8601 date, e.g. 2024-01-15).", col.Name),
			})
		} else if col.Type == datasource.TypeJSON {
			// JSON/JSONB — cannot use ILIKE/LIKE, skip as filter param
			continue
		} else if col.Type == datasource.TypeString {
			// String columns — exact match (FK, email, phone etc.)
			params = append(params, config.EndpointParam{
				Name:     col.Name,
				In:       config.ParamInQuery,
				Type:     config.ParamTypeString,
				Required: &paramRequired,
				Description: fmt.Sprintf(
					"Filter by exact '%s' value.", col.Name),
			})
		}
	}

	// Pagination params
	paginationRequired := false
	params = append(params, config.EndpointParam{
		Name:        "limit",
		In:          config.ParamInQuery,
		Type:        config.ParamTypeInt,
		Required:    &paginationRequired,
		Description: "Max records to return (default 100, max 1000).",
	})
	params = append(params, config.EndpointParam{
		Name:        "offset",
		In:          config.ParamInQuery,
		Type:        config.ParamTypeInt,
		Required:    &paginationRequired,
		Description: "Number of records to skip (for pagination).",
	})

	return params
}

// findEnumColumns ищет колонки, которые вероятно являются enum-полями.
// Возвращает имена колонок, которые являются строковыми и содержат
// типичные для enum суффиксы (status, type, role, city, country).
func findEnumColumns(cols []datasource.Column, entity config.Entity) []string {
	var enums []string
	for _, col := range cols {
		// Пропускаем PK
		isPK := false
		for _, f := range entity.Fields {
			if f.Name == col.Name && f.PrimaryKey != nil && *f.PrimaryKey {
				isPK = true
				break
			}
		}
		if isPK {
			continue
		}
		if col.Type != datasource.TypeString {
			continue
		}
		lower := strings.ToLower(col.Name)
		switch {
		case strings.Contains(lower, "status"):
			enums = append(enums, col.Name)
		case strings.Contains(lower, "type"):
			enums = append(enums, col.Name)
		case strings.Contains(lower, "role"):
			enums = append(enums, col.Name)
		case strings.Contains(lower, "city"):
			enums = append(enums, col.Name)
		case strings.Contains(lower, "country"):
			enums = append(enums, col.Name)
		}
	}
	return enums
}

func boolPtr(v bool) *bool {
	return &v
}

// ── LLM-friendly schema ────────────────────────────────────────────────────

// SchemaForLLM — обселиченное описание схемы для LLM-агента.
// Не содержит raw SQL, только семантические типы и связи.
type SchemaForLLM struct {
	// Entities — список сущностей, доступных агенту. Каждая сущность — это
	// таблица, но абстрагированная через бизнес-имя и семантические типы.
	Entities []LLMEntity `json:"entities"`

	// WorkflowHints — стратегические подсказки агенту: как искать,
	// какие связи использовать, какие тулы вызывать.
	WorkflowHints []string `json:"workflow_hints,omitempty"`
}

// LLMEntity — описание одной сущности для LLM.
type LLMEntity struct {
	// Name — бизнес-имя сущности ("Товар (catalog_product)").
	Name string `json:"name"`

	// ToolPrefix — префикс для тулов, ссылающихся на эту сущность ("catalog_product").
	// Нужен для построения правильных ссылок на find_*, get_*, list_*.
	ToolPrefix string `json:"-"`

	// Description — комментарий из БД либо авто-описание.
	Description string `json:"description,omitempty"`

	// SearchFields — поля, по которым работает нечёткий поиск (ILIKE/LIKE).
	// Агент может передавать текст в name-параметр find_* тула.
	SearchFields string `json:"search_fields,omitempty"`

	// FilterFields — поля для точной фильтрации, сгруппированные по типу.
	FilterFields []FilterGroup `json:"filter_fields,omitempty"`

	// Relations — связи с другими сущностями (FK).
	Relations []LLMRelation `json:"relations,omitempty"`
}

// FilterGroup — группа фильтров одного типа.
type FilterGroup struct {
	// Label — "exact" / "bool" / "range" / "text search" / "enum".
	Label string `json:"label"`

	// Fields — список колонок с описанием.
	Fields []FilterField `json:"fields"`
}

// FilterField — одна колонка-фильтр.
type FilterField struct {
	Name        string `json:"name"`
	Column      string `json:"column"`      // оригинальное имя в БД (snake_case)
	Type        string `json:"type"`         // string/int/float/bool/date/enum
	Description string `json:"description,omitempty"`
	IsFK        bool   `json:"is_fk,omitempty"`   // true если это внешний ключ
	FKEntity    string `json:"fk_entity,omitempty"` // имя сущности, на которую ссылается FK
}

// LLMRelation — связь с другой сущностью.
type LLMRelation struct {
	// Field — колонка в текущей таблице (FK).
	Field string `json:"field"`

	// ReferencedEntity — имя связанной сущности.
	ReferencedEntity string `json:"referenced_entity"`

	// ReferencedTool — тул для навигации к связанным данным.
	ReferencedTool string `json:"referenced_tool,omitempty"`
}

// GenerateSchemaForLLM превращает datasource.Schema в обселиченное
// описание для LLM-агента. Никакого raw SQL.
//
// cfg — сгенерированный config.Config (нужен для entities, endpoints, FK).
func GenerateSchemaForLLM(schema *datasource.Schema, cfg *config.Config) *SchemaForLLM {
	if schema == nil {
		return &SchemaForLLM{Entities: []LLMEntity{}}
	}

	// Build entity map from config (shortName -> Entity)
	entityMap := make(map[string]config.Entity)
	for _, e := range cfg.Entities {
		entityMap[e.Name] = e
	}

	// Build table -> entity name map
	tableToEntity := make(map[string]string)
	for _, e := range cfg.Entities {
		short := e.Name
		full := e.Table
		tableToEntity[full] = short
		// Also index by short name
		tableToEntity[short] = short
	}

	// Build FK index: (tableName, column) -> referenced table
	fkIndex := make(map[[2]string]string) // key: (table, column) -> referencedTable
	for _, tbl := range schema.Tables {
		for _, fk := range tbl.ForeignKeys {
			for i, col := range fk.Columns {
				if i < len(fk.ReferencedColumns) {
					fkIndex[[2]string{tbl.Name, col}] = fk.ReferencedTable
				}
			}
		}
	}

	// Build entity -> relation index from config.Relation
	entityRelations := make(map[string][]config.Relation)
	for _, e := range cfg.Entities {
		if len(e.Relations) > 0 {
			entityRelations[e.Name] = append(entityRelations[e.Name], e.Relations...)
		}
	}

	entities := make([]LLMEntity, 0, len(cfg.Entities))
	hints := []string{}
	hintSet := make(map[string]bool)

	for _, e := range cfg.Entities {
		// Find the original datasource.Table for this entity
		var tbl *datasource.Table
		for i := range schema.Tables {
			stripped := schema.Tables[i].Name
			if idx := strings.LastIndex(stripped, "."); idx >= 0 {
				stripped = stripped[idx+1:]
			}
			if stripped == e.Name || schema.Tables[i].Name == e.Table {
				tbl = &schema.Tables[i]
				break
			}
		}
		if tbl == nil {
			continue
		}

		// Build name and description
		businessName := shortBusinessName(e.Name)
		displayName := fmt.Sprintf("%s (%s)", businessName, e.Name)

		desc := e.Description
		if desc == "" {
			desc = fmt.Sprintf("Таблица %s", e.Name)
		}

		// Search fields
		var searchFields []string
		for _, ep := range cfg.Endpoints {
			if ep.Entity == e.Name && ep.SearchField != "" {
				searchFields = append(searchFields, ep.SearchField)
			}
		}

		// Filter fields — group by type
		exactFields := make([]FilterField, 0)
		boolFields := make([]FilterField, 0)
		rangeFields := make([]FilterField, 0)

		for _, f := range e.Fields {
			isPK := f.PrimaryKey != nil && *f.PrimaryKey
			if isPK {
				continue
			}

			// Check FK
			fkRef := fkIndex[[2]string{tbl.Name, f.Column}]
			if fkRef == "" {
				// Also try short table name
				short := tbl.Name
				if idx := strings.LastIndex(short, "."); idx >= 0 {
					short = short[idx+1:]
				}
				fkRef = fkIndex[[2]string{short, f.Column}]
				if fkRef == "" {
					fkRef = fkIndex[[2]string{e.Name, f.Column}]
				}
			}

			// Resolve FK entity name
			fkEntity := ""
			if fkRef != "" {
				if refShort := tableToEntity[fkRef]; refShort != "" {
					fkEntity = shortBusinessName(refShort)
				} else {
					short := fkRef
					if idx := strings.LastIndex(short, "."); idx >= 0 {
						short = short[idx+1:]
					}
					fkEntity = shortBusinessName(short)
				}
			}

			// Check if search field (already handled above, skip from filters)
			isSearch := false
			for _, sf := range searchFields {
				if sf == f.Column || sf == f.Name {
					isSearch = true
					break
				}
			}
			if isSearch {
				continue
			}

			fieldDesc := f.Description
			if fkEntity != "" {
				if fieldDesc != "" {
					fieldDesc += " | "
				}
				fieldDesc += fmt.Sprintf("FK → %s (используй поиск по %s)", fkEntity, fkEntity)
			}

			ff := FilterField{
				Name:        shortColumnName(f.Name),
				Column:      f.Column,
				Type:        string(f.Type),
				Description: fieldDesc,
				IsFK:        fkRef != "",
				FKEntity:    fkEntity,
			}

			switch f.Type {
			case config.FieldTypeBool:
				boolFields = append(boolFields, ff)
			case config.FieldTypeInt, config.FieldTypeFloat:
				rangeFields = append(rangeFields, ff)
			default:
				exactFields = append(exactFields, ff)
			}
		}

		// Relations from config
		relations := make([]LLMRelation, 0)
		for _, rel := range entityRelations[e.Name] {
			targetName := rel.Table
			if targetShort := tableToEntity[rel.Table]; targetShort != "" {
				targetName = targetShort
			}
			relations = append(relations, LLMRelation{
				Field:            rel.LocalFK,
				ReferencedEntity: shortBusinessName(targetName),
			})
		}

		// Build filter groups
		filterFields := make([]FilterGroup, 0)
		if len(boolFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "bool", Fields: boolFields})
		}
		if len(rangeFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "range", Fields: rangeFields})
		}
		if len(exactFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "exact", Fields: exactFields})
		}

		entities = append(entities, LLMEntity{
			Name:         displayName,
			ToolPrefix:   e.Name, // e.g. "catalog_product"
			Description:  desc,
			SearchFields: strings.Join(searchFields, ", "),
			FilterFields: filterFields,
			Relations:    relations,
		})
	}

	// Generate workflow hints
	hintKey := func(h string) string {
		return strings.ToLower(strings.TrimSpace(h))
	}

	// Check for category-like entities (тип детали, не производитель)
	hasCategory := false
	hasBrand := false
	for _, e := range entities {
		low := strings.ToLower(e.Name)
		if strings.Contains(low, "категори") || strings.Contains(low, "category") {
			hasCategory = true
		}
		if strings.Contains(low, "бренд") || strings.Contains(low, "brand") {
			hasBrand = true
		}
	}

	if hasCategory && hasBrand {
		h := "Категории = тип детали (тормозные колодки, амортизаторы). Бренды = производитель (Bosch, KYB, TRW). Сначала ищи категорию, потом — товары через products_by_category."
		if !hintSet[hintKey(h)] {
			hints = append(hints, h)
			hintSet[hintKey(h)] = true
		}
	} else if hasCategory {
		h := "Категории = тип детали. Сначала ищи категорию, потом — товары через products_by_category."
		if !hintSet[hintKey(h)] {
			hints = append(hints, h)
			hintSet[hintKey(h)] = true
		}
	}

	// car_applicability — JSONB, ILIKE не работает. Хинт убран до появления
	// поддержки JSONB-фильтрации в find-эндпоинте.
	// TODO: добавить обратно когда data-service научится фильтровать по JSONB.

	return &SchemaForLLM{
		Entities:      entities,
		WorkflowHints: hints,
	}
}

// shortBusinessName отрезает префикс (catalog_, auth_, django_) и
// возвращает читаемое имя.
func shortBusinessName(name string) string {
	for _, pfx := range DisplayPrefixes {
		if strings.HasPrefix(name, pfx) {
			result := strings.TrimPrefix(name, pfx)
			if result == "cartitem" {
				return "Cart item"
			}
			if result == "sitesettings" {
				return "Settings"
			}
			return titleCase(result)
		}
	}
	return titleCase(name)
}

// titleCase capitalises the first letter of an ASCII string.
func titleCase(s string) string {
	if s == "" {
		return ""
	}
	return strings.ToUpper(s[:1]) + s[1:]
}


// shortColumnName делает snake_case колонку читаемой для LLM.
func shortColumnName(name string) string {
	// Простейшее преобразование: _ → пробел
	result := strings.ReplaceAll(name, "_", " ")
	// Если выглядит как FK (_id), подчёркиваем
	if strings.HasSuffix(name, "_id") {
		result = strings.TrimSuffix(result, " id") + " ID"
	}
	return result
}

// ── Pluralization ───────────────────────────────────────────────────────────

// pluralizeEntity returns the English plural form of an entity name.
func pluralizeEntity(name string) string {
	special := map[string]string{
		"product":      "products",
		"brand":        "brands",
		"category":     "categories",
		"order":        "orders",
		"cart":         "cart",
		"cartitem":     "cart_items",
		"sitesettings": "settings",
		"user":         "users",
		"group":        "groups",
	}
	if p, ok := special[name]; ok {
		return p
	}
	short := name
	for _, prefix := range DisplayPrefixes {
		if strings.HasPrefix(short, prefix) {
			short = strings.TrimPrefix(short, prefix)
			break
		}
	}
	if p, ok := special[short]; ok {
		return p
	}
	if strings.HasSuffix(short, "s") {
		return short
	}
	if strings.HasSuffix(short, "y") {
		return short[:len(short)-1] + "ies"
	}
	return short + "s"
}

// toolDisplayName generates a human-readable English display name for a tool.
func toolDisplayName(op, entityName string) string {
	short := entityName
	for _, prefix := range DisplayPrefixes {
		if strings.HasPrefix(short, prefix) {
			short = strings.TrimPrefix(short, prefix)
			break
		}
	}
	plural := pluralizeEntity(entityName)
	switch op {
	case string(config.OpGetByID):
		return fmt.Sprintf("%s by ID", short)
	case string(config.OpFind):
		return fmt.Sprintf("Find %s", short)
	case string(config.OpList):
		return fmt.Sprintf("All %s", plural)
	case string(config.OpCount):
		return fmt.Sprintf("Count %s", plural)
	case string(config.OpDistinct):
		return fmt.Sprintf("Distinct %s", plural)
	default:
		return ""
	}
}

// compactFilterSummary builds a grouped description of filter fields.
// Groups: search (partial), exact (string/int/float), bool (true/false).
func compactFilterSummary(ent *config.Entity) string {
	if ent == nil || len(ent.Fields) == 0 {
		return ""
	}
	var searchFields, exactFields, boolFields []string
	for _, f := range ent.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Name == "limit" || f.Name == "offset" {
			continue
		}
		lower := f.Name
		isSearch := lower == "name" || strings.HasSuffix(lower, "_name") || strings.HasSuffix(lower, "_title") || strings.HasPrefix(lower, "name")
		if isSearch && f.Type == config.FieldTypeString {
			searchFields = append(searchFields, lower)
		} else if f.Type == config.FieldTypeBool {
			boolFields = append(boolFields, lower)
		} else {
			exactFields = append(exactFields, lower)
		}
	}
	var parts []string
	if len(searchFields) > 0 {
		parts = append(parts, fmt.Sprintf("partial match on '%s'", strings.Join(searchFields, ", ")))
	}
	if len(exactFields) > 0 {
		show := exactFields
		if len(show) > 3 {
			show = show[:3]
			show = append(show, fmt.Sprintf("+%d more", len(exactFields)-3))
		}
		parts = append(parts, fmt.Sprintf("exact: %s", strings.Join(show, ", ")))
	}
	if len(boolFields) > 0 {
		parts = append(parts, fmt.Sprintf("bool: %s", strings.Join(boolFields, ", ")))
	}
	return strings.Join(parts, "; ")
}

// GenerateMCPTools creates compact MCP tools from endpoints with LLM-friendly descriptions.
// Descriptions are kept under ~100 chars. Field dumps and relation hints are removed.
// DisplayName provides human-readable Russian names for the admin UI.
func GenerateMCPTools(endpoints []config.Endpoint, entities []config.Entity) []config.MCPTool {
	entityMap := make(map[string]*config.Entity, len(entities))
	for i := range entities {
		entityMap[entities[i].Name] = &entities[i]
	}

	tools := make([]config.MCPTool, 0, len(endpoints))
	for _, ep := range endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			continue
		}

		var toolName, desc, displayName string
		ent := entityMap[ep.Entity]

		switch ep.Op {
		case config.OpGetByID:
			toolName = fmt.Sprintf("get_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get a single %s by its ID. "+
					"Use after find_%s when you have a specific ID.",
				ep.Entity, ep.Entity)
			displayName = toolDisplayName(string(config.OpGetByID), ep.Entity)

		case config.OpFind:
			toolName = fmt.Sprintf("find_%s", ep.Entity)
			filters := compactFilterSummary(ent)
			if filters != "" {
				desc = fmt.Sprintf(
					"Search %s by name (partial match). Filters: %s. "+
						"If user asks about a type (e.g. 'muffler', 'brake pads'), "+
						"search categories first, then navigate to products.",
					pluralizeEntity(ep.Entity), filters)
			} else {
				desc = fmt.Sprintf(
					"Search %s by text query. Example: find_%s(name='query')",
					pluralizeEntity(ep.Entity), ep.Entity)
			}
			displayName = toolDisplayName(string(config.OpFind), ep.Entity)

		case config.OpList:
			toolName = fmt.Sprintf("list_%s", ep.Entity)
			desc = fmt.Sprintf(
				"List all %s. Supports filters and pagination. "+
					"Use when find_%s returns no results or you need all records.",
				pluralizeEntity(ep.Entity), ep.Entity)
			displayName = toolDisplayName(string(config.OpList), ep.Entity)

		case config.OpDistinct:
			toolName = fmt.Sprintf("distinct_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get unique values for enum columns in %s. "+
					"Use to discover valid filter values.",
				pluralizeEntity(ep.Entity))
			displayName = toolDisplayName(string(config.OpDistinct), ep.Entity)

		case config.OpCount:
			toolName = fmt.Sprintf("count_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Count %s matching filters. Returns {entity, count}.",
				pluralizeEntity(ep.Entity))
			displayName = toolDisplayName(string(config.OpCount), ep.Entity)

		case config.OpCustomQuery:
			// Short name: {child_plural}_by_{parent} (e.g. "products_by_brand")
			pathParts := strings.Split(strings.Trim(ep.Path, "/"), "/")
			parentName := ""
			if len(pathParts) >= 1 {
				parentName = pathParts[0]
			}
			if parentName == "" {
				parts := strings.Split(ep.QueryID, "_by_")
				if len(parts) == 2 {
					parentName = parts[1]
				}
			}
			childShort := ep.Entity
			for _, pfx := range DisplayPrefixes {
				childShort = strings.TrimPrefix(childShort, pfx)
			}
			parentShort := parentName
			for _, pfx := range DisplayPrefixes {
				parentShort = strings.TrimPrefix(parentShort, pfx)
			}
			toolName = fmt.Sprintf("%s_by_%s", pluralizeEntity(ep.Entity), parentShort)
			displayName = fmt.Sprintf("%s by %s", pluralizeEntity(ep.Entity), parentShort)

			// Strategic description: guides LLM workflow
			desc = fmt.Sprintf(
				"Get all %s for a given %s. "+
					"Use after find_%s to get the ID, then call this to list related %s. "+
					"Supports filters and pagination.",
				pluralizeEntity(ep.Entity), parentShort,
				parentName, pluralizeEntity(ep.Entity))
		}

		if toolName != "" {
			params := deriveToolParams(ep)
			tools = append(tools, config.MCPTool{
				Name:        toolName,
				DisplayName: displayName,
				Endpoint:    ep.Path,
				Description: desc,
				Params:      params,
			})
		}
	}
	return tools
}

// deriveToolParams извлекает параметры инструмента из структуры endpoint'а.
// Если endpoint имеет явные Params (из configgen), используем их.
// Иначе — auto-generate из path params + search field.
func deriveToolParams(ep config.Endpoint) []config.EndpointParam {
	// Если endpoint уже имеет Params (из configgen.buildFilterParams) — используем их
	if len(ep.Params) > 0 {
		return ep.Params
	}

	params := make([]config.EndpointParam, 0)

	// 1. Path params из {param} в URL
	pathParams := extractPathParams(ep.Path)
	for _, pp := range pathParams {
		required := true
		params = append(params, config.EndpointParam{
			Name:        pp,
			In:          config.ParamInPath,
			Type:        config.ParamTypeString,
			Required:    &required,
			Description: fmt.Sprintf("Unique identifier for %s", ep.Entity),
		})
	}

	// 2. Query param для поиска (find/list)
	if ep.Op == config.OpFind || ep.Op == config.OpList {
		qp := ep.QueryParam
		if qp == "" {
			qp = ep.SearchField
		}
		if qp != "" {
			required := false
			desc := fmt.Sprintf("Text query to search %s by field '%s'. If omitted, returns all records.",
				ep.Entity, ep.SearchField)
			params = append(params, config.EndpointParam{
				Name:        qp,
				In:          config.ParamInQuery,
				Type:        config.ParamTypeString,
				Required:    &required,
				Description: desc,
			})
		}
	}

	return params
}

// extractPathParams извлекает {param_name} из URL-паттерна.
func extractPathParams(path string) []string {
	params := make([]string, 0)
	for {
		start := strings.Index(path, "{")
		if start < 0 {
			break
		}
		end := strings.Index(path[start:], "}")
		if end < 0 {
			break
		}
		params = append(params, path[start+1:start+end])
		path = path[start+end+1:]
	}
	return params
}
