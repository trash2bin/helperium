package handlers

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// NewStrategyHandler creates a generic HTTP handler for any search.Strategy.
//
// Flow:
//  1. Resolve entity via c.Resolver
//  2. Strategy parses HTTP request into query.QueryPlan
//  3. query.Engine builds SQL (+ tenant filter where possible)
//  4. COUNT + SELECT execution
//  5. Row mapping via c.Builder.MapRow + query.FormatRows
//
// Tenant row-level isolation:
//   - For []Condition-based plans: injected into the WHERE clause
//   - For RawWhere plans (grep with multi-token AND): wrapped in a
//     subquery to ensure tenant filter is always applied.
func NewStrategyHandler(c *Context, strategy search.Strategy, entityName string, entityCfg config.Entity) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		qCtx, qCancel := c.queryCtx(r)
		if qCancel != nil {
			defer qCancel()
		}
		entity, ok := c.Resolver.Resolve(entityName)
		if !ok {
			RespondError(w, http.StatusInternalServerError, "config_error", "entity not found")
			return
		}

		// Bridge runtime.AdapterSubset => query.AdapterSubset
		qAdapter := &runtime.AdapterToQuery{Inner: c.Adapter}
		searchAdapter := search.NewAdapter(qAdapter)

		plan, err := strategy.ParseRequest(r, entityCfg, searchAdapter)
		if err != nil {
			RespondError(w, http.StatusBadRequest, "parse_error", err.Error())
			return
		}

		engine := query.NewEngine(qAdapter)
		translate := asPlaceholderFunc(c.Adapter)

		// Tenant filter (row-level isolation)
		tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), 0, translate)

		if plan.Format == query.FormatCount {
			sqlStr, args, err := engine.BuildCount(*plan)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
				return
			}
			if tenantWhere != "" {
				sqlStr = "SELECT COUNT(*) FROM (" + sqlStr + ") AS _cnt WHERE " + tenantWhere
				args = append(args, tenantArgs...)
			}
			rows, err := c.DB.QueryContext(qCtx, sqlStr, args...)
			if err != nil {
				slog.Error("DB error in strategy handler count", "err", err, "strategy", strategy.Name(), "entity", entityName)
				RespondError(w, http.StatusInternalServerError, "db_error",
					"Query execution failed. Check field names via schema tool.")
				return
			}
			defer rows.Close() //nolint:errcheck
			var count int
			if rows.Next() {
				_ = rows.Scan(&count)
			}
			RespondJSON(w, http.StatusOK, map[string]any{
				"entity": entityName,
				"count":  count,
			})
			return
		}

		// Build the SELECT query
		sqlStr, args, err := engine.Build(*plan)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
			return
		}

		// Apply tenant filter
		if tenantWhere != "" {
			if plan.RawWhere != "" {
				slog.Debug("strategy handler: wrapping RawWhere query in subquery for tenant filter",
					"strategy", strategy.Name(), "entity", entityName)
				sqlStr = "SELECT * FROM (" + sqlStr + ") AS _t WHERE " + tenantWhere
				args = append(args, tenantArgs...)
			} else if len(plan.Where) > 0 {
				sqlStr, args = insertTenantBeforeLimit(sqlStr, args, " AND "+tenantWhere, tenantArgs)
			} else {
				sqlStr, args = insertTenantBeforeLimit(sqlStr, args, " WHERE "+tenantWhere, tenantArgs)
			}
		}

		// Count for pagination
		countSQL := countQuery(sqlStr)

		total := runCountQuery(qCtx, c.DB, countSQL, args)

		// Execute SELECT
		rows, err := c.DB.QueryContext(qCtx, sqlStr, args...)
		if err != nil {
			slog.Error("DB error in strategy handler", "err", err, "strategy", strategy.Name(), "entity", entityName)
			RespondError(w, http.StatusInternalServerError, "db_error",
				"Query execution failed. Check field names via schema tool.")
			return
		}
		defer rows.Close() //nolint:errcheck

		results, err := c.Builder.MapRows(rows, func(rows *sql.Rows) (map[string]any, error) {
			return c.Builder.MapRow(rows, entity)
		}, 10000)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
			return
		}

		result := query.FormatRows(results, total, plan.Format, strategy.EntityIDCol(), strategy.EntityNameCol())

		// If no results, add LLM hint with available distinct values
		if total == 0 {
			hint := collectEmptyHint(qCtx, c.DB, entityCfg, searchAdapter)
			if hint != nil {
				result.EmptyHint = hint
			}
		}

		RespondJSON(w, http.StatusOK, result)
	}
}

// collectEmptyHint builds a hint for the LLM when search returns zero results.
// For each string field, it fetches up to 5 distinct values.
func collectEmptyHint(ctx context.Context, db runtime.AdapterSubset, entity config.Entity, a search.Adapter) *query.EmptyHint {
	if entity.Name == "" {
		return nil
	}

	qTable := a.QuoteIdentifier(entity.Table)
	suggested := fmt.Sprintf("Try schema_%s() to discover available values, then retry with exact values.", entity.Name)

	hint := &query.EmptyHint{
		SuggestedAction: suggested,
		AvailableValues: make(map[string][]string),
	}

	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type != config.FieldTypeString {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}

		qCol := a.QuoteIdentifier(f.Column)
		distinctSQL := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL ORDER BY %s LIMIT 5", qCol, qTable, qCol, qCol)

		rows, err := db.QueryContext(ctx, distinctSQL)
		if err != nil {
			slog.Debug("collectEmptyHint: query failed", "field", f.Name, "err", err)
			continue
		}

		var vals []string
		for rows.Next() {
			var v string
			if err := rows.Scan(&v); err != nil {
				continue
			}
			vals = append(vals, v)
		}
		rows.Close()

		if len(vals) > 0 {
			hint.AvailableValues[f.Name] = vals
		}
	}

	if len(hint.AvailableValues) == 0 {
		return nil
	}
	return hint
}

// insertTenantBeforeLimit inserts a SQL fragment before the LIMIT/OFFSET clause
// and reorders args so that WHERE args, tenant args, and LIMIT/OFFSET args
// appear in the correct order.
func insertTenantBeforeLimit(sql string, args []any, tenantClause string, tenantArgs []any) (string, []any) {
	upper := strings.ToUpper(sql)
	lastLimit := strings.LastIndex(upper, " LIMIT ")
	lastOffset := strings.LastIndex(upper, " OFFSET ")

	// Count how many trailing args belong to LIMIT/OFFSET
	limitOffsetCount := 0
	if lastOffset >= 0 {
		limitOffsetCount++ // OFFSET arg
	}
	if lastLimit >= 0 {
		limitOffsetCount++ // LIMIT arg
	}

	// Split args: WHERE args vs LIMIT/OFFSET args
	whereArgsLen := len(args) - limitOffsetCount
	if whereArgsLen < 0 {
		whereArgsLen = 0
	}
	whereArgs := args[:whereArgsLen]
	limitOffsetArgs := args[whereArgsLen:]

	// Rebuild: WHERE args + tenant args + LIMIT/OFFSET args
	newArgs := make([]any, 0, len(args)+len(tenantArgs))
	newArgs = append(newArgs, whereArgs...)
	newArgs = append(newArgs, tenantArgs...)
	newArgs = append(newArgs, limitOffsetArgs...)

	// Insert tenant clause before LIMIT
	var newSQL string
	if lastLimit >= 0 {
		newSQL = sql[:lastLimit] + tenantClause + sql[lastLimit:]
	} else {
		newSQL = sql + tenantClause
	}

	return newSQL, newArgs
}
