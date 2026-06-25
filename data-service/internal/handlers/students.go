package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/repository"
)

// StudentHandler обрабатывает запросы к /students/*.
type StudentHandler struct {
	students *repository.StudentRepo
}

func NewStudentHandler(students *repository.StudentRepo) *StudentHandler {
	return &StudentHandler{students: students}
}

// GetByID обрабатывает GET /students/{id}.
func (h *StudentHandler) GetByID(w http.ResponseWriter, r *http.Request) {
	id := urlParam(r, "id")
	slog.InfoContext(r.Context(), "GET /students/:id",
		"student_id", id,
	)

	s, err := h.students.GetByID(r.Context(), id)
	if err != nil {
		slog.ErrorContext(r.Context(), "student lookup failed", "student_id", id, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if s == nil {
		slog.InfoContext(r.Context(), "student not found", "student_id", id)
		writeNotFound(w)
		return
	}

	slog.InfoContext(r.Context(), "student found",
		"student_id", id,
		"full_name", s.FullName,
	)
	WriteJSON(w, http.StatusOK, s)
}

// FindByName обрабатывает GET /students?name=....
// Без параметра name возвращает всех студентов.
func (h *StudentHandler) FindByName(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")

	// Если name не передан — возвращаем всех
	if name == "" {
		slog.InfoContext(r.Context(), "GET /students (list all)")
		all, err := h.students.ListAll(r.Context())
		if err != nil {
			slog.ErrorContext(r.Context(), "students list failed", "error", err)
			writeError(w, http.StatusInternalServerError, "database error")
			return
		}
		slog.InfoContext(r.Context(), "all students", "count", len(all))
		WriteJSON(w, http.StatusOK, all)
		return
	}
	slog.InfoContext(r.Context(), "GET /students?name=",
		"search_name", name,
	)

	if name == "" {
		writeError(w, http.StatusBadRequest, "query parameter 'name' is required")
		return
	}

	s, err := h.students.FindByName(r.Context(), name)
	if err != nil {
		slog.ErrorContext(r.Context(), "student search failed", "name", name, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if s == nil {
		slog.InfoContext(r.Context(), "student not found by name", "name", name)
		writeNotFound(w)
		return
	}

	slog.InfoContext(r.Context(), "student found by name",
		"name", name,
		"student_id", s.ID,
	)
	WriteJSON(w, http.StatusOK, s)
}

// GetDisciplines обрабатывает GET /students/{id}/disciplines.
func (h *StudentHandler) GetDisciplines(w http.ResponseWriter, r *http.Request) {
	id := urlParam(r, "id")
	slog.InfoContext(r.Context(), "GET /students/:id/disciplines",
		"student_id", id,
	)

	disciplines, err := h.students.GetDisciplines(r.Context(), id)
	if err != nil {
		slog.ErrorContext(r.Context(), "student disciplines failed", "student_id", id, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "student disciplines",
		"student_id", id,
		"count", len(disciplines),
	)
	WriteJSON(w, http.StatusOK, disciplines)
}
