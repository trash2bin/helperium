package configgen

import (
	"fmt"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// GenerateMCPTools creates compact MCP tools from endpoints with LLM-friendly descriptions.
// filterableRules/searchableRules — resolved FieldRules для стратегий (явные слайсы,
// т.к. Go не позволяет два variadic в одной сигнатуре).
//
// Фаза 2 (консолидация) + Фаза 2.5 (деконсолидация filter):
//   - N пер-энтити filter_{entity} (поля в схеме тула) — живой REST /{entity}/filter
//   - 5 консолидированных db_* (db_map, db_describe, db_search, db_get, db_related)
//     через /q/* диспетчер.
//
// Остальные per-entity тулы (grep_*, schema_*) не эмитятся.
//
// LLMToolPolicy (opt-in): если ExposeGetByID/Count/Distinct=true, в ДОПОЛНЕНИЕ
// эмитятся per-entity get_*/count_*/distinct_* (для клиентов, которым нужны).
// REST-эндпоинты /{entity}/get|count|distinct остаются всегда.
func GenerateMCPTools(endpoints []config.Endpoint, entities []config.Entity, displayPrefixes []string, customPlurals map[string]string, filterableRules []config.FieldRule, searchableRules []config.FieldRule, policy config.LLMToolPolicy) []config.MCPTool {
	// Detect whether filter tools will be emitted. Descriptions must match the
	// actual tool set; stale filter_<entity> references cause TOOL_NOT_FOUND.
	hasFilterTools := false
	for _, ep := range endpoints {
		if ep.Strategy == "filter" && ep.Entity != "" {
			hasFilterTools = true
			break
		}
	}

	// ── Консолидированные тулы (Фаза 2, деконсолидация filter в 2.5-smoke):
	// db_map/db_describe/db_search/db_filter/db_get/db_related — через /q/*.
	// Фаза 2.5 деконсолидировала filter в per-entity filter_{entity}, но это
	// требовало strategy=filter в конфиге tenant'а. Для конфигов без filter-
	// endpoints (introspected schema) модель остаётся без инструмента фильтрации:
	// db_search — текстовый, db_get — по id, db_related — FK. Поэтому db_filter
	// возвращён как консолидированный тул на /q/filter (роут существовал всегда).
	// Динамические field__op параметры (price__lt=2000) не объявляются в схеме,
	// но validateArgs их игнорирует, а httpclient.Call передаёт 1:1 в query.
	tools := GenerateConsolidatedMCPTools(displayPrefixes, customPlurals, hasFilterTools)

	entityMap := make(map[string]*config.Entity, len(entities))
	for i := range entities {
		entityMap[entities[i].Name] = &entities[i]
	}

	// ── Пер-энтити filter_{entity} (Фаза 2.5 smoke-фикс) ────────────────
	// Смоук с живой моделью показал: тупая модель не делает db_map→db_describe→
	// filter_{entity}, а лезет в filter сразу. Имена полей ей нужны ПРЯМО в схеме тула
	// (топ-левел properties), а консолидированный db_filter с entity-параметром
	// не может их дать (схема статична). Поэтому filter — пер-энтити:
	//   filter_{entity} с полями через FilterStrategy.ToolParams() (см. filter.go:86).
	// Указывает на живой REST-роут /{entity}/filter (tenant-безопасный).
	// Эмитится ТОЛЬКО если endpoints содержит strategy=filter — иначе схема тула
	// раздувается (57 параметров catalog_product) и превышает AGENT_MAX_TURN_TOKENS.
	// Для конфигов без filter-endpoints модель использует консолидированный db_filter
	// (компактный, поля берёт из db_map).
	// M3: строим с resolved-правилами (как endpoint_builder.go:190), иначе
	// ToolParams отдаст дефолтные поля, а не кастомные.
	for _, ep := range endpoints {
		if ep.Strategy == "filter" && ep.Entity != "" {
			ent := entityMap[ep.Entity]
			if ent == nil {
				continue
			}
			tool := strategyToMCPTool("filter", *ent, ep.Path, displayPrefixes, customPlurals, filterableRules, searchableRules)
			if tool != nil {
				tools = append(tools, *tool)
			}
		}
	}

	// Opt-in per-entity тулы по политике (get_*/count_*/distinct_*).
	for _, ep := range endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			continue
		}

		var emit bool
		switch ep.Op {
		case config.OpGetByID:
			emit = policy.ExposeGetByID
		case config.OpCount:
			emit = policy.ExposeCount
		case config.OpDistinct:
			emit = policy.ExposeDistinct
		default:
			// Стратегии и прочее — не эмитим per-entity (консолидированы).
			continue
		}
		if !emit {
			continue
		}

		ent := entityMap[ep.Entity]
		if ent == nil {
			continue
		}

		var toolName, desc string
		switch ep.Op {
		case config.OpGetByID:
			toolName = fmt.Sprintf("get_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get a single %s by its ID. ONLY use with an id you ALREADY got from search/filter. "+
					"NEVER enumerate ids (id=1, id=2, ...) — search first.", ep.Entity)
		case config.OpCount:
			toolName = fmt.Sprintf("count_%s", ep.Entity)
			desc = fmt.Sprintf("Count %s matching filters. Returns {entity, count}.",
				pluralizeEntity(ep.Entity, displayPrefixes, customPlurals))
		case config.OpDistinct:
			toolName = fmt.Sprintf("distinct_%s", ep.Entity)
			desc = fmt.Sprintf("Get unique values for enum columns in %s.", ep.Entity)
		}

		tools = append(tools, config.MCPTool{
			Name:        toolName,
			DisplayName: toolDisplayName(string(ep.Op), ep.Entity, displayPrefixes, customPlurals),
			Endpoint:    ep.Path,
			Description: desc,
			Params:      deriveToolParams(ep),
		})
	}

	return tools
}

