package handlers

import (
	"database/sql"
	"net/http"
)

// ListHandler обрабатывает GET /entity.
func ListHandler(c *Context, entityName string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
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

		rows, err := c.DB.QueryContext(r.Context(), query.SQL, query.Args...)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "db_error", err.Error())
			return
		}
		defer rows.Close()

		results, err := c.Builder.MapRows(rows, func(rows *sql.Rows) (map[string]any, error) {
			return c.Builder.MapRow(rows, entity)
		}, 10000)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
			return
		}

		RespondJSON(w, http.StatusOK, results)
	}
}
