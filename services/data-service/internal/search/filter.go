package search

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
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
			"REQUIRED: every call must include at least one field filter. limit only controls the returned preview; it is not a filter.\n"+
			"RESULT: every successful response includes total, the authoritative number of records matching the filters. For a count question, use total and do not fetch extra rows.\n"+
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
			"  {field}__gt_field=other_field — compare numeric fields (old_price__gt_field=price)\n"+
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

		// Field-to-field comparisons accept a field name as a string.
		// ParseRequest validates the target as a filterable numeric column
		// and quotes it before it reaches the SQL renderer.
		if !isFK && isNumericField(field) {
			for _, op := range []struct{ suffix, desc string }{
				{"__gt_field", "greater than another numeric field"},
				{"__gte_field", "greater than or equal to another numeric field"},
				{"__lt_field", "less than another numeric field"},
				{"__lte_field", "less than or equal to another numeric field"},
			} {
				params = append(params, config.EndpointParam{
					Name:        field.Name + op.suffix,
					In:          config.ParamInQuery,
					Type:        config.ParamTypeString,
					Required:    &f,
					Description: filterParamDescription(fmt.Sprintf("Filter: %s; value must be another filterable numeric field name.", op.desc), field),
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

func isNumericField(field config.EntityField) bool {
	return field.Type == config.FieldTypeInt || field.Type == config.FieldTypeFloat
}

func comparisonOperator(op string) query.Operator {
	switch op {
	case "gt":
		return query.OpGt
	case "gte":
		return query.OpGte
	case "lt":
		return query.OpLt
	case "lte":
		return query.OpLte
	default:
		panic("unsupported field comparison operator: " + op)
	}
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
		// db_map показывает display-имена полей ("is active", "brand ID"),
		// а реальные колонки — snake_case (is_active, brand_id). Модель
		// копирует имя из db_map в filter-параметр, и без этой нормализации
		// получала "Unknown field, skip" → parse_error. Принимаем оба варианта
		// (как CanonicalEntityName делает для сущностей).
		if display := displayColumnName(f.Name); display != f.Name {
			fieldMap[display] = f
		}
	}

	// ── Parse filter conditions ─────────────────────────────────────
	var conditions []query.Condition

	// Модель иногда упаковывает фильтр в JSON-объект: filter='{"category ID": 19}'
	// или filters='{"price__gt": 1000}', вместо прямых параметров. Это данные,
	// а не код: разворачиваем их в обычные условия с той же валидацией полей.
	unwrapped, err := unwrapFilterObject(q, fieldMap, a)
	if err != nil {
		return nil, err
	}
	conditions = append(conditions, unwrapped...)

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

		case "gt_field", "lt_field", "gte_field", "lte_field":
			// Compare two validated numeric entity columns. The right side is a field
			// name, never a raw SQL expression.
			if !isNumericField(f) || strings.HasSuffix(f.Name, "_id") {
				continue
			}
			target, ok := fieldMap[val]
			if !ok || !isNumericField(target) || strings.HasSuffix(target.Name, "_id") ||
				(target.PrimaryKey != nil && *target.PrimaryKey) || target.Column == "tenant_id" {
				continue
			}
			comparison_op := strings.TrimSuffix(op, "_field")
			conditions = append(conditions, query.Condition{
				Field:    qName,
				FieldRef: a.QuoteIdentifier(target.Column),
				Operator: comparisonOperator(comparison_op),
			})
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
	// Список валидных filterable-полей (display + snake_case) — это данные для
	// модели, а не промпт-инжиниринг: она видела db_map, но не связывает его
	// с db_filter. Перечисляем поля, чтобы она могла исправить вызов.
	if len(conditions) == 0 {
		valid := make([]string, 0, len(fieldMap))
		for name := range fieldMap {
			valid = append(valid, name)
		}
		sort.Strings(valid)
		if len(valid) == 0 {
			return nil, fmt.Errorf("at least one filter parameter is required, but entity %s has no filterable fields", entity.Name)
		}
		return nil, fmt.Errorf(
			"at least one filter parameter is required. Valid filterable fields for %s: %s. "+
				"Pass them directly as query parameters (e.g. %s__gt=1000), not wrapped in an object.",
			entity.Name, strings.Join(valid, ", "), strings.ReplaceAll(valid[0], " ", "_"),
		)
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

// displayColumnName делает snake_case колонку читаемой для LLM — зеркалит
// configgen.shortColumnName (search не может импортировать configgen из-за
// цикличности). Нужна, чтобы filter принимал display-имена, которые модель
// копирует из db_map ("is active", "brand ID").
func displayColumnName(name string) string {
	result := strings.ReplaceAll(name, "_", " ")
	if strings.HasSuffix(name, "_id") {
		result = strings.TrimSuffix(result, " id") + " ID"
	}
	return result
}

// unwrapFilterObject разворачивает JSON-обёртку фильтра, которую LLM-модель
// иногда присылает вместо прямых query-параметров. Слабые модели не видят
// поля в JSON-схеме db_filter (там только entity + limit) и упаковывают их в
// объект: filter='{"category ID": 19}' / filters='{"price__gt": 1000}' /
// filter_fields='{"is promo": true}'. Принимаем известные ключи-обёртки и
// превращаем содержимое в обычные условия: ключи валидируются тем же fieldMap
// (whitelist + display-имена), значения — convertValue, tenant_id и PK
// исключаются как в основном цикле. Это данные, а не код: никакой SQL из
// значений не строится, read-only и tenant-изоляция не обходятся.
func unwrapFilterObject(q url.Values, fieldMap map[string]config.EntityField, a Adapter) ([]query.Condition, error) {
	var conditions []query.Condition

	for _, wrapKey := range []string{"filter", "filters", "filter_fields"} {
		vals, ok := q[wrapKey]
		if !ok || len(vals) == 0 {
			continue
		}
		raw := strings.TrimSpace(vals[0])
		if raw == "" {
			continue
		}

		if strings.HasPrefix(raw, "{") {
			var obj map[string]any
			if err := json.Unmarshal([]byte(raw), &obj); err != nil {
				continue // мусор — не блокируем, пусть модель увидит valid-fields ошибку
			}
			conds, err := conditionsFromObject(obj, fieldMap, a)
			if err != nil {
				return nil, err
			}
			conditions = append(conditions, conds...)
		}

		// Слабая модель иногда шлёт массив условий:
		// filters='[{"field": "category ID", "operator": "=", "value": 90}]'.
		// Разбираем как список {field, operator, value} записей.
		if strings.HasPrefix(raw, "[") {
			var items []map[string]any
			if err := json.Unmarshal([]byte(raw), &items); err != nil {
				continue
			}
			for _, it := range items {
				f, fok := it["field"].(string)
				if !fok {
					continue
				}
				op, _ := it["operator"].(string)
				switch op {
				case "", "=", "==":
					op = "eq"
				case "!=":
					op = "neq"
				case ">":
					op = "gt"
				case ">=":
					op = "gte"
				case "<":
					op = "lt"
				case "<=":
					op = "lte"
				}
				// вложенный объект из одного ключа: {"field": X, "value": 90}
				conds, err := conditionsFromObject(map[string]any{f + "__" + op: it["value"]}, fieldMap, a)
				if err != nil {
					return nil, err
				}
				conditions = append(conditions, conds...)
			}
		}
	}

	return conditions, nil
}

// conditionsFromObject превращает распарсенный JSON-объект фильтра в условия.
// Поддерживает те же операторы, что основной цикл: {field}, {field__op},
// {field: {__op: value}} и {field: [v1, v2]} (IN).
func conditionsFromObject(obj map[string]any, fieldMap map[string]config.EntityField, a Adapter) ([]query.Condition, error) {
	var conditions []query.Condition

	// Детерминированный порядок: map-итерация в Go случайна, а порядок
	// условий влияет на SQL/args в тестах и на стабильность планов.
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	for _, key := range keys {
		val := obj[key]
		fieldName, op, found := strings.Cut(key, "__")
		if !found {
			fieldName = key
			op = "eq"
		}

		f, ok := fieldMap[fieldName]
		if !ok {
			continue // Unknown field, skip (как в основном цикле)
		}
		if f.Column == "tenant_id" {
			continue
		}
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		qName := a.QuoteIdentifier(f.Column)

		switch op {
		case "eq":
			// Массив без явного оператора → IN (как основной цикл для __in).
			if items, ok := val.([]any); ok {
				values := make([]any, 0, len(items))
				for _, it := range items {
					v, err := jsonScalar(it, f.Type)
					if err != nil {
						continue
					}
					values = append(values, v)
				}
				if len(values) > 0 {
					conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpIn, Values: values})
				}
				continue
			}
			// Поддержка вложенного объекта: {"price": {"__gt": 1000}} —
			// рекурсивно разбираем его как {price__gt: 1000}.
			if nested, ok := val.(map[string]any); ok {
				nestedObj := make(map[string]any, len(nested))
				for nk, nv := range nested {
					nestedObj[fieldName+"__"+strings.TrimPrefix(nk, "__")] = nv
				}
				nestedConds, err := conditionsFromObject(nestedObj, fieldMap, a)
				if err != nil {
					return nil, err
				}
				conditions = append(conditions, nestedConds...)
				continue
			}
			v, err := jsonScalar(val, f.Type)
			if err != nil {
				continue
			}
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpEq, Value: v})

		case "neq":
			v, err := jsonScalar(val, f.Type)
			if err != nil {
				continue
			}
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpEq, Value: v, Not: true})

		case "gt", "lt", "gte", "lte":
			v, err := jsonScalar(val, f.Type)
			if err != nil {
				continue
			}
			c, err := makeComparison(qName, op, f, fmt.Sprint(v))
			if err != nil {
				continue
			}
			conditions = append(conditions, c)

		case "in":
			items, ok := val.([]any)
			if !ok {
				continue
			}
			values := make([]any, 0, len(items))
			for _, it := range items {
				v, err := jsonScalar(it, f.Type)
				if err != nil {
					continue
				}
				values = append(values, v)
			}
			if len(values) > 0 {
				conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpIn, Values: values})
			}

		default:
			// Unknown operator, skip.
		}
	}

	return conditions, nil
}

// jsonScalar приводит JSON-значение к типизированному значению поля.
func jsonScalar(val any, ft config.FieldType) (any, error) {
	switch ft {
	case config.FieldTypeInt:
		if n, ok := val.(float64); ok {
			return int64(n), nil
		}
		if s, ok := val.(string); ok {
			return strconv.ParseInt(strings.TrimSpace(s), 10, 64)
		}
		return nil, fmt.Errorf("invalid int value: %v", val)
	case config.FieldTypeFloat:
		if n, ok := val.(float64); ok {
			return n, nil
		}
		if s, ok := val.(string); ok {
			return strconv.ParseFloat(strings.TrimSpace(s), 64)
		}
		return nil, fmt.Errorf("invalid float value: %v", val)
	case config.FieldTypeBool:
		if b, ok := val.(bool); ok {
			return b, nil
		}
		if s, ok := val.(string); ok {
			return strconv.ParseBool(strings.TrimSpace(s))
		}
		return nil, fmt.Errorf("invalid bool value: %v", val)
	default:
		return val, nil
	}
}
