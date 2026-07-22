package configgen

import (
	"fmt"
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/search"
)

// GenerateMCPTools creates compact MCP tools from endpoints with LLM-friendly descriptions.
func GenerateMCPTools(endpoints []config.Endpoint, entities []config.Entity, displayPrefixes []string, customPlurals map[string]string) []config.MCPTool {
	entityMap := make(map[string]*config.Entity, len(entities))
	for i := range entities {
		entityMap[entities[i].Name] = &entities[i]
	}

	// Build set of entities that have strategy-based endpoints
	hasStrategy := make(map[string]bool)
	for _, ep := range endpoints {
		if ep.Strategy != "" && ep.Entity != "" {
			hasStrategy[ep.Entity] = true
		}
	}

	tools := make([]config.MCPTool, 0, len(endpoints))
	for _, ep := range endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			continue
		}

		// Strategy-based endpoints (grep, filter, simple, search)
		// Use the strategy's ToolName/ToolDescription/ToolParams.
		if ep.Strategy != "" {
			// Find entity config for strategy params
			var entCfg *config.Entity
			for i := range entities {
				if entities[i].Name == ep.Entity {
					entCfg = &entities[i]
					break
				}
			}
			if entCfg == nil {
				continue
			}
			tool := strategyToMCPTool(ep.Strategy, *entCfg, ep.Path)
			if tool != nil {
				tools = append(tools, *tool)
			}
			continue
		}

		var toolName, desc, displayName string
		ent := entityMap[ep.Entity]

		switch ep.Op {
		case config.OpGetByID:
			toolName = fmt.Sprintf("get_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get a single %s by its ID. "+
					"Use after search_%s when you have a specific ID.",
				ep.Entity, ep.Entity)
			displayName = toolDisplayName(string(config.OpGetByID), ep.Entity, displayPrefixes, customPlurals)

		case config.OpFind:
			if hasStrategy[ep.Entity] {
				continue // search strategy handles text search
			}
			toolName = fmt.Sprintf("find_%s", ep.Entity)
			filters := compactFilterSummary(ent)
			if filters != "" {
				desc = fmt.Sprintf(
					"Search %s by name (partial match). Filters: %s.",
					pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), filters)
			} else {
				desc = fmt.Sprintf(
					"Search %s by text query. Example: search_%s(pattern='query')",
					pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), ep.Entity)
			}
			displayName = toolDisplayName(string(config.OpFind), ep.Entity, displayPrefixes, customPlurals)

		case config.OpList:
			if hasStrategy[ep.Entity] {
				continue // search strategy handles listing
			}
			toolName = fmt.Sprintf("list_%s", ep.Entity)
			desc = fmt.Sprintf(
				"List all %s. Supports filters and pagination. "+
					"Use when search_%s returns no results or you need all records.",
				pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), ep.Entity)
			displayName = toolDisplayName(string(config.OpList), ep.Entity, displayPrefixes, customPlurals)

		case config.OpDistinct:
			toolName = fmt.Sprintf("distinct_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get unique values for enum columns in %s. "+
					"Use INSTEAD of fetching all records — fast and token-cheap. "+
					"Example: distinct_%s(column='brand') returns ['Brembo', 'Bosch', 'TRW']. "+
					"Always try this first to discover available filter values.",
				pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), ep.Entity)
			displayName = toolDisplayName(string(config.OpDistinct), ep.Entity, displayPrefixes, customPlurals)

		case config.OpCount:
			toolName = fmt.Sprintf("count_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Count %s matching filters. Returns {entity, count}. Fast and token-cheap.",
				pluralizeEntity(ep.Entity, displayPrefixes, customPlurals))
			displayName = toolDisplayName(string(config.OpCount), ep.Entity, displayPrefixes, customPlurals)

			// Add filter params so LLM knows which fields to filter by
			if ent != nil {
				f := false
				for _, field := range ent.Fields {
					if field.PrimaryKey != nil && *field.PrimaryKey {
						continue
					}
					pt := config.ParamTypeString
					switch field.Type {
					case config.FieldTypeInt:
						pt = config.ParamTypeInt
					case config.FieldTypeFloat:
						pt = config.ParamTypeFloat
					case config.FieldTypeBool:
						pt = config.ParamTypeBool
					}
					ep.Params = append(ep.Params, config.EndpointParam{
						Name: field.Name, In: config.ParamInQuery, Type: pt, Required: &f,
						Description: fmt.Sprintf("Filter by exact '%s' value.", field.Name),
					})
					// __like for strings
					if field.Type == config.FieldTypeString {
						ep.Params = append(ep.Params, config.EndpointParam{
							Name: field.Name + "__like", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &f,
							Description: fmt.Sprintf("LIKE pattern for '%s'. Use %% as wildcard.", field.Name),
						})
					}
					// __gt/__lt for numeric
					if field.Type == config.FieldTypeInt || field.Type == config.FieldTypeFloat {
						for _, op := range []struct{ suffix, desc string }{
							{"__gt", "greater than"},
							{"__gte", "greater than or equal"},
							{"__lt", "less than"},
							{"__lte", "less than or equal"},
						} {
							ep.Params = append(ep.Params, config.EndpointParam{
								Name: field.Name + op.suffix, In: config.ParamInQuery, Type: pt, Required: &f,
								Description: fmt.Sprintf("Filter: %s '%s' value.", op.desc, field.Name),
							})
						}
					}
				}
			}

		case config.OpCustomQuery:
			// Relationship tools (products_by_brand) kept for strategy entities
			// because they have required path params ({id}) preventing empty calls.
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
			for _, pfx := range displayPrefixes {
				childShort = strings.TrimPrefix(childShort, pfx)
			}
			parentShort := parentName
			for _, pfx := range displayPrefixes {
				parentShort = strings.TrimPrefix(parentShort, pfx)
			}
			toolName = fmt.Sprintf("%s_by_%s", pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), parentShort)
			displayName = fmt.Sprintf("%s by %s", pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), parentShort)

			// Strategic description: guides LLM workflow
			desc = fmt.Sprintf(
				"Get all %s for a given %s. "+
					"Use after search_%s to get the ID, then call this to list related %s. "+
					"Supports filters and pagination.",
				pluralizeEntity(ep.Entity, displayPrefixes, customPlurals), parentShort,
				parentName, pluralizeEntity(ep.Entity, displayPrefixes, customPlurals))
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

// strategyToMCPTool создаёт MCPTool для strategy-эндпоинта, используя
// методы стратегии для генерации имени, описания и параметров.
func strategyToMCPTool(strategyName string, entity config.Entity, epPath string) *config.MCPTool {
	idCol := entity.IDColumnOrDefault()
	nameCol := entity.FirstStringFieldColumn()

	var strategy search.Strategy
	switch strategyName {
	case "grep":
		strategy = search.NewGrepStrategy(idCol, nameCol)
	case "filter":
		strategy = search.NewFilterStrategy(idCol, nameCol)
	case "simple":
		strategy = search.NewSimpleStrategy(idCol, nameCol, nameCol)
	case "search":
		strategy = search.NewSearchStrategy(idCol, nameCol)
	default:
		return nil
	}

	return &config.MCPTool{
		Name:        strategy.ToolName(entity),
		Description: strategy.ToolDescription(entity),
		Params:      strategy.ToolParams(entity),
		Endpoint:    epPath,
	}
}
