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
	idCol      string
	nameCol    string
	maxFilters int
}

const (
	// maxFilterValueLen — максимальная длина одного значения фильтра.
	maxFilterValueLen = 200
	// maxInValues — максимум значений в field__in.
	maxInValues = 50
)

// NewFilterStrategy creates a FilterStrategy.
func NewFilterStrategy(idCol, nameCol string) *FilterStrategy {
	return &FilterStrategy{
		idCol:      idCol,
		nameCol:    nameCol,
		maxFilters: 15,
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
		"Фильтрация %s по значениям полей.\n"+
			"\n"+
			"ВАЖНО: Передай хотя бы один параметр фильтра!\n"+
			"\n"+
			"Операторы (добавляются к имени поля через __):\n"+
			"  {field}=value       — точное совпадение (category='Тормозная система')\n"+
			"  {field}__gt=value   — больше (price__gt=1000)\n"+
			"  {field}__lt=value   — меньше (price__lt=5000)\n"+
			"  {field}__gte=value  — больше или равно\n"+
			"  {field}__lte=value  — меньше или равно\n"+
			"  {field}__like=value — LIKE поиск (reason__like='%%Голов%%')\n"+
			"  {field}__in=a,b,c   — IN список (status__in=shipped,delivered)\n"+
			"\n"+
			"Примеры:\n"+
			"  category='Тормозная система', price__lte=5000\n"+
			"    → тормозные запчасти до 5000₽\n"+
			"  status__in=shipped,delivered, limit=10\n"+
			"    → последние 10 отправленных и доставленных заказов\n"+
			"  rating__gte=4.5, experience__gte=10\n"+
			"    → топ врачи со стажем от 10 лет\n"+
			"\n"+
			"SQLite: LIKE чувствителен к регистру для кириллицы, используй %% как wildcard.",
		entity.Name,
	)
}

func (s *FilterStrategy) ToolParams(entity config.Entity) []config.EndpointParam {
	f := false

	// Build a param for each non-PK field.
	params := make([]config.EndpointParam, 0, len(entity.Fields)*4+3)

	for _, field := range entity.Fields {
		if field.PrimaryKey != nil && *field.PrimaryKey {
			continue
		}

		pt := fieldTypeToParamType(field.Type)

		// Exact match: just {field}
		params = append(params, config.EndpointParam{
			Name:        field.Name,
			In:          config.ParamInQuery,
			Type:        pt,
			Required:    &f,
			Description: fmt.Sprintf("Filter by exact '%s' value.", field.Name),
		})

		// Comparison operators for numeric fields.
		if field.Type == config.FieldTypeInt || field.Type == config.FieldTypeFloat {
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
					Description: fmt.Sprintf("Filter: %s '%s' value.", op.desc, field.Name),
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
				Description: fmt.Sprintf("LIKE pattern for '%s'. Use %% as wildcard.", field.Name),
			})
		}

		// IN for all field types.
		params = append(params, config.EndpointParam{
			Name:        field.Name + "__in",
			In:          config.ParamInQuery,
			Type:        pt,
			ArrayOf:     pt,
			Required:    &f,
			Description: fmt.Sprintf("Comma-separated values for IN filter on '%s'.", field.Name),
		})
	}

	// Limit param (offset, sort_by, format still work in ParseRequest but are not in schema)
	params = append(params, config.EndpointParam{
		Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: &f,
		Description: "Max results (1-1000, default: 20).",
	})

	return params
}

// ParseRequest разбирает HTTP-запрос в QueryPlan для filter-стратегии.
func (s *FilterStrategy) ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error) {
	q := r.URL.Query()

	// ── Build field map ─────────────────────────────────────────────
	fieldMap := make(map[string]config.EntityField, len(entity.Fields))
	for _, f := range entity.Fields {
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
		Select:  selectClause(entity, q, a),
		From:    a.QuoteIdentifier(entity.Table),
		Where:   conditions,
		Limit:   parseLimitParam(q, 10),
		Offset:  parseOffset(q),
		Order:   parseOrder(q, entity, a),
		Format:  parseFormat(q),
	}, nil
}
