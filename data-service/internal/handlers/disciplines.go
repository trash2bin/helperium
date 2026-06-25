package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/repository"
)

// DisciplineHandler обрабатывает запросы к /disciplines.
type DisciplineHandler struct {
	disciplines *repository.DisciplineRepo
}

func NewDisciplineHandler(disciplines *repository.DisciplineRepo) *DisciplineHandler {
	return &DisciplineHandler{disciplines: disciplines}
}

// GetAll обрабатывает GET /disciplines.
func (h *DisciplineHandler) GetAll(w http.ResponseWriter, r *http.Request) {
	slog.InfoContext(r.Context(), "GET /disciplines")

	disciplines, err := h.disciplines.GetAll(r.Context())
	if err != nil {
		slog.ErrorContext(r.Context(), "disciplines lookup failed", "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "all disciplines", "count", len(disciplines))
	WriteJSON(w, http.StatusOK, disciplines)
}
