package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/repository"
)

// StatsHandler обрабатывает GET /stats.
type StatsHandler struct {
	stats *repository.StatsRepo
}

func NewStatsHandler(stats *repository.StatsRepo) *StatsHandler {
	return &StatsHandler{stats: stats}
}

// GetStats возвращает количество записей во всех таблицах.
func (h *StatsHandler) GetStats(w http.ResponseWriter, r *http.Request) {
	slog.InfoContext(r.Context(), "GET /stats")

	stats, err := h.stats.GetAll(r.Context())
	if err != nil {
		slog.ErrorContext(r.Context(), "stats lookup failed", "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "stats retrieved",
		"students", stats.Students,
		"teachers", stats.Teachers,
		"disciplines", stats.Disciplines,
	)
	WriteJSON(w, http.StatusOK, stats)
}
