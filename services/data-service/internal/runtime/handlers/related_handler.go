package handlers

import (
	"database/sql"
	"fmt"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// RelatedHandler — GET /q/related?entity=X&id=...&relation=...
//
// Возвращает строки сущности X, где FK-колонка relation == id.
// Один запрос по объявленному FK (config.Entity.Relations) — никаких
// произвольных JOIN'ов.
//
// В отличие от legacy navigation custom_query:
//   - применяет tenant-фильтр (row-level isolation);
//   - ограничивает LIMIT (нет у custom_query);
//   - параметры валидируются через whitelist Relation.
//
// relation — имя FK-колонки (например "brand_id"). Если не задан и есть
// ровно одна relation — берётся она. Если несколько — клиент должен указать.
type RelatedHandler struct {
	ctx    *Context
	entity config.Entity
}

// NewRelatedHandler создаёт RelatedHandler для entity.
func NewRelatedHandler(ctx *Context, entity config.Entity) *RelatedHandler {
	return &RelatedHandler{ctx: ctx, entity: entity}
}

func (h *RelatedHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	qCtx, qCancel := h.ctx.queryCtx(r)
	if qCancel != nil {
		defer qCancel()
	}

	idStr := r.URL.Query().Get("id")
	if idStr == "" {
		RespondError(w, http.StatusBadRequest, "missing_id", "id parameter is required")
		return
	}
	relation := r.URL.Query().Get("relation")

	// Разрешаем FK-колонку через whitelist Relations.
	fkCol := h.resolveRelation(relation)
	if fkCol == "" {
		RespondError(w, http.StatusBadRequest, "invalid_relation",
			"unknown relation. Call db_map to see available relations for this entity.")
		return
	}

	// Tenant-фильтр (row-level isolation). Fail-closed.
	translate := asPlaceholderFunc(h.ctx.Adapter)
	tenantWhere, tenantArgs, tenantDeny := tenantFilter(h.entity.Name, h.ctx.Auth, h.ctx.tenantID(r), 1, translate)
	if tenantDeny != tenantDenyNone {
		respondTenantDeny(w, tenantDeny)
		return
	}

	// SQL: SELECT <cols без tenant_id> FROM entity WHERE fk = ? [AND tenant] LIMIT ?
	// Плейсхолдер id = $1, tenant = следующий, limit = последний.
	qTable := h.ctx.Adapter.QuoteIdentifier(h.entity.Table)
	qFK := h.ctx.Adapter.QuoteIdentifier(fkCol)

	// Проекция БЕЗ tenant_id: как в strategy_handler (L2 — tenant_id не должен
	// течь в JSON-ответ). SELECT * вернул бы tenant_id, т.к. tableToEntity
	// включает его в Fields, а MapRow маппит по publicFor.
	cols := make([]string, 0, len(h.entity.Fields))
	for _, f := range h.entity.Fields {
		if f.Column == "tenant_id" {
			continue
		}
		cols = append(cols, h.ctx.Adapter.QuoteIdentifier(f.Column))
	}
	if len(cols) == 0 {
		cols = []string{"*"}
	}

	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if l, err := strconv.Atoi(v); err == nil && l > 0 && l <= 100 {
			limit = l
		}
	}

	// Собираем args: id, [tenant args], limit.
	args := make([]any, 0, 2+len(tenantArgs))
	args = append(args, idStr)
	phIdx := 1 // id занимает первый placeholder

	sqlStr := fmt.Sprintf("SELECT %s FROM %s WHERE %s = %s",
		strings.Join(cols, ", "), qTable, qFK,
		h.ctx.Adapter.TranslatePlaceholder(phIdx))

	if tenantWhere != "" {
		sqlStr += " AND " + tenantWhere
		args = append(args, tenantArgs...)
	}

	// LIMIT: плейсхолдер после id + всех tenant-аргументов.
	// tenantWhere содержит столько плейсхолдеров, сколько tenantArgs.
	limitPh := 1 + len(tenantArgs) + 1 // id + tenant + 1 (сам limit)
	sqlStr += " LIMIT " + h.ctx.Adapter.TranslatePlaceholder(limitPh)
	args = append(args, limit)

	slog.Debug("related query", "entity", h.entity.Name, "fk", fkCol, "id", idStr,
		"sql", sqlStr, "args", len(args))

	rows, err := h.ctx.DB.QueryContext(qCtx, sqlStr, args...)
	if err != nil {
		slog.Error("DB error in related", "err", err, "entity", h.entity.Name, "fk", fkCol)
		RespondError(w, http.StatusInternalServerError, "db_error",
			"Query execution failed. Check relation name via db_map.")
		return
	}
	defer rows.Close() //nolint:errcheck

	results, err := h.ctx.Builder.MapRows(rows, func(rows *sql.Rows) (map[string]any, error) {
		return h.ctx.Builder.MapRow(rows, runtime.ConfigToEntities([]config.Entity{h.entity})[0])
	}, limit)
	if err != nil {
		RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
		return
	}

	RespondJSON(w, http.StatusOK, results)
}

// safeFKRe — допустимые FK-имена для встраивания в SQL без квотирования
// (см. navigation.go:16 — та же защита). Имена с спецсимволами (пробелы, `"`,
// `;`, дефисы) сломают SQL или позволят инъекцию из имени БД.
var safeFKRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// resolveRelation находит FK-колонку по имени relation.
// Если relation пуст и у entity ровно одна relation — берётся она.
// Возвращает "" если relation не найден или имя небезопасно (safeFKRe).
func (h *RelatedHandler) resolveRelation(relation string) string {
	var candidates []string
	if relation == "" {
		if len(h.entity.Relations) == 1 {
			candidates = []string{h.entity.Relations[0].LocalFK}
		} else {
			return ""
		}
	} else {
		for _, rel := range h.entity.Relations {
			if rel.LocalFK == relation || rel.Field == relation {
				candidates = append(candidates, rel.LocalFK)
				break
			}
		}
	}
	if len(candidates) == 0 {
		return ""
	}
	fkCol := candidates[0]
	if !safeFKRe.MatchString(fkCol) {
		return ""
	}
	return fkCol
}
