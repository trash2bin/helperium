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

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/datasource"
)

// skipPrefixes — таблицы, начинающиеся с этих префиксов, исключаются.
// Можно расширять через Generate's skipPrefixes параметр.
var defaultSkipPrefixes = []string{
	"sqlite_",
	"pg_",
	"documents", // внутренняя таблица RAG
}

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

// canFindByID возвращает true, если у таблицы ровно одна PK-колонка.
func canFindByID(pk []string) bool {
	return len(pk) == 1
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
	mergedSkip := append([]string{}, defaultSkipPrefixes...)
	mergedSkip = append(mergedSkip, skipPrefixes...)

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
		if shouldSkip(tbl.Name, mergedSkip) {
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
				Description: fmt.Sprintf("Возвращает %s по идентификатору", entity.Name),
			})
		}

		// find (по name-полю) — он же fallback на список без параметра
		if searchCol, ok := findSearchField(tbl.Columns); ok {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpFind,
				Entity:      entity.Name,
				SearchField: searchCol.Name,
				QueryParam:  searchCol.Name,
				Description: fmt.Sprintf("Ищет %s по имени. Без параметра возвращает все записи.", entity.Name),
			})
		}

		// stats — Counter.Name тоже должен пройти regex ^[a-z][a-z0-9_]*$
		// (JSON Schema строже чем Entity.Name? нет — оба проверяются).
		// Используем "короткое" имя (без schema prefix).
		counters = append(counters, config.Counter{
			Name:   entity.Name,
			Entity: entity.Name,
		})
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

	// Generate MCP Tools from endpoints — с разговорными описаниями для LLM.
	cfg.MCPTools = GenerateMCPTools(endpoints)

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

	return config.Entity{
		Name:     shortName,
		Table:    tbl.Name,
		IDColumn: idCol,
		Fields:   fields,
	}
}

// firstPK возвращает первую PK-колонку или пустую строку.
func firstPK(pk []string) string {
	if len(pk) > 0 {
		return pk[0]
	}
	return ""
}

// shouldSkip проверяет, начинается ли имя с одного из skip-префиксов.
func shouldSkip(name string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

// GenerateMCPTools создаёт MCP-тулы из эндпоинтов с разговорными описаниями для LLM.
// Экспортируемая функция — используется как configgen.Generate, так и
// handlers.MCPManifestHandler для рантайм-генерации без зависимости от дискового конфига.
func GenerateMCPTools(endpoints []config.Endpoint) []config.MCPTool {
	tools := make([]config.MCPTool, 0, len(endpoints))
	for _, ep := range endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			continue
		}

		var toolName, desc string
		switch ep.Op {
		case config.OpGetByID:
			toolName = fmt.Sprintf("get_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Возвращает данные о %s по его уникальному и��ентификатору. "+
					"Используйте, когда уже знаете ID нужной записи (например, из результатов find_%s).",
				ep.Entity, ep.Entity)
		case config.OpFind:
			toolName = fmt.Sprintf("find_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Позволяет найти %s по текстовому запросу (имени, названию). "+
					"Если па��аметр поиска не указан, возвращает полный список всех записей.",
				ep.Entity)
		case config.OpCustomQuery:
			toolName = fmt.Sprintf("query_%s", ep.Path)
			toolName = strings.ReplaceAll(toolName, "{", "")
			toolName = strings.ReplaceAll(toolName, "}", "")
			toolName = strings.ReplaceAll(toolName, "/", "_")
			toolName = strings.TrimPrefix(toolName, "_")
			if ep.Description != "" {
				desc = fmt.Sprintf("Выполняет пользовательский запрос: %s", ep.Description)
			} else {
				desc = fmt.Sprintf("Выполняет пользовательский запрос по пути %s", ep.Path)
			}
		}

		if toolName != "" {
			// Derive params from endpoint structure (path params + query params)
			params := deriveToolParams(ep)
			tools = append(tools, config.MCPTool{
				Name:        toolName,
				Endpoint:    ep.Path,
				Description: desc,
				Params:      params,
			})
		}
	}
	return tools
}

// deriveToolParams извлекает параметры инструмента из структуры endpoint'а.
func deriveToolParams(ep config.Endpoint) []config.EndpointParam {
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
			Description: fmt.Sprintf("Уникальный идентификатор %s", ep.Entity),
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
			desc := fmt.Sprintf("Текстовый запрос для поиска %s по полю '%s'. Если не указан, возвращаются все записи.",
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
