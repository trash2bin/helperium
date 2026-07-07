package handlers

import (
	"database/sql"
	"fmt"
	"net/http"
	"strconv"

	"github.com/agent-tutor/agent-tutor-go/config"
)

// CustomQueryHandler обрабатывает эндпоинты с операцией custom_query.
//
// Tenant row-level фильтрация НЕ применяется автоматически для custom queries —
// запрос должен включать tenant_id в SQL самостоятельно. Это осознанное решение:
// custom queries — это JOIN'ы и сложные выражения, где автоматическая подстановка
// WHERE была бы ненадёжной.
func CustomQueryHandler(c *Context, queryID string, params []config.EndpointParam) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cq, ok := c.CustomQueries[queryID]
		if !ok {
			RespondError(w, http.StatusNotFound, "query_not_found",
				fmt.Sprintf("custom query %q not found", queryID))
			return
		}

		// Сбор аргументов из path и query параметров
		args := make([]any, 0, len(params))
		for _, p := range params {
			var val string
			var found bool

			if p.In == "path" {
				val = c.URLParam(r, p.Name)
				if val != "" {
					found = true
				}
			} else if p.In == "query" {
				val = r.URL.Query().Get(p.Name)
				if val != "" {
					found = true
				}
			}

			if !found && p.Required != nil && *p.Required {
				RespondError(w, http.StatusBadRequest, "missing_param",
					fmt.Sprintf("parameter %q is required", p.Name))
				return
			}

			parsed, err := parseParam(val, string(p.Type))
			if err != nil {
				RespondError(w, http.StatusBadRequest, "invalid_param",
					fmt.Sprintf("parameter %q: %v", p.Name, err))
				return
			}
			args = append(args, parsed)
		}

		query, err := c.Builder.BuildCustomQuery(cq, args)
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
			return c.Builder.MapCustomQueryRow(rows, cq.ResultMapping)
		}, cq.MaxRows)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
			return
		}

		RespondJSON(w, http.StatusOK, results)
	}
}

func parseParam(val, typ string) (any, error) {
	if val == "" {
		return nil, nil
	}
	switch typ {
	case "int":
		return strconv.Atoi(val)
	case "float":
		return strconv.ParseFloat(val, 64)
	case "bool":
		return strconv.ParseBool(val)
	case "string":
		return val, nil
	default:
		return val, nil // fallback — отдаём как строку
	}
}
