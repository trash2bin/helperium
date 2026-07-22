// Package search — SearchStrategy объединяет grep + filter в один инструмент.
//
// LLM видит ОДИН инструмент search_{entity} вместо двух (grep + filter).
// Параметры: pattern (опционально — текстовый поиск) + field__op (фильтры).
// Если есть pattern — делает multi-token AND grep по строковым полям.
// Если есть field__op — делает фильтрацию по указанным полям.
// Если есть и то и другое — AND комбинация.
package search

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// SearchStrategy — комбинированная стратегия: grep (text) + filter (field).
//
// LLM-facing name: search_{entity}
// Параметры:
//
//	pattern (опционально) — текстовый grep-поиск (multi-token AND, multi-field OR, regex)
//	{field} — точное совпадение
//	{field}__gt/lte/in/like — операторы
//	limit (опционально)
//
// Примеры:
//
//	search_products(pattern="тормозные колодки")       — текстовый поиск
//	search_products(category="Тормозная система")       — фильтр по полю
//	search_products(pattern="Brembo", category="Тормозная система") — комбо
type SearchStrategy struct {
	// ── Security limits (same as GrepStrategy) ───────────────────────
	maxRegexLen       int
	maxTokens         int
	maxFields         int
	maxFilterValueLen int
	maxInValues       int
	maxPatternLen     int
	maxFilters        int  // макс кол-во field__op фильтров
	maxTotalConditions int // макс всего: tokens + field фильтры

	idCol   string
	nameCol string
}

// NewSearchStrategy creates a SearchStrategy.
func NewSearchStrategy(idCol, nameCol string) *SearchStrategy {
	return &SearchStrategy{
		idCol:             idCol,
		nameCol:           nameCol,
		maxRegexLen:       200,   // ReDoS защита
		maxTokens:         10,    // макс токенов
		maxFields:         20,    // макс полей для grep
		maxFilterValueLen: 1000,  // макс длина filter value
		maxInValues:       100,   // макс значений IN
		maxPatternLen:     2000,  // макс длина pattern
		maxFilters:        15,    // макс field__op фильтров
		maxTotalConditions: 25,   // макс всего условий (tokens + filters)
	}
}

func (s *SearchStrategy) Name() string          { return "search" }
func (s *SearchStrategy) EntityIDCol() string   { return s.idCol }
func (s *SearchStrategy) EntityNameCol() string { return s.nameCol }

func (s *SearchStrategy) ToolName(entity config.Entity) string {
	return "search_" + entity.Name
}

func (s *SearchStrategy) ToolDescription(entity config.Entity) string {
	// Build available fields list for the description
	var fieldNames []string
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		fieldNames = append(fieldNames, f.Name)
	}
	fieldsStr := strings.Join(fieldNames, ", ")

	return fmt.Sprintf(
		"Unified search for %[1]s — text search + field filters in one tool.\n"+
			"\n"+
			"❗MANDATORY: Pass at least one parameter! DO NOT call without parameters!\n"+
			"\n"+
			"HOW TO USE:\n"+
			"  • Text search: 'pattern' parameter — searches across all text fields.\n"+
			"    Multi-word = AND (pattern='brake pads' → both words required).\n"+
			"  • Field filter: pass field=value (category='Brakes').\n"+
			"    Operators: {field}__gt, __lt, __gte, __lte, __like, __in, __neq\n"+
			"  • Combine: pattern + fields (pattern='Brembo', category='Brakes') — AND.\n"+
			"\n"+
			"EXAMPLES:\n"+
			"  search_%[1]s(pattern='oil') → text search\n"+
			"  search_%[1]s(category='Brakes', price__lte=5000) → filter + price\n"+
			"  search_%[1]s(pattern='Brembo', category='Brakes') → combined\n"+
			"\n"+
			"Available fields: %[2]s\n"+
			"\n"+
			"SQLite: Cyrillic search is case-sensitive, try capitalized form.",
		entity.Name, fieldsStr,
	)
}

func (s *SearchStrategy) ToolParams(entity config.Entity) []config.EndpointParam {
	f := false

	t := true

	// 1. pattern (REQUIRED — LLM MUST pass a non-empty search query).
	//    The parse layer will also reject empty strings as a safety net.
	params := []config.EndpointParam{
		{Name: "pattern", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &t,
			Description: "REQUIRED. Search query — multi-token AND across all text fields. NEVER pass empty string! Examples: 'oil', 'Brembo brake pads', 'muffler BMW X5'."},
	}

	// 2. Для каждого поля — exact match + операторы (как в filter)
	for _, field := range entity.Fields {
		if field.PrimaryKey != nil && *field.PrimaryKey {
			continue
		}

		pt := fieldTypeToParamType(field.Type)

		// Exact match
		params = append(params, config.EndpointParam{
			Name: field.Name, In: config.ParamInQuery, Type: pt, Required: &f,
			Description: fmt.Sprintf("Filter by exact '%s' value.", field.Name),
		})

		// Comparison operators for numeric
		if field.Type == config.FieldTypeInt || field.Type == config.FieldTypeFloat {
			for _, op := range []struct{ suffix, desc string }{
				{"__gt", "greater than"},
				{"__gte", "greater than or equal"},
				{"__lt", "less than"},
				{"__lte", "less than or equal"},
			} {
				params = append(params, config.EndpointParam{
					Name: field.Name + op.suffix, In: config.ParamInQuery, Type: pt, Required: &f,
					Description: fmt.Sprintf("Filter: %s '%s' value.", op.desc, field.Name),
				})
			}
		}

		// LIKE for strings
		if field.Type == config.FieldTypeString {
			params = append(params, config.EndpointParam{
				Name: field.Name + "__like", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &f,
				Description: fmt.Sprintf("LIKE pattern for '%s'. Use %% as wildcard.", field.Name),
			})
		}

		// != for all types (not just numeric)
		params = append(params, config.EndpointParam{
			Name: field.Name + "__neq", In: config.ParamInQuery, Type: pt, Required: &f,
			Description: fmt.Sprintf("Filter: not equal to '%s'. Example: status__neq=deleted", field.Name),
		})

		// IN for all
		params = append(params, config.EndpointParam{
			Name: field.Name + "__in", In: config.ParamInQuery, Type: pt, ArrayOf: pt, Required: &f,
			Description: fmt.Sprintf("Comma-separated values for IN filter on '%s'.", field.Name),
		})
	}

	// 3. limit (опционально)
	params = append(params, config.EndpointParam{
		Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: &f,
		Description: "Max results (1-1000, default: 10). Keep small (10-20) unless user explicitly asks for more.",
	})

	return params
}

// ParseRequest разбирает HTTP-запрос в QueryPlan для search-стратегии.
//
// Логика:
//   - Если есть pattern → grep-like multi-token AND поиск по строковым полям
//   - Если есть field__op фильтры → filter-like точная фильтрация
//   - Если и то и другое → AND комбинация
//   - Если ни того, ни другого → 400 ошибка
func (s *SearchStrategy) ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error) {
	q := r.URL.Query()

	// ── Парсим pattern (опционально) ─────────────────────────────────
	pattern := strings.TrimSpace(q.Get("pattern"))

	// Security: лимит длины non-regex pattern
	if len(pattern) > s.maxPatternLen {
		return nil, fmt.Errorf("pattern too long: %d chars (max %d)", len(pattern), s.maxPatternLen)
	}

	hasPattern := pattern != ""

	// ── Парсим фильтры (field__op) ──────────────────────────────────
	fieldMap := make(map[string]config.EntityField, len(entity.Fields))
	for _, f := range entity.Fields {
		fieldMap[f.Name] = f
	}

	var conditions []query.Condition
	ignoreCase := parseBoolParam(q, "ignore_case", true)
	regex := parseBoolParam(q, "regex", false)
	invert := parseBoolParam(q, "invert", false)

	for key, vals := range q {
		if len(vals) == 0 || vals[0] == "" {
			continue
		}
		val := vals[0]

		// Skip known non-filter params
		switch key {
		case "pattern", "limit", "offset", "sort_by", "format", "ignore_case", "regex", "invert", "fields":
			continue
		}

		// Parse field__op syntax
		fieldName, op, found := strings.Cut(key, "__")
		if !found {
			fieldName = key
			op = "eq"
		}

		f, ok := fieldMap[fieldName]
		if !ok {
			continue
		}
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}

		// Security: лимит длины filter value
		if len(val) > s.maxFilterValueLen {
			return nil, fmt.Errorf("filter value for '%s' too long: %d chars (max %d)", fieldName, len(val), s.maxFilterValueLen)
		}

		qName := a.QuoteIdentifier(f.Column)

		switch op {
		case "eq":
			c, err := makeEqCondition(qName, f, val)
			if err != nil {
				continue
			}
			conditions = append(conditions, c)
		case "neq":
			c, err := makeEqCondition(qName, f, val)
			if err != nil {
				continue
			}
			c.Not = true
			conditions = append(conditions, c)
		case "gt", "lt", "gte", "lte":
			c, err := makeComparison(qName, op, f, val)
			if err != nil {
				continue
			}
			conditions = append(conditions, c)
		case "like":
			if f.Type != config.FieldTypeString {
				continue
			}
			conditions = append(conditions, query.Condition{
				Field:    qName,
				Operator: query.OpILike,
				Value:    val,
				RawValue: true,
			})
		case "in":
			parts := strings.Split(val, ",")

			// Security: кап на количество значений в IN
			if len(parts) > s.maxInValues {
				return nil, fmt.Errorf("too many values for IN filter on '%s': %d (max %d)", fieldName, len(parts), s.maxInValues)
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
			continue
		}
	}

	hasFilters := len(conditions) > 0

	// ── Security: max filters limit ────────────────────────────────
	if len(conditions) > s.maxFilters {
		return nil, fmt.Errorf("too many filter conditions: %d (max %d)", len(conditions), s.maxFilters)
	}

	// ── Ни pattern, ни фильтров → 400 ───────────────────────────────
	if !hasPattern && !hasFilters {
		var fieldList []string
		for _, f := range entity.Fields {
			if f.PrimaryKey != nil && *f.PrimaryKey {
				continue
			}
			fieldList = append(fieldList, fmt.Sprintf("%s (%s)", f.Name, string(f.Type)))
		}
		return nil, fmt.Errorf(
			"at least one parameter required! Pass 'pattern' for text search or field filters.\n"+
				"Examples: pattern='oil', category='Brakes', price__gte=1000\n"+
				"Available fields: %s",
			strings.Join(fieldList, ", "))
	}

	// ── Строим WHERE ────────────────────────────────────────────────
	var allWhereParts []string
	var allArgs []any
	phIdx := 1

	// Часть 1: pattern → grep-like multi-token WHERE
	if hasPattern {
		// ReDoS защита для regex
		if regex && len(pattern) > s.maxRegexLen {
			return nil, fmt.Errorf("regex pattern too long: %d chars (max %d)", len(pattern), s.maxRegexLen)
		}

		// Fields для grep
		fieldsStr := strings.TrimSpace(q.Get("fields"))
		var searchFields []config.EntityField
		if fieldsStr != "" {
			names := strings.Split(fieldsStr, ",")
			if len(names) > s.maxFields {
				return nil, fmt.Errorf("too many fields: %d (max %d)", len(names), s.maxFields)
			}
			fieldSet := make(map[string]bool)
			for _, n := range names {
				fieldSet[strings.TrimSpace(n)] = true
			}
			for _, f := range entity.Fields {
				if fieldSet[f.Name] {
					searchFields = append(searchFields, f)
				}
			}
			if len(searchFields) == 0 {
				searchFields = stringFields(entity)
			}
		} else {
			searchFields = stringFields(entity)
		}

		if len(searchFields) > s.maxFields {
			searchFields = searchFields[:s.maxFields]
		}

		// Security: если есть pattern, но нет текстовых полей — ошибка
		if len(searchFields) == 0 {
			return nil, fmt.Errorf("entity has no text-searchable fields for pattern search; use field filters instead")
		}

		tokens := tokenize(pattern)
		if len(tokens) > s.maxTokens {
			tokens = tokens[:s.maxTokens]
		}

		// ── Security: max total conditions ─────────────────────────────
		totalConditions := len(tokens) + len(conditions)
		if totalConditions > s.maxTotalConditions {
			return nil, fmt.Errorf("too many search conditions: %d (max %d)", totalConditions, s.maxTotalConditions)
		}

		if len(tokens) > 0 {
			var grepWhereParts []string

			if regex {
				reOp := regexOp(a)
				if invert {
					reOp = "!" + reOp
				}
				fieldClauses := make([]string, 0, len(searchFields))
				for _, f := range searchFields {
					qName := a.QuoteIdentifier(f.Column)
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					fieldClauses = append(fieldClauses, qName+" "+reOp+" "+ph)
					allArgs = append(allArgs, pattern)
				}
				grepWhereParts = append(grepWhereParts, "("+strings.Join(fieldClauses, " OR ")+")")
			} else {
				likeOp := "LIKE"
				collateNocase := false
				if ignoreCase {
					if a.IsPostgres() {
						likeOp = "ILIKE"
					} else {
						collateNocase = true
					}
				}
				if invert {
					likeOp = "NOT " + likeOp
				}

				fieldClauses := make([]string, 0, len(searchFields))
				for _, f := range searchFields {
					qName := a.QuoteIdentifier(f.Column)
					if collateNocase {
						qName = qName + " COLLATE NOCASE"
					}
					tokenClauses := make([]string, 0, len(tokens))
					for _, tok := range tokens {
						escaped := a.QuoteString(tok)
						val := "%" + escaped + "%"
						ph := a.TranslatePlaceholder(phIdx)
						phIdx++
						tokenClauses = append(tokenClauses, qName+" "+likeOp+" "+ph)
						allArgs = append(allArgs, val)
					}
					fieldClauses = append(fieldClauses, "("+strings.Join(tokenClauses, " AND ")+")")
				}
				grepWhereParts = append(grepWhereParts, strings.Join(fieldClauses, " OR "))
			}

			if len(grepWhereParts) > 0 {
				grepPart := strings.Join(grepWhereParts, " AND ")
				// When combining with filters, wrap grep part in parens
				// for correct SQL operator precedence: (pattern OR clauses) AND filter
				if hasFilters {
					grepPart = "(" + grepPart + ")"
				}
				allWhereParts = append(allWhereParts, grepPart)
			}
		}
	}

	// Часть 2: фильтры → Condition-based WHERE
	if hasFilters {
		if len(allWhereParts) > 0 {
			// AND between grep and filter parts — handled via RawWhere + Conditions
			// This is complex: we can't mix RawWhere and Condition[] in the engine cleanly.
			// Instead, convert conditions into RawWhere.
			var filterWhereParts []string
			for _, cond := range conditions {
				qName := cond.Field
				switch cond.Operator {
				case query.OpEq:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					op := "="
					if cond.Not {
						op = "!="
					}
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s %s %s", qName, op, ph))
					allArgs = append(allArgs, cond.Value)
				case query.OpGt:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s > %s", qName, ph))
					allArgs = append(allArgs, cond.Value)
				case query.OpGte:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s >= %s", qName, ph))
					allArgs = append(allArgs, cond.Value)
				case query.OpLt:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s < %s", qName, ph))
					allArgs = append(allArgs, cond.Value)
				case query.OpLte:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s <= %s", qName, ph))
					allArgs = append(allArgs, cond.Value)
				case query.OpILike:
					ph := a.TranslatePlaceholder(phIdx)
					phIdx++
					if a.IsPostgres() {
						filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s ILIKE %s", qName, ph))
					} else {
						filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s COLLATE NOCASE LIKE %s", qName, ph))
					}
					allArgs = append(allArgs, cond.Value)
				case query.OpIn:
					var placeholders []string
					for _, v := range cond.Values {
						ph := a.TranslatePlaceholder(phIdx)
						phIdx++
						placeholders = append(placeholders, ph)
						allArgs = append(allArgs, v)
					}
					filterWhereParts = append(filterWhereParts, fmt.Sprintf("%s IN (%s)", qName, strings.Join(placeholders, ", ")))
				default:
					continue
				}
			}
			if len(filterWhereParts) > 0 {
				allWhereParts = append(allWhereParts, "("+strings.Join(filterWhereParts, " AND ")+")")
			}
		} else {
			// Only filters — use Condition-based Where (clean, engine handles it)
			return &query.QueryPlan{
				Select:  selectClause(entity, q, a),
				From:    a.QuoteIdentifier(entity.Table),
				Where:   conditions,
				Limit:   parseFilterLimit(q),
				Offset:  parseOffset(q),
				Order:   parseOrder(q, entity, a),
				Format:  parseFormat(q),
			}, nil
		}
	}

	// ── Если только pattern (без фильтров) → RawWhere путь ──────────
	if hasPattern && !hasFilters {
		// Use the plan directly with RawWhere — same as grep.go
		if len(allWhereParts) == 0 {
			// All tokens skipped (no string fields) → list
			return &query.QueryPlan{
				Select:  selectClause(entity, q, a),
				From:    a.QuoteIdentifier(entity.Table),
				Limit:   parseLimit(q),
				Offset:  parseOffset(q),
				Order:   parseOrder(q, entity, a),
				Format:  parseFormat(q),
			}, nil
		}
	}

	// ── If we get here, we have RawWhere parts ──────────────────────
	if len(allWhereParts) == 0 {
		return nil, fmt.Errorf("no search conditions could be built from the given parameters")
	}

	limit := parseLimit(q)
	if hasFilters {
		limit = parseFilterLimit(q)
	}

	return &query.QueryPlan{
		Select:       selectClause(entity, q, a),
		From:         a.QuoteIdentifier(entity.Table),
		RawWhere:     strings.Join(allWhereParts, " AND "),
		RawWhereArgs: allArgs,
		Limit:        limit,
		Offset:       parseOffset(q),
		Order:        parseOrder(q, entity, a),
		Format:       parseFormat(q),
	}, nil
}
