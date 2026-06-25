package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/repository"
)

// TeacherHandler обрабатывает запросы к /teachers/*.
type TeacherHandler struct {
	teachers *repository.TeacherRepo
}

func NewTeacherHandler(teachers *repository.TeacherRepo) *TeacherHandler {
	return &TeacherHandler{teachers: teachers}
}

// FindByName обрабатывает GET /teachers?name=....
// Без параметра name возвращает всех преподавателей.
func (h *TeacherHandler) FindByName(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")

	if name == "" {
		slog.InfoContext(r.Context(), "GET /teachers (list all)")
		all, err := h.teachers.ListAll(r.Context())
		if err != nil {
			slog.ErrorContext(r.Context(), "teachers list failed", "error", err)
			writeError(w, http.StatusInternalServerError, "database error")
			return
		}
		slog.InfoContext(r.Context(), "all teachers", "count", len(all))
		WriteJSON(w, http.StatusOK, all)
		return
	}
	slog.InfoContext(r.Context(), "GET /teachers?name=",
		"search_name", name,
	)

	if name == "" {
		writeError(w, http.StatusBadRequest, "query parameter 'name' is required")
		return
	}

	t, err := h.teachers.FindByName(r.Context(), name)
	if err != nil {
		slog.ErrorContext(r.Context(), "teacher lookup failed", "name", name, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "teacher found",
		"name", name,
		"teacher_id", t.ID,
		"disciplines_count", len(t.Disciplines),
	)
	WriteJSON(w, http.StatusOK, t)
}

// GetSchedule обрабатывает GET /teachers/{name}/schedule?day=Пн.
func (h *TeacherHandler) GetSchedule(w http.ResponseWriter, r *http.Request) {
	name := urlParam(r, "name")
	day := queryParam(r, "day")

	slog.InfoContext(r.Context(), "GET /teachers/:name/schedule",
		"teacher_name", name,
		"day", day,
	)

	schedule, err := h.teachers.GetSchedule(r.Context(), name, day)
	if err != nil {
		slog.ErrorContext(r.Context(), "teacher schedule failed", "teacher", name, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "teacher schedule",
		"teacher", name,
		"entries", len(schedule),
	)
	WriteJSON(w, http.StatusOK, schedule)
}
