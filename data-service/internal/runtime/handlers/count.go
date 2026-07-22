package handlers

import (
	"fmt"
	"net/http"
	"strings"
)

// CountHandler обрабатывает GET /entity/count?status=new&...
// Возвращает количество записей, соответствующих фильтрам.
//
// Пример: GET /orders/count?status=new → {"count": 42}
//
// Используй вместо find_*, когда нужно узнать КОЛИЧЕСТВО записей,
// а не сами данные — это быстрее и дешевле по токенам.
func CountHandler(c *Context, entityName string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entity, ok := c.Resolver.Resolve(entityName)
		if !ok {
			RespondError(w, http.StatusInternalServerError, "config_error", "entity not found")
			return
		}

		translate := asPlaceholderFunc(c.Adapter)

		// Собираем query-параметры как фильтры, поддерживая field__op синтаксис
		var filterCols []string
		var filterVals []any
		var filterOps []string

		// Build set of filterable field names (non-PK)
		fieldMap := make(map[string]struct{}, len(entity.Fields))
		for _, f := range entity.Fields {
			if !f.PrimaryKey {
				fieldMap[f.Name] = struct{}{}
			}
		}

		for key, vals := range r.URL.Query() {
			if len(vals) == 0 || vals[0] == "" {
				continue
			}
			val := vals[0]

			// Skip system params
			if key == "limit" || key == "offset" || key == "sort_by" || key == "format" {
				continue
			}

			// Parse field__op syntax
			fieldName, op, found := strings.Cut(key, "__")
			if !found {
				fieldName = key
				op = "eq"
			}

			if _, ok := fieldMap[fieldName]; !ok {
				continue
			}

			// Map __op to filter operator
			var filterOp string
			switch op {
			case "eq", "gt", "gte", "lt", "lte":
				filterOp = op
			case "like":
				filterOp = "like"
			case "neq":
				filterOp = "neq"
			case "in":
				filterOp = "in"
			default:
				continue
			}

			filterCols = append(filterCols, fieldName)
			filterVals = append(filterVals, val)
			filterOps = append(filterOps, filterOp)
		}

		// Строим SELECT COUNT(*) вместо SELECT ...
		var countSQL string
		var args []any

		if len(filterCols) == 0 {
			countSQL = fmt.Sprintf("SELECT COUNT(*) FROM %s", c.Adapter.QuoteIdentifier(entity.Table))
		} else {
			// Используем BuildFilter чтобы получить WHERE-условия,
			// но заменяем SELECT ... на SELECT COUNT(*)
			query, err := c.Builder.BuildFilter(entity, filterCols, filterVals, filterOps)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
				return
			}
			// Заменяем "SELECT ... FROM" на "SELECT COUNT(*) FROM"
			upper := strings.ToUpper(query.SQL)
			fromIdx := strings.Index(upper, " FROM ")
			if fromIdx > 0 {
				countSQL = "SELECT COUNT(*)" + query.SQL[fromIdx:]
			} else {
				countSQL = "SELECT COUNT(*) FROM " + c.Adapter.QuoteIdentifier(entity.Table)
			}
			args = query.Args
		}

		// Добавляем tenant-фильтр
		tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(args), translate)
		if tenantWhere != "" {
			if strings.Contains(strings.ToUpper(countSQL), " WHERE ") {
				countSQL += " AND " + tenantWhere
			} else {
				countSQL += " WHERE " + tenantWhere
			}
			args = append(args, tenantArgs...)
		}

		// Выполняем COUNT запрос
		rows, err := c.DB.QueryContext(r.Context(), countSQL, args...)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "db_error", err.Error())
			return
		}
		defer rows.Close() //nolint:errcheck

		var count int
		if rows.Next() {
			if err := rows.Scan(&count); err != nil {
			RespondError(w, http.StatusInternalServerError, "scan_error",
				fmt.Sprintf("failed to scan count for %q: %v", entityName, err))
			return
		}
		}

		RespondJSON(w, http.StatusOK, map[string]any{
			"entity": entityName,
			"count":  count,
		})
	}
}
