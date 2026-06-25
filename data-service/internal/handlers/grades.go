package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/repository"
)

// GradeHandler обрабатывает запросы к /students/{id}/grades.
type GradeHandler struct {
	grades *repository.GradeRepo
}

func NewGradeHandler(grades *repository.GradeRepo) *GradeHandler {
	return &GradeHandler{grades: grades}
}

// GetByStudent обрабатывает GET /students/{id}/grades?discipline_id=....
func (h *GradeHandler) GetByStudent(w http.ResponseWriter, r *http.Request) {
	studentID := urlParam(r, "id")
	disciplineID := queryParam(r, "discipline_id")

	slog.InfoContext(r.Context(), "GET /students/:id/grades",
		"student_id", studentID,
		"discipline_id", disciplineID,
	)

	grades, err := h.grades.GetByStudent(r.Context(), studentID, disciplineID)
	if err != nil {
		slog.ErrorContext(r.Context(), "grades lookup failed", "student_id", studentID, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "student grades",
		"student_id", studentID,
		"count", len(grades),
	)
	WriteJSON(w, http.StatusOK, grades)
}

// ListAll обрабатывает GET /grades — все оценки.
func (h *GradeHandler) ListAll(w http.ResponseWriter, r *http.Request) {
	slog.InfoContext(r.Context(), "GET /grades (list all)")

	grades, err := h.grades.ListAll(r.Context())
	if err != nil {
		slog.ErrorContext(r.Context(), "grades list failed", "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "all grades", "count", len(grades))
	WriteJSON(w, http.StatusOK, grades)
}
