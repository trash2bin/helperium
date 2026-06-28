package handlers

import (
	"database/sql"
	"net/http"
)

// FindHandler обрабатывает поиск по полю (напр. /students?name=...).
// Если параметр поиска не передан — fallback на список всех записей.
func FindHandler(c *Context, entityName, searchField, queryParam string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entity, ok := c.Resolver.Resolve(entityName)
		if !ok {
			RespondError(w, http.StatusInternalServerError, "config_error", "entity not found")
			return
		}

		paramName := queryParam
		if paramName == "" {
			paramName = searchField
		}

		value := r.URL.Query().Get(paramName)
		if value == "" {
			// Fallback — список всех записей
			query, err := c.Builder.BuildList(entity, "", nil)
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
			return
		}

		query, err := c.Builder.BuildFind(entity, searchField, value)
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