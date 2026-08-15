package search

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// FilterStrategy — field-based filtering strategy.
//
// LLM-facing name: filter_{entity}
// Supports field__op syntax: {field}__eq, {field}__gt, {field}__like, etc.
// Short form {field} = exact match (eq).
type FilterStrategy struct {
	idCol           string
	nameCol         string
	maxFilters      int
	filterableRules []config.FieldRule
}

const (
	// maxFilterValueLen — максимальная длина одного значения фильтра.
	maxFilterValueLen = 200
	// maxInValues — максимум значений в field__in.
	maxInValues = 50
)

// NewFilterStrategy creates a FilterStrategy.
// filterableRules — опционально: если не переданы, используются DefaultFilterableFieldRules().
func NewFilterStrategy(idCol, nameCol string, filterableRules ...config.FieldRule) *FilterStrategy {
	rules := config.DefaultFilterableFieldRules()
	if len(filterableRules) > 0 {
		rules = filterableRules
	}
	if rules == nil {
		rules = config.DefaultFilterableFieldRules()
	}
	return &FilterStrategy{
		idCol:           idCol,
		nameCol:         nameCol,
		maxFilters:      15,
		filterableRules: rules,
	}
}

func (s *FilterStrategy) Name() string { return "filter" }

func (s *FilterStrategy) EntityIDCol() string   { return s.idCol }
func (s *FilterStrategy) EntityNameCol() string { return s.nameCol }

func (s *FilterStrategy) ToolName(entity config.Entity) string {
	return "filter_" + entity.Name
}

func (s *FilterStrategy) ToolDescription(entity config.Entity) string {
	return fmt.Sprintf(
		"Exact-value filtering over %s. Use ONLY when you KNOW the value.\n"+
			"\n"+
			"WHEN: you have an exact value (an id from a previous search, a known status, a price range).\n"+
			"WHEN NOT: do not guess values — call schema on the entity first to see valid values.\n"+
			"\n"+
			"Operators (appended to the field name with __):\n"+
			"  {field}=value       — exact match (status='shipped')\n"+
			"  {field}__gt=value   — greater than (price__gt=1000)\n"+
			"  {field}__lt=value   — less than (price__lt=5000)\n"+
			"  {field}__gte=value  — greater than or equal\n"+
			"  {field}__lte=value  — less than or equal\n"+
			"  {field}__like=value — LIKE search (name__like='%%head%%')\n"+
			"  {field}__in=a,b,c   — IN list (status__in=shipped,delivered)\n"+
			"\n"+
			"Examples:\n"+
			"  status__in=shipped,delivered, limit=10\n"+
			"  price__lte=5000, category='brakes'\n"+
			"\n"+
			"SQLite: LIKE is case-sensitive for Cyrillic, use %% as wildcard.",
		entity.Name,
	)
}

func filterParamDescription(base string, field config.EntityField) string {
	if field.Description == "" {
		return base
	}
	return fmt.Sprintf("%s Field meaning: %s", base, field.Description)
}

func (s *FilterStrategy) ToolParams(entity config.Entity) []config.EndpointParam {
	f := false

	// Build a param for each non-PK field that is actually filterable.
	params := make([]config.EndpointParam, 0, len(entity.Fields)*4+3)

	for _, field := range entity.Fields {
		if field.PrimaryKey != nil && *field.PrimaryKey {
			continue
		}
		if field.ExcludeFromSearch {
			continue // PII/excluded: не участвует в фильтрации
		}
		if !config.IsFilterableField(field, s.filterableRules) {
			continue // Delegate to config.IsFilterableField (implicit rules + configurable FieldRules)
		}

		pt := fieldTypeToParamType(field.Type)

		// Exact match: just {field}
		params = append(params, config.EndpointParam{
			Name:        field.Name,
			In:          config.ParamInQuery,
			Type:        pt,
			Required:    &f,
			Description: filterParamDescription(fmt.Sprintf("Filter by exact '%s' value.", field.Name), field),
		})

		// Comparison operators for numeric fields (skip FK — exact match only).
		isFK := strings.HasSuffix(field.Name, "_id")
		if !isFK && (field.Type == config.FieldTypeInt || field.Type == config.FieldTypeFloat) {
			for _, op := range []struct{ suffix, desc string }{
				{"__gt", "greater than"},
				{"__gte", "greater than or equal"},
				{"__lt", "less than"},
				{"__lte", "less than or equal"},
			} {
				params = append(params, config.EndpointParam{
					Name:        field.Name + op.suffix,
					In:          config.ParamInQuery,
					Type:        pt,
					Required:    &f,
					Description: filterParamDescription(fmt.Sprintf("Filter: %s '%s' value.", op.desc, field.Name), field),
				})
			}
		}

		// LIKE for string fields.
		if field.Type == config.FieldTypeString {
			params = append(params, config.EndpointParam{
				Name:        field.Name + "__like",
				In:          config.ParamInQuery,
				Type:        config.ParamTypeString,
				Required:    &f,
				Description: filterParamDescription(fmt.Sprintf("LIKE pattern for '%s'. Use %% as wildcard.", field.Name), field),
			})
		}

		// IN for all field types.
		params = append(params, config.EndpointParam{
			Name:        field.Name + "__in",
			In:          config.ParamInQuery,
			Type:        pt,
			ArrayOf:     pt,
			Required:    &f,
			Description: filterParamDescription(fmt.Sprintf("Comma-separated values for IN filter on '%s'.", field.Name), field),
		})
	}

	// Limit param (offset, sort_by, format still work in ParseRequest but are not in schema)
	params = append(params, config.EndpointParam{
		Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: &f,
		Description: "Max results (1-100, default: 20).",
	})

	return params
}

