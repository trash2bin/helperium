package handlers

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// SchemaHandler — HTTP handler for schema_{entity}.
// Returns structured metadata about an entity: total count, distinct values, min/max/avg.
type StrategySchemaHandler struct {
	strategy *search.SchemaStrategy
	entity   config.Entity
	ctx      *Context
}

// NewStrategySchemaHandler creates a StrategySchemaHandler.
func NewStrategySchemaHandler(ctx *Context, strategy *search.SchemaStrategy, entity config.Entity) *StrategySchemaHandler {
	return &StrategySchemaHandler{strategy: strategy, entity: entity, ctx: ctx}
}

func (h *StrategySchemaHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	qTable := h.ctx.Adapter.QuoteIdentifier(h.entity.Table)
	translate := asPlaceholderFunc(h.ctx.Adapter)

	// Tenant filter (row-level isolation)
	tenantWhere, tenantArgs := tenantFilter(h.entity.Name, h.ctx.Auth, h.ctx.tenantID(r), 0, translate)

	// 1. Total count
	countSQL := fmt.Sprintf("SELECT COUNT(*) FROM %s", qTable)
	if tenantWhere != "" {
		countSQL += " WHERE " + tenantWhere
	}

	var total int64
	countRows, err := h.ctx.DB.QueryContext(ctx, countSQL, tenantArgs...)
	if err != nil {
		slog.Error("DB error in schema count", "err", err, "entity", h.entity.Name)
		RespondError(w, http.StatusInternalServerError, "query_failed", "Query execution failed.")
		return
	}
	if countRows.Next() {
		_ = countRows.Scan(&total)
	}
	countRows.Close() //nolint:errcheck

	// 2. For each field — collect metadata
	fields := make(map[string]any)
	for _, f := range h.strategy.FieldInfo(h.entity) {
		qCol := h.ctx.Adapter.QuoteIdentifier(f.Column)

		switch f.Type {
		case config.FieldTypeString:
			vals := h.distinctValues(r.Context(), qTable, qCol, tenantWhere, tenantArgs)
			fields[f.Name] = map[string]any{
				"type":     "string",
				"distinct": vals,
				"count":    len(vals),
			}

		case config.FieldTypeInt, config.FieldTypeFloat:
			stats := h.fieldStats(r.Context(), qTable, qCol, tenantWhere, tenantArgs)
			if stats != nil {
				fields[f.Name] = map[string]any{
					"type": string(f.Type),
					"min":  stats["min"],
					"max":  stats["max"],
					"avg":  stats["avg"],
				}
			} else {
				fields[f.Name] = map[string]any{"type": string(f.Type)}
			}

		case config.FieldTypeBool:
			vals := h.distinctValues(r.Context(), qTable, qCol, tenantWhere, tenantArgs)
			fields[f.Name] = map[string]any{
				"type":     "bool",
				"distinct": vals,
			}

		default:
			fields[f.Name] = map[string]any{"type": string(f.Type)}
		}
	}

	result := map[string]any{
		"entity": h.entity.Name,
		"total":  total,
		"fields": fields,
	}

	RespondJSON(w, http.StatusOK, result)
}

// distinctValues returns up to 20 distinct values for a column.
func (h *StrategySchemaHandler) distinctValues(rctx context.Context, qTable, qCol, tenantWhere string, tenantArgs []any) []string {
	query := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL ORDER BY %s LIMIT 20",
		qCol, qTable, qCol, qCol)
	if tenantWhere != "" {
		query += " AND " + tenantWhere
	}

	rows, err := h.ctx.DB.QueryContext(rctx, query, tenantArgs...)
	if err != nil {
		slog.Error("DB error in schema distinct", "err", err, "entity", h.entity.Name, "column", qCol)
		return nil
	}
	defer rows.Close() //nolint:errcheck

	var vals []string
	for rows.Next() {
		var val nullableString
		if err := rows.Scan(&val); err != nil {
			continue
		}
		if val.valid {
			vals = append(vals, val.value)
		}
	}
	if vals == nil {
		vals = []string{}
	}
	return vals
}

// nullableString is a simple nullable string scanner.
type nullableString struct {
	value string
	valid bool
}

func (ns *nullableString) Scan(src any) error {
	if src == nil {
		ns.valid = false
		return nil
	}
	ns.valid = true
	ns.value = fmt.Sprintf("%v", src)
	return nil
}

// fieldStats returns min/max/avg for a numeric field.
func (h *StrategySchemaHandler) fieldStats(rctx context.Context, qTable, qCol, tenantWhere string, tenantArgs []any) map[string]float64 {
	query := fmt.Sprintf("SELECT MIN(%s), MAX(%s), AVG(%s) FROM %s", qCol, qCol, qCol, qTable)
	if tenantWhere != "" {
		query += " WHERE " + tenantWhere
	}

	var min, max, avg nullableFloat
	statRows, err := h.ctx.DB.QueryContext(rctx, query, tenantArgs...)
	if err != nil {
		slog.Error("DB error in schema stats", "err", err, "entity", h.entity.Name, "column", qCol)
		return nil
	}
	if statRows.Next() {
		_ = statRows.Scan(&min, &max, &avg)
	}
	statRows.Close() //nolint:errcheck

	result := map[string]float64{}
	if min.valid {
		result["min"] = min.value
	}
	if max.valid {
		result["max"] = max.value
	}
	if avg.valid {
		result["avg"] = avg.value
	}
	return result
}

// nullableFloat is a simple nullable float64 scanner.
type nullableFloat struct {
	value float64
	valid bool
}

func (nf *nullableFloat) Scan(src any) error {
	if src == nil {
		nf.valid = false
		return nil
	}
	nf.valid = true
	nf.value = toFloat64(src)
	return nil
}

func toFloat64(v any) float64 {
	switch val := v.(type) {
	case float64:
		return val
	case int64:
		return float64(val)
	case int:
		return float64(val)
	default:
		return 0
	}
}
