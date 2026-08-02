package handlers

import (
	"fmt"
	"log/slog"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

// columnFieldType возвращает тип колонки из entity.Fields по имени колонки.
func columnFieldType(entity runtime.Entity, column string) string {
	for _, f := range entity.Fields {
		if f.Column == column || f.Name == column {
			return f.Type
		}
	}
	return "string"
}

// DistinctHandler обрабатывает GET /entity/distinct?column=status.
// Возвращает уникальные значения указанной колонки (максимум 50).
// Используется агентами для определения допустимых значений enum-полей.
//
// Пример: GET /orders/distinct?column=status → ["new", "processing", "shipped"]
func DistinctHandler(c *Context, entityName string) http.HandlerFunc {
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

		column := r.URL.Query().Get("column")
		if column == "" {
			RespondError(w, http.StatusBadRequest, "missing_param",
				"parameter 'column' is required")
			return
		}

		// Проверяем что колонка существует в entity и находим её тип.
		foundCol := entity.FindColumn(column)
		if foundCol == "" {
			RespondError(w, http.StatusBadRequest, "invalid_column",
				fmt.Sprintf("column %q not found in entity %q", column, entityName))
			return
		}
		fieldType := columnFieldType(entity, foundCol)

		translate := asPlaceholderFunc(c.Adapter)

		// SELECT DISTINCT column FROM table WHERE column IS NOT NULL LIMIT 50
		query := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL ORDER BY %s LIMIT 50",
			c.Adapter.QuoteIdentifier(foundCol),
			c.Adapter.QuoteIdentifier(entity.Table),
			c.Adapter.QuoteIdentifier(foundCol),
			c.Adapter.QuoteIdentifier(foundCol),
		)

		// Добавляем tenant-фильтр ПЕРЕД LIMIT (после ORDER BY):
		// "... LIMIT 50 AND tenant..." — невалидный SQL.
		// existingArgCount=0: в query нет плейсхолдеров, кроме tenant.
		tenantWhere, tenantArgs, tenantDeny := tenantFilter(entityName, c.Auth, c.tenantID(r), 0, translate)
		if tenantDeny != tenantDenyNone {
			respondTenantDeny(w, tenantDeny)
			return
		}
		if tenantWhere != "" {
			upper := strings.ToUpper(query)
			if limIdx := strings.LastIndex(upper, " LIMIT "); limIdx >= 0 {
				query = query[:limIdx] + " AND " + tenantWhere + query[limIdx:]
			} else {
				query += " AND " + tenantWhere
			}
		}

		rows, err := c.DB.QueryContext(qCtx, query, tenantArgs...)
		if err != nil {
			slog.Error("DB error in distinct", "err", err, "tenant", c.tenantID(r), "entity", entityName)
			RespondError(w, http.StatusInternalServerError, "db_error",
				"Query execution failed. Check field names via schema tool.")
			return
		}
		defer rows.Close() //nolint:errcheck

		var values []any
		for rows.Next() {
			var raw any
			if err := rows.Scan(&raw); err != nil {
				slog.Warn("distinct: scan error", "err", err, "entity", entityName, "column", column)
				continue
			}
			if raw != nil {
				values = append(values, runtime.CoerceNative(raw, fieldType))
			}
		}
		if values == nil {
			values = []any{}
		}

		RespondJSON(w, http.StatusOK, map[string]any{
			"column":    column,
			"entity":    entityName,
			"values":    values,
			"count":     len(values),
			"truncated": len(values) >= 50,
		})
	}
}