// ParseRequest разбирает HTTP-запрос в QueryPlan для filter-стратегии.
func (s *FilterStrategy) ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error) {
	q := r.URL.Query()

	// ── Build field map ─────────────────────────────────────────────
	fieldMap := make(map[string]config.EntityField, len(entity.Fields))
	for _, f := range entity.Fields {
		if f.ExcludeFromSearch {
			continue // PII/excluded: не участвует в фильтрации
		}
		// M3: FieldRules enforced в runtime — зеркально ToolParams. Иначе
		// заблокированное админом поле (block_names/suffix/contains) можно
		// фильтровать напрямую через HTTP, минуя ограничение.
		if !config.IsFilterableField(f, s.filterableRules) {
			continue
		}
		fieldMap[f.Name] = f
	}

	// ── Parse filter conditions ─────────────────────────────────────
	var conditions []query.Condition

	for key, vals := range q {
		if len(vals) == 0 || vals[0] == "" {
			continue
		}
		val := vals[0]

		// Skip known non-filter params.
		switch key {
		case "limit", "offset", "sort_by", "format", "tenant_id":
			continue
		}

		// Parse field__op syntax.
		fieldName, op, found := strings.Cut(key, "__")
		if !found {
			// No __op suffix → exact match.
			fieldName = key
			op = "eq"
		}

		f, ok := fieldMap[fieldName]
		if !ok {
			continue // Unknown field, skip.
		}
		// Tenant isolation: tenant_id не должен быть доступен LLM как filter-поле
		if f.Column == "tenant_id" {
			continue
		}
		// Skip PK fields — they are filtered via get_by_id, not filter.
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}

		qName := a.QuoteIdentifier(f.Column)

		switch op {
		case "eq":
			if len(val) > maxFilterValueLen {
				return nil, fmt.Errorf("filter value for '%s' too long (%d chars, max %d)", fieldName, len(val), maxFilterValueLen)
			}
			c, err := makeEqCondition(qName, f, val)
			if err != nil {
				continue
			}
			conditions = append(conditions, c)

		case "neq":
			if len(val) > maxFilterValueLen {
				return nil, fmt.Errorf("filter value for '%s' too long (%d chars, max %d)", fieldName, len(val), maxFilterValueLen)
			}
			c, err := makeEqCondition(qName, f, val)
			if err != nil {
				continue
			}
			c.Not = true
			conditions = append(conditions, c)

		case "gt", "lt", "gte", "lte":
			// Numeric comparison.
			if len(val) > maxFilterValueLen {
				return nil, fmt.Errorf("filter value for '%s__%s' too long (%d chars, max %d)", fieldName, op, len(val), maxFilterValueLen)
			}
			c, err := makeComparison(qName, op, f, val)
			if err != nil {
				continue
			}
			conditions = append(conditions, c)

		case "like":
			if f.Type != config.FieldTypeString {
				continue
			}
			if len(val) > maxFilterValueLen {
				return nil, fmt.Errorf("filter value for '%s__like' too long (%d chars, max %d)", fieldName, len(val), maxFilterValueLen)
			}
			// RawValue=true: user provides their own % wildcards.
			// OpILike for proper case-insensitive search (cyrillic support).
			//
			// ⚠️ Контракт (L10): значение идёт сырым и обёрнуто ESCAPE '\' в SQL
			// (query_builder.go). % и _ — wildcard'ы пользователя; литеральный
			// backslash ВАЖНО не экранируется и поэтому становится escape-символом:
			//   field__like=50\%  →  literal "50%"
			//   field__like=C\_   →  literal "C_"
			// Искать строку с backslash как таковым нельзя без двойного \\ —
			// это осознанный tradeoff (пользователь управляет wildcard'ами).
			conditions = append(conditions, query.Condition{
				Field:    qName,
				Operator: query.OpILike,
				Value:    val,
				RawValue: true,
			})

		case "in":
			if len(val) > maxFilterValueLen {
				return nil, fmt.Errorf("filter value for '%s__in' too long (%d chars, max %d)", fieldName, len(val), maxFilterValueLen)
			}
			parts := strings.Split(val, ",")
			if len(parts) > maxInValues {
				return nil, fmt.Errorf("too many values for '%s__in' (%d, max %d)", fieldName, len(parts), maxInValues)
			}
			vals := make([]any, 0, len(parts))
			for _, p := range parts {
				p = strings.TrimSpace(p)
				if p == "" {
					continue
				}
				typed, err := convertValue(p, f.Type)
				if err != nil {
					continue
				}
				vals = append(vals, typed)
			}
			if len(vals) > 0 {
				conditions = append(conditions, query.Condition{
					Field:    qName,
					Operator: query.OpIn,
					Values:   vals,
				})
			}

		default:
			// Unknown operator, skip.
			continue
		}
	}

	// ── Security: max filters limit ─────────────────────────────────
	if len(conditions) > s.maxFilters {
		return nil, fmt.Errorf("too many filter conditions: %d (max %d)", len(conditions), s.maxFilters)
	}

	// ── Error if no filter conditions: LLM must learn to pass parameters.
	if len(conditions) == 0 {
		return nil, fmt.Errorf("at least one filter parameter is required. Examples: category='brakes', price__gt=1000")
	}

	return &query.QueryPlan{
		Select: selectClause(entity, q, a),
		From:   a.QuoteIdentifier(entity.Table),
		Where:  conditions,
		Limit:  parseLimitParam(q, 10),
		Offset: parseOffset(q),
		Order:  parseOrder(q, entity, a),
		Format: parseFormat(q),
	}, nil
}
