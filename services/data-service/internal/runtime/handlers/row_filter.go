package handlers

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// tenantDenyReason — причина отказа в tenant-изоляции (fail-closed).
// Позволяет хендлеру различать ошибку ЗАПРОСА (пустой tenant_id → 400)
// от ошибки КОНФИГА (нет row_filter для entity → 403/500). В проде это
// важно: «клиент прислал кривой запрос» и «у нас сломан конфиг» — разные
// инциденты.
type tenantDenyReason int

const (
	tenantDenyNone tenantDenyReason = iota // deny=false: изоляция не требуется/есть
	tenantDenyMissingTenantID              // header-auth, но X-Tenant-ID пуст (ошибка запроса → 400)
	tenantDenyMissingRowFilter             // header-auth, tenant_id есть, но entity не покрыта row_filter (ошибка конфига → 403)
)

// tenantFilter возвращает готовый WHERE-фрагмент с переведёнными плейсхолдерами
// и соответствующими args для row-level фильтрации по auth.row_filters.
//
// translatePlaceholder — функция адаптера для замены '?' на "$1"/"$2"/...
// (получает 1-based индекс). Индексы начинаются с len(existingArgs)+1 чтобы
// плейсхолдеры стыковались с основным запросом без конфликтов.
//
// Возвращает denyReason != tenantDenyNone если запрос ДОЛЖЕН быть отклонён
// (fail-closed, P0-1):
//   - tenantDenyMissingTenantID: auth.Strategy == header И tenantID пуст
//     (клиент не передал X-Tenant-ID) → ошибка запроса → 400
//   - tenantDenyMissingRowFilter: auth.Strategy == header И tenantID есть,
//     но нет RowFilter для этой entity → ошибка конфига → 403
//
// Возвращает denyReason == tenantDenyNone (пустые where/args) если:
//   - auth не настроен или strategy != "header" (single-tenant / без изоляции)
//
// Fail-closed: при настроенном header-auth отсутствие фильтра — это ошибка
// конфигурации, а не «нет изоляции». Раньше возвращалось ("", nil) → SQL без
// WHERE → тенант видел чужие строки (row_filter_security_test.go документировал
// уязвимость). Теперь denyReason сигнализирует хендлеру: верни 400/403.
func tenantFilter(
	entityName string,
	auth *config.AuthConfig,
	tenantID string,
	existingArgCount int,
	translate runtime.PlaceholderFunc,
) (whereClause string, args []any, denyReason tenantDenyReason) {
	if auth == nil || auth.Strategy != config.AuthStrategyHeader {
		return "", nil, tenantDenyNone
	}
	if tenantID == "" {
		// AuthStrategyHeader требует X-Tenant-ID. Пустой → deny (fail-closed),
		// ошибка запроса.
		return "", nil, tenantDenyMissingTenantID
	}

	for i := range auth.RowFilters {
		if auth.RowFilters[i].Entity == entityName {
			where := auth.RowFilters[i].Where
			ph := translate(existingArgCount + 1)
			where = strings.ReplaceAll(where, ":tenant_id", ph)
			return where, []any{tenantID}, tenantDenyNone
		}
	}

	// Auth настроен, tenantID есть, но RowFilter для entity отсутствует —
	// это ошибка онбординга (забыли прописать row_filters). Fail-closed:
	// deny (ошибка конфига), а не тихая отдача всех строк.
	return "", nil, tenantDenyMissingRowFilter
}

// respondTenantDeny — единая точка ответа при fail-closed tenant-отказе.
// Различает: пустой X-Tenant-ID (ошибка запроса → 400) vs отсутствие
// row_filter для entity (ошибка конфига → 403). В проде это разные
// инциденты: «клиент прислал кривой запрос» vs «конфиг сломан для всех».
func respondTenantDeny(w http.ResponseWriter, reason tenantDenyReason) {
	switch reason {
	case tenantDenyMissingTenantID:
		RespondError(w, http.StatusBadRequest, "missing_tenant_id",
			"X-Tenant-ID header is required when auth strategy is header")
	case tenantDenyMissingRowFilter:
		RespondError(w, http.StatusForbidden, "missing_row_filter",
			"tenant isolation misconfigured: no row_filter for this entity "+
				"(validate config: every entity must have a row_filter under header-auth)")
	default:
		RespondError(w, http.StatusForbidden, "tenant_isolation", "tenant isolation required")
	}
}

// asPlaceholderFunc извлекает функцию перевода плейсхолдеров из адаптера.
func asPlaceholderFunc(adapter runtime.AdapterSubset) runtime.PlaceholderFunc {
	if adapter == nil {
		return func(i int) string { return fmt.Sprintf("$%d", i) }
	}
	return adapter.TranslatePlaceholder
}
