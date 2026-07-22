package handlers

import (
	"database/sql"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
)

// FindHandler обрабатывает поиск по полям (напр. /students?name=...&course=3).
// Поддерживает множественную фильтрацию: все непустые query-параметры
// применяются как WHERE-условия (AND).
//
// Поведение:
//   - Если нет активных фильтров → fallback на список всех записей (массив)
//   - Если 1 фильтр (search field) → старое поведение: один объект или 404
//   - Если несколько фильтров → массив результатов (включая пустой [])
func FindHandler(c *Context, entityName, searchField, queryParam string) http.HandlerFunc {
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

		translate := asPlaceholderFunc(c.Adapter)

		// Собираем все непустые query-параметры как фильтры
		var filterCols []string
		var filterVals []any
		var filterOps []string

		// Known filter params: все колонки entity (кроме PK)
		for _, f := range entity.Fields {
			if f.PrimaryKey {
				continue
			}
			val := r.URL.Query().Get(f.Name)
			if val == "" {
				continue
			}

			// Validate value length
			if err := ValidateSearchValue(val); err != nil {
				RespondError(w, http.StatusBadRequest, "validation_error",
					fmt.Sprintf("param %q: %v", f.Name, err))
				return
			}

			filterCols = append(filterCols, f.Name)

			// Определяем тип операции по типу поля
			switch f.Type {
			case "int":
				if intVal, err := strconv.ParseInt(val, 10, 64); err == nil {
					filterVals = append(filterVals, intVal)
					filterOps = append(filterOps, "eq")
				} else {
					RespondError(w, http.StatusBadRequest, "validation_error",
						fmt.Sprintf("param %q: expected integer, got %q", f.Name, val))
					return
				}
			case "float":
				if floatVal, err := strconv.ParseFloat(val, 64); err == nil {
					filterVals = append(filterVals, floatVal)
					filterOps = append(filterOps, "eq")
				} else {
					RespondError(w, http.StatusBadRequest, "validation_error",
						fmt.Sprintf("param %q: expected number, got %q", f.Name, val))
					return
				}
			default:
				// string, bool, datetime — LIKE поиск
				filterVals = append(filterVals, val)
				filterOps = append(filterOps, "like")
			}
		}

		// Если нет активных фильтров — fallback на список всех записей
		if len(filterCols) == 0 {
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
				slog.Error("DB error in find", "err", err, "tenant", c.tenantID(r), "entity", entityName)
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
			return
		}

		// BuildFilter с множественными условиями
		query, err := c.Builder.BuildFilter(entity, filterCols, filterVals, filterOps)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
			return
		}

		// Добавляем tenant-фильтр
		tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(query.Args), translate)
		if tenantWhere != "" {
			query.SQL += " AND " + tenantWhere
			query.Args = append(query.Args, tenantArgs...)
		}

		// Pagination
		limit, offset := readPagination(r)
		countSQL := countQuery(query.SQL)
		total := runCountQuery(qCtx, c.DB, countSQL, query.Args)
		query.SQL = appendPagination(query.SQL, limit, offset)

		rows, err := c.DB.QueryContext(qCtx, query.SQL, query.Args...)
		if err != nil {
			slog.Error("DB error in find", "err", err, "tenant", c.tenantID(r), "entity", entityName)
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

		// Всегда возвращаем массив для консистентности (включая пустой).
		// LLM корректно обрабатывает пустой массив [] — это не ошибка.
		RespondJSON(w, http.StatusOK, results)
	}
}
