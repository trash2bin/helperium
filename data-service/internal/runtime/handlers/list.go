package handlers

import (
	"database/sql"
	"log/slog"
	"net/http"
)

// ListHandler обрабатывает GET /entity.
func ListHandler(c *Context, entityName string) http.HandlerFunc {
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

		// Row-level tenant filter
		translate := asPlaceholderFunc(c.Adapter)
		tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), 0, translate)

		query, err := c.Builder.BuildList(entity, tenantWhere, tenantArgs)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
			return
		}

		// Pagination
		limit, offset := readPagination(r)
		countSQL := countQuery(query.SQL)
		total := runCountQuery(qCtx, c.DB, countSQL, query.Args)
		query.SQL = appendPagination(query.SQL, limit, offset)

		rows, err := c.DB.QueryContext(qCtx, query.SQL, query.Args...)
		if err != nil {
			slog.Error("DB error in list", "err", err, "tenant", c.tenantID(r), "entity", entityName)
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

		setPaginationHeaders(w, total, limit, offset)
		RespondJSON(w, http.StatusOK, results)
	}
}
