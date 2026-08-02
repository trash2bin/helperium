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
func GenerateMCPTools(endpoints []config.Endpoint, entities []config.Entity, displayPrefixes []string, customPlurals map[string]string, filterableRules []config.FieldRule, searchableRules []config.FieldRule) []config.MCPTool {
	entityMap := make(map[string]*config.Entity, len(entities))
	for i := range entities {
		entityMap[entities[i].Name] = &entities[i]
	}

	tools := make([]config.MCPTool, 0, len(endpoints))
	for _, ep := range endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			continue
		}

		// Strategy-based endpoints (grep, filter, schema)
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
			tool := strategyToMCPTool(ep.Strategy, *entCfg, ep.Path, displayPrefixes, customPlurals, filterableRules, searchableRules)
			if tool != nil {
				tools = append(tools, *tool)
			}
			continue
		}

		var toolName, desc, displayName string

		switch ep.Op {
		case config.OpGetByID:
			toolName = fmt.Sprintf("get_%s", ep.Entity)
			desc = fmt.Sprintf(
				"Get a single %s by its ID. "+
					"Use after grep_%s when you have a specific ID.",
				ep.Entity, ep.Entity)
			displayName = toolDisplayName(string(config.OpGetByID), ep.Entity, displayPrefixes, customPlurals)

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
