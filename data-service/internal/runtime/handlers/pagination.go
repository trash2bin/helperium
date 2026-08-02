package handlers

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

const (
	defaultLimit = 100
	maxLimit     = 1000
	maxOffset    = 100000
)

// readPagination извлекает limit и offset из query params.
func readPagination(r *http.Request) (limit, offset int) {
	limit = defaultLimit
	offset = 0

	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 {
			limit = parsed
			if limit > maxLimit {
				limit = maxLimit
			}
		}
	}
	if o := r.URL.Query().Get("offset"); o != "" {
		if parsed, err := strconv.Atoi(o); err == nil && parsed >= 0 {
			offset = parsed
			if offset > maxOffset {
				offset = maxOffset
			}
		}
	}
	return limit, offset
}

// appendPagination добавляет LIMIT и OFFSET к SQL запросу.
func appendPagination(sql string, limit, offset int) string {
	sql += fmt.Sprintf(" LIMIT %d OFFSET %d", limit, offset)
	return sql
}

// countQuery заменяет SELECT ... FROM на SELECT COUNT(*) FROM, сохраняя WHERE.
// Удаляет LIMIT/OFFSET если есть — COUNT не нуждается в пагинации.
func countQuery(selectSQL string) string {
	upper := strings.ToUpper(selectSQL)
	idx := strings.Index(upper, " FROM ")
	if idx < 0 {
		return ""
	}
	result := "SELECT COUNT(*)" + selectSQL[idx:]
	// Удаляем LIMIT ... OFFSET ...
	limIdx := strings.LastIndex(strings.ToUpper(result), " LIMIT ")
	if limIdx > 0 {
		result = strings.TrimSpace(result[:limIdx])
	}
	return result
}

// countQueryWithArgs возвращает count SQL и args без LIMIT/OFFSET аргументов.
func countQueryWithArgs(selectSQL string, args []any) (string, []any) {
	upper := strings.ToUpper(selectSQL)
	idx := strings.Index(upper, " FROM ")
	if idx < 0 {
		return "", nil
	}
	result := "SELECT COUNT(*)" + selectSQL[idx:]
	// Удаляем LIMIT ... OFFSET ...
	limIdx := strings.LastIndex(strings.ToUpper(result), " LIMIT ")
	offIdx := strings.LastIndex(strings.ToUpper(result), " OFFSET ")

	// Count how many trailing args belong to LIMIT/OFFSET
	limitOffsetCount := 0
	if offIdx >= 0 {
		limitOffsetCount++ // OFFSET arg
	}
	if limIdx >= 0 {
		limitOffsetCount++ // LIMIT arg
	}

	// Split args: WHERE args vs LIMIT/OFFSET args
	whereArgsLen := len(args) - limitOffsetCount
	if whereArgsLen < 0 {
		whereArgsLen = 0
	}
	whereArgs := args[:whereArgsLen]

	if limIdx > 0 {
		result = strings.TrimSpace(result[:limIdx])
	}
	return result, whereArgs
}

// runCountQuery выполняет COUNT запрос и возвращает общее число записей.
// При ошибке логирует её и возвращает -1 (вызывающий код видит невалидное
// значение, а не тихо получает 0).
func runCountQuery(ctx context.Context, db runtime.AdapterSubset, countSQL string, args []any) int {
	rows, err := db.QueryContext(ctx, countSQL, args...)
	if err != nil {
		slog.Error("runCountQuery: count query failed", "err", err, "sql", countSQL)
		return -1
	}
	defer rows.Close() //nolint:errcheck
	var total int
	if rows.Next() {
		if err := rows.Scan(&total); err != nil {
			slog.Error("runCountQuery: scan failed", "err", err)
			return -1
		}
	}
	if err := rows.Err(); err != nil {
		slog.Error("runCountQuery: rows iteration error", "err", err)
		return -1
	}
	return total
}
