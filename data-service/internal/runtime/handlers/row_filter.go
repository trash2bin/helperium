package handlers

import (
	"fmt"
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

// tenantFilter возвращает готовый WHERE-фрагмент с переведёнными плейсхолдерами
// и соответствующими args для row-level фильтрации по auth.row_filters.
//
// translatePlaceholder — функция адаптера для замены '?' на "$1"/"$2"/...
// (получает 1-based индекс). Индексы начинаются с len(existingArgs)+1 чтобы
// плейсхолдеры стыковались с основным запросом без конфликтов.
//
// Возвращает пустые строку/args если:
//   - auth не настроен или strategy != "header"
//   - нет фильтра для этой entity
//   - tenantID пуст (клиент не передал X-Tenant-ID)
func tenantFilter(
	entityName string,
	auth *config.AuthConfig,
	tenantID string,
	existingArgCount int,
	translate runtime.PlaceholderFunc,
) (whereClause string, args []any) {
	if auth == nil || auth.Strategy != config.AuthStrategyHeader {
		return "", nil
	}
	if tenantID == "" {
		return "", nil
	}

	for i := range auth.RowFilters {
		if auth.RowFilters[i].Entity == entityName {
			where := auth.RowFilters[i].Where
			ph := translate(existingArgCount + 1)
			where = strings.ReplaceAll(where, ":tenant_id", ph)
			return where, []any{tenantID}
		}
	}

	return "", nil
}

// asPlaceholderFunc извлекает функцию перевода плейсхолдеров из адаптера.
func asPlaceholderFunc(adapter runtime.AdapterSubset) runtime.PlaceholderFunc {
	if adapter == nil {
		return func(i int) string { return fmt.Sprintf("$%d", i) }
	}
	return adapter.TranslatePlaceholder
}