// GenerateConsolidatedMCPTools возвращает 6 константных LLM-тулов,
// указывающих на /q/* диспетчер. Число тулов НЕ зависит от числа сущностей.
//
// entity — обычный string (не enum): на большой БД enum на сотни значений
// расдул бы манифест и жрал токены на каждый вызов. Допустимые имена модель
// узнаёт из db_map, сервер валидирует через EntityResolver (whitelist).
func GenerateConsolidatedMCPTools(displayPrefixes []string, customPlurals map[string]string, hasFilterTools bool) []config.MCPTool {
	dbMapDesc := "Map of the database: entities, their fields, searchable columns, relations (FK). " +
		"Call FIRST to learn what entities exist and how to query them. " +
		"Also shows which fields are searchable (use in db_search)."
	if hasFilterTools {
		dbMapDesc += " Filterable fields are listed per entity (use filter_<entity> with exact field names from the entity schema)."
	}
	dbMapDesc += " For exact-value or numeric filtering use db_filter on any entity."

	idSource := "db_search"
	if hasFilterTools {
		idSource = "db_search or filter_<entity>"
	}
	dbGetDesc := "Fetch ONE record by its id. " +
		"ONLY use with an id you ALREADY obtained from " + idSource + ". " +
		"NEVER enumerate ids (id=1, id=2, ...) — search or filter first."

	tools := []config.MCPTool{
		{
			Name:        "db_map",
			DisplayName: "Database map",
			Endpoint:    "/q/map",
			Description: dbMapDesc,
		},
		{
			Name:        "db_describe",
			DisplayName: "Describe entity",
			Endpoint:    "/q/describe",
			Description: "Metadata about ONE entity: total count, available values per field, min/max for numeric. " +
				"Use BEFORE searching when unsure about field names or valid values. " +
				"No guessing: see actual values first.",
			Params: []config.EndpointParam{
				{Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Entity name (from db_map, canonical e.g. catalog_product)."},
			},
		},
		{
			Name:        "db_search",
			DisplayName: "Text search",
			Endpoint:    "/q/search",
			Description: "PRIMARY text search across an entity. Search here FIRST instead of guessing ids. " +
				"Finds records by words/phrases in searchable fields (see db_map).",
			Params: []config.EndpointParam{
				{Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Entity name (from db_map, canonical e.g. catalog_product)."},
				{Name: "pattern", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Search query. Example: 'blue widget'."},
				{Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: ptrBool(false),
					Description: "Max results (1-100, default: 10)."},
				{Name: "fields", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(false),
					Description: "Comma-separated field names to search. Default: all searchable fields."},
			},
		},
		{
			Name:        "db_filter",
			DisplayName: "Filter records",
			Endpoint:    "/q/filter",
			Description: "Filter records of an entity by field values or numeric ranges. " +
				"HOW TO CALL: pass field names DIRECTLY as top-level parameters — do NOT wrap them in filter/filters/filter_fields/where objects. " +
				"Field names come from db_map (both display names like 'brand ID' and snake_case like brand_id work). " +
				"Every call needs entity plus at least one field parameter. " +
				"RESULT: every successful response includes total, the authoritative count of matching records — for a count question use total directly (set limit=1).\n" +
				"\n" +
				"Operators (append to the field name with __):\n" +
				"  {field}=value          — exact match (status=new)\n" +
				"  {field}__gt=value      — greater than (price__gt=3000)\n" +
				"  {field}__lt=value      — less than (price__lt=2000)\n" +
				"  {field}__gte=value     — greater than or equal\n" +
				"  {field}__lte=value     — less than or equal\n" +
				"  {field}__in=a,b,c      — IN list (status__in=new,delivered)\n" +
				"  {field}__like=value    — LIKE search (name__like=brake)\n" +
				"  {field}__gt_field=other — compare numeric fields (old_price__gt_field=price)\n" +
				"\n" +
				"Examples:\n" +
				"  entity=catalog_product, price__lt=2000 (correct)\n" +
				"  entity=catalog_product, status=new, limit=1 (correct — count question)\n" +
				"  WRONG: entity=catalog_product, filter={...} — never wrap fields in an object\n" +
				"\n" +
				"WHEN: you have an exact value or numeric range (price, status, availability, id from a previous search).\n" +
				"WHEN NOT: do not guess values — call db_describe first to see valid values.",
			Params: []config.EndpointParam{
				{Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Entity name (from db_map, canonical e.g. catalog_product)."},
				{Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: ptrBool(false),
					Description: "Max results (1-100, default: 10). Use 1 for pure count questions."},
			},
		},
		{
			Name:        "db_get",
			DisplayName: "Get by id",
			Endpoint:    "/q/get",
			Description: dbGetDesc,
			Params: []config.EndpointParam{
				{Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Entity name (from db_map, canonical e.g. catalog_product)."},
				{Name: "id", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Parent record id."},
				{Name: "relation", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(false),
					Description: "FK column name (from db_map relations). Optional if entity has one relation."},
			},
		},
		{
			Name:        "db_related",
			DisplayName: "Related records",
			Endpoint:    "/q/related",
			Description: "Fetch records of an entity linked to a parent by FK (one hop). " +
				"Use to navigate relations shown in db_map (e.g. orders for a customer). " +
				"One query, no JOINs.",
			Params: []config.EndpointParam{
				{Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Entity name (from db_map, canonical e.g. catalog_product)."},
				{Name: "id", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(true),
					Description: "Parent record id."},
				{Name: "relation", In: config.ParamInQuery, Type: config.ParamTypeString, Required: ptrBool(false),
					Description: "FK column name (from db_map relations). Optional if entity has one relation."},
			},
		},
	}
	return tools
}

// ptrBool возвращает указатель на bool.
func ptrBool(b bool) *bool { return &b }

// strategyToMCPTool создаёт MCPTool для strategy-эндпоинта, используя
// методы стратегии для генерации имени, описания и параметров.
// filterableRules/searchableRules — resolved FieldRules (M3: кастомные
// правила доходят до манифеста, а не дефолтные).
func strategyToMCPTool(strategyName string, entity config.Entity, epPath string, displayPrefixes []string, customPlurals map[string]string, filterableRules []config.FieldRule, searchableRules []config.FieldRule) *config.MCPTool {
	idCol := entity.IDColumnOrDefault()
	nameCol := entity.FirstStringFieldColumn()

	var strategy search.Strategy
	switch strategyName {
	case "grep":
		strategy = search.NewGrepStrategy(idCol, nameCol, searchableRules...)
	case "filter":
		strategy = search.NewFilterStrategy(idCol, nameCol, filterableRules...)
	case "schema":
		strategy = search.NewSchemaStrategy(idCol, nameCol)

	default:
		return nil
	}

	displayName := toolDisplayName(strategyName, entity.Name, displayPrefixes, customPlurals)

	return &config.MCPTool{
		Name:        strategy.ToolName(entity),
		DisplayName: displayName,
		Description: strategy.ToolDescription(entity),
		Params:      strategy.ToolParams(entity),
		Endpoint:    epPath,
	}
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
