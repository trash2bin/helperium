package handlers

import (
	"fmt"
	"net/http"

	"github.com/agent-tutor/agent-tutor-go/config"
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
				sql += " WHERE " + counter.Filter
			}

			rows, err := c.DB.QueryContext(r.Context(), sql)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "db_error", "failed to count "+counter.Entity)
				return
			}
			var count int
			if rows.Next() {
				if err := rows.Scan(&count); err != nil {
					rows.Close()
					RespondError(w, http.StatusInternalServerError, "scan_error", "failed to scan count for "+counter.Entity)
					return
				}
			}
			rows.Close()
			results[counter.Name] = count
		}

		RespondJSON(w, http.StatusOK, results)
	}
}
