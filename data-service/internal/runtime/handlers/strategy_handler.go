package handlers

import (
	"database/sql"
	"log/slog"
	"net/http"

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
		entity, ok := c.Resolver.Resolve(entityName)
		if !ok {
			RespondError(w, http.StatusInternalServerError, "config_error", "entity not found")
			return
		}

		// Bridge runtime.AdapterSubset → query.AdapterSubset
		qAdapter := runtimeToQueryAdapter{c.Adapter}
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
				if plan.RawWhere != "" {
					// Wrap in subquery for RawWhere (same as SELECT path)
					sqlStr = "SELECT COUNT(*) FROM (" + sqlStr + ") AS _cnt WHERE " + tenantWhere
				} else {
					sqlStr = "SELECT COUNT(*) FROM (" + sqlStr + ") AS _cnt WHERE " + tenantWhere
				}
				args = append(args, tenantArgs...)
			}
			rows, err := c.DB.QueryContext(r.Context(), sqlStr, args...)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "db_error", err.Error())
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

		// Count for pagination (tenant-aware), вычисляется до tenant filter
		// чтобы потом включить tenant условия
		countSQL := countQuery(sqlStr)

		// Apply tenant filter
		if tenantWhere != "" {
			if plan.RawWhere != "" {
				// RawWhere содержит сложную WHERE-логику (AND/OR, multi-token).
				// Не можем просто добавить AND — нарушит логику выражений.
				// Вместо этого оборачиваем весь запрос в подзапрос:
				//   SELECT * FROM (<original_query>) AS _t WHERE <tenant_where>
				slog.Debug("strategy handler: wrapping RawWhere query in subquery for tenant filter",
					"strategy", strategy.Name(), "entity", entityName)
				sqlStr = "SELECT * FROM (" + sqlStr + ") AS _t WHERE " + tenantWhere
				args = append(args, tenantArgs...)
				// COUNT тоже нужно пересчитать через подзапрос после tenant filter
				countSQL = countQuery(sqlStr)
			} else if len(plan.Where) > 0 {
				sqlStr += " AND " + tenantWhere
				args = append(args, tenantArgs...)
			} else {
				sqlStr += " WHERE " + tenantWhere
				args = append(args, tenantArgs...)
			}
		}

		total := runCountQuery(r.Context(), c.DB, countSQL, args)

		// Execute SELECT
		rows, err := c.DB.QueryContext(r.Context(), sqlStr, args...)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "db_error", err.Error())
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
		RespondJSON(w, http.StatusOK, result)
	}
}

// runtimeToQueryAdapter bridges runtime.AdapterSubset to query.AdapterSubset.
// Both interfaces have overlapping method sets; this wrapper ensures
// runtime.AdapterSubset satisfies query.AdapterSubset without import cycles.
type runtimeToQueryAdapter struct {
	inner runtime.AdapterSubset
}

func (w runtimeToQueryAdapter) TranslatePlaceholder(index int) string { return w.inner.TranslatePlaceholder(index) }
func (w runtimeToQueryAdapter) QuoteIdentifier(name string) string    { return w.inner.QuoteIdentifier(name) }

// QuoteString экранирует LIKE-специальные символы '%' и '_'.
// DB-agnostic реализация, так как runtime.AdapterSubset не включает QuoteString.
func (w runtimeToQueryAdapter) QuoteString(s string) string {
	escaped := ""
	for _, c := range s {
		if c == '%' || c == '_' {
			escaped += "\\"
		}
		escaped += string(c)
	}
	return escaped
}
