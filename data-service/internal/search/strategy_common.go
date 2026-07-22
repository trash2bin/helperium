// Package search — общие утилиты для стратегий поиска.
//
// Все функции отсюда доступны grep.go, filter.go, search.go, simple.go
// через package-level namespace.
package search

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// =============================================================================
// Shared utility functions for search strategies
// =============================================================================

// stringFields returns all string fields of an entity (excluding PK).
func stringFields(entity config.Entity) []config.EntityField {
	var result []config.EntityField
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Column == "tenant_id" {
			continue // Tenant isolation: не допускаем поиск по tenant_id
		}
		if f.Type == config.FieldTypeString {
			result = append(result, f)
		}
	}
	return result
}

// regexOp returns the regex operator for the database.
func regexOp(a Adapter) string {
	if a.IsPostgres() {
		return "~"
	}
	return "REGEXP"
}

// tokenize splits a string into words (by whitespace) and trims each.
func tokenize(s string) []string {
	parts := strings.Fields(s)
	if len(parts) == 0 {
		return nil
	}
	return parts
}

// parseBoolParam extracts a bool from query params with the given default.
func parseBoolParam(q map[string][]string, name string, def bool) bool {
	vals, ok := q[name]
	if !ok || len(vals) == 0 {
		return def
	}
	switch strings.ToLower(strings.TrimSpace(vals[0])) {
	case "true", "1", "yes":
		return true
	case "false", "0", "no":
		return false
	default:
		return def
	}
}

// parseLimitParam extracts limit from query params with the given default.
func parseLimitParam(q map[string][]string, defaultLimit int) int {
	vals, ok := q["limit"]
	if !ok || len(vals) == 0 {
		return defaultLimit
	}
	v, err := strconv.Atoi(strings.TrimSpace(vals[0]))
	if err != nil || v <= 0 {
		return defaultLimit
	}
	if v > 100 {
		return 100
	}
	return v
}

// parseOffset extracts offset from query params.
func parseOffset(q map[string][]string) int {
	vals, ok := q["offset"]
	if !ok || len(vals) == 0 {
		return 0
	}
	v, err := strconv.Atoi(strings.TrimSpace(vals[0]))
	if err != nil || v < 0 {
		return 0
	}
	return v
}

// parseFormat extracts format from query params.
func parseFormat(q map[string][]string) query.ResponseFormat {
	vals, ok := q["format"]
	if !ok || len(vals) == 0 {
		return query.FormatCompact
	}
	switch strings.ToLower(strings.TrimSpace(vals[0])) {
	case "full":
		return query.FormatFull
	case "count":
		return query.FormatCount
	default:
		return query.FormatCompact
	}
}

// parseOrder parses sort_by into OrderClause.
func parseOrder(q map[string][]string, entity config.Entity, a Adapter) []query.OrderClause {
	vals, ok := q["sort_by"]
	if !ok || len(vals) == 0 {
		return nil
	}
	sortBy := strings.TrimSpace(vals[0])
	if sortBy == "" {
		return nil
	}

	desc := false
	fieldName := sortBy
	if strings.HasPrefix(sortBy, "-") {
		desc = true
		fieldName = strings.TrimPrefix(sortBy, "-")
	}

	// Find the column by public name.
	colName := findColumn(entity, fieldName)
	if colName == "" {
		return nil
	}
	return []query.OrderClause{
		{Field: a.QuoteIdentifier(colName), Desc: desc},
	}
}

// findColumn looks up the DB column name for a given public field name.
func findColumn(entity config.Entity, fieldName string) string {
	for _, f := range entity.Fields {
		if f.Name == fieldName {
			return f.Column
		}
	}
	return ""
}

// selectClause creates a SelectClause based on the response format.
func selectClause(entity config.Entity, q map[string][]string, a Adapter) query.SelectClause {
	format := parseFormat(q)
	switch format {
	case query.FormatFull:
		cols := make([]string, 0, len(entity.Fields))
		for _, f := range entity.Fields {
			cols = append(cols, a.QuoteIdentifier(f.Column))
		}
		return query.SelectClause{Columns: cols}
	case query.FormatCount:
		return query.SelectClause{}
	default: // compact: id + first string field
		cols := []string{a.QuoteIdentifier(entity.IDColumn)}
		for _, f := range entity.Fields {
			if f.Type == config.FieldTypeString {
				cols = append(cols, a.QuoteIdentifier(f.Column))
				break
			}
		}
		return query.SelectClause{Columns: cols}
	}
}

// selectClauseFull creates a SelectClause with all entity columns.
func selectClauseFull(entity config.Entity, a Adapter) query.SelectClause {
	cols := make([]string, 0, len(entity.Fields))
	for _, f := range entity.Fields {
		cols = append(cols, a.QuoteIdentifier(f.Column))
	}
	return query.SelectClause{Columns: cols}
}

// makeEqCondition creates a Condition for exact comparison based on field type.
func makeEqCondition(qName string, f config.EntityField, val string) (query.Condition, error) {
	typed, err := convertValue(val, f.Type)
	if err != nil {
		return query.Condition{}, err
	}
	return query.Condition{
		Field:    qName,
		Operator: query.OpEq,
		Value:    typed,
	}, nil
}

// makeComparison creates a Condition for a comparison operator.
func makeComparison(qName, op string, f config.EntityField, val string) (query.Condition, error) {
	typed, err := convertValue(val, f.Type)
	if err != nil {
		return query.Condition{}, err
	}

	var operator query.Operator
	switch op {
	case "gt":
		operator = query.OpGt
	case "lt":
		operator = query.OpLt
	case "gte":
		operator = query.OpGte
	case "lte":
		operator = query.OpLte
	default:
		return query.Condition{}, fmt.Errorf("unknown comparison op: %s", op)
	}

	return query.Condition{
		Field:    qName,
		Operator: operator,
		Value:    typed,
	}, nil
}

// convertValue converts a string value to the typed value based on FieldType.
func convertValue(val string, ft config.FieldType) (any, error) {
	switch ft {
	case config.FieldTypeInt:
		return strconv.ParseInt(strings.TrimSpace(val), 10, 64)
	case config.FieldTypeFloat:
		return strconv.ParseFloat(strings.TrimSpace(val), 64)
	case config.FieldTypeBool:
		return strconv.ParseBool(strings.TrimSpace(val))
	default:
		return val, nil
	}
}

// fieldTypeToParamType converts a FieldType to a ParamType.
func fieldTypeToParamType(ft config.FieldType) config.ParamType {
	switch ft {
	case config.FieldTypeInt:
		return config.ParamTypeInt
	case config.FieldTypeFloat:
		return config.ParamTypeFloat
	case config.FieldTypeBool:
		return config.ParamTypeBool
	default:
		return config.ParamTypeString
	}
}
