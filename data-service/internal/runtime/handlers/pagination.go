package handlers

import (
	"context"
	"log/slog"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

const (
	defaultLimit = 100
	maxLimit     = 1000
	maxOffset    = 100000
)

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
