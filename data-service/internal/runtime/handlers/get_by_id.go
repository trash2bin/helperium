package handlers

import (
	"log/slog"
	"net/http"
)

// GetByIDHandler обрабатывает GET /entity/{id}.
func GetByIDHandler(c *Context, entityName string) http.HandlerFunc {
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

		id := c.URLParam(r, "id")
		if err := ValidateID(id); err != nil {
			RespondError(w, http.StatusBadRequest, "validation_error", err.Error())
			return
		}

		query, err := c.Builder.BuildGetByID(entity, id)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
			return
		}

		// Tenant filter
		translate := asPlaceholderFunc(c.Adapter)
		tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(query.Args), translate)
		if tenantWhere != "" {
			query.SQL += " AND " + tenantWhere
			query.Args = append(query.Args, tenantArgs...)
		}

		rows, err := c.DB.QueryContext(qCtx, query.SQL, query.Args...)
		if err != nil {
			slog.Error("DB error in get_by_id", "err", err, "tenant", c.tenantID(r), "entity", entityName)
			RespondError(w, http.StatusInternalServerError, "db_error",
				"Query execution failed. Check field names via schema tool.")
			return
		}
		defer rows.Close() //nolint:errcheck

		if !rows.Next() {
			RespondJSON(w, http.StatusNotFound, map[string]string{
				"error":   "not_found",
				"message": "resource not found",
			})
			return
		}

		row, err := c.Builder.MapRow(rows, entity)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
			return
		}

		RespondJSON(w, http.StatusOK, row)
	}
}
