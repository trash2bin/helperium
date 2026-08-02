package handlers

import (
	"fmt"
	"log/slog"
	"net/http"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// StatsHandler возвращает количество записей по счётчикам из конфига.
func StatsHandler(c *Context, cfg *config.Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if cfg.Stats == nil || len(cfg.Stats.Counters) == 0 {
			RespondJSON(w, http.StatusOK, map[string]int{})
			return
		}

		results := make(map[string]int)
		for _, counter := range cfg.Stats.Counters {
			entity, ok := c.Resolver.Resolve(counter.Entity)
			if !ok {
				continue
			}

			sql := fmt.Sprintf("SELECT COUNT(*) FROM %s", c.Adapter.QuoteIdentifier(entity.Table))
			if counter.Filter != "" {
				// counter.Filter приходит из конфига (config.json), не от HTTP-запроса.
				// WHERE-фрагмент валидируется Config.Validate() при загрузке конфига.
				sql = fmt.Sprintf("%s WHERE %s", sql, counter.Filter)
			}

			// Tenant-фильтр (row-level isolation): как в count.go/get_by_id.go/strategy.
			// Без него в multi-tenant конфиге /stats отдавал глобальные счётчики
			// по всем тенантам (cross-tenant leak).
			translate := asPlaceholderFunc(c.Adapter)
			tenantWhere, tenantArgs := tenantFilter(counter.Entity, c.Auth, c.tenantID(r), 0, translate)
			if tenantWhere != "" {
				if counter.Filter != "" {
					sql = fmt.Sprintf("%s AND %s", sql, tenantWhere)
				} else {
					sql = fmt.Sprintf("%s WHERE %s", sql, tenantWhere)
				}
			}

			qCtx, qCancel := c.queryCtx(r)
			if qCancel != nil {
				defer qCancel()
			}
			args := tenantArgs
			rows, err := c.DB.QueryContext(qCtx, sql, args...)
			if err != nil {
				// Fail-soft: один битый counter (например, RowFilter на несуществующую
				// колонку) НЕ должен ронять весь /stats. Логируем и пропускаем —
				// остальные счётчики считаем.
				slog.Error("stats: counter query failed, skipping",
					"counter", counter.Name, "entity", counter.Entity,
					"tenant", c.tenantID(r), "err", err, "sql", sql)
				continue
			}

			var count int
			if rows.Next() {
				if err := rows.Scan(&count); err != nil {
					_ = rows.Close()
					slog.Error("stats: counter scan failed, skipping",
						"counter", counter.Name, "entity", counter.Entity, "err", err)
					continue
				}
			}
			_ = rows.Close()
			results[counter.Name] = count
		}

		RespondJSON(w, http.StatusOK, results)
	}
}
