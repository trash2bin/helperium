package handlers

import (
	"log/slog"
	"net/http"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/repository"
)

// ScheduleHandler обрабатывает запросы к /groups/{id}/schedule и /schedule.
type ScheduleHandler struct {
	students *repository.StudentRepo
	db       db.DB
}

func NewScheduleHandler(students *repository.StudentRepo, database db.DB) *ScheduleHandler {
	return &ScheduleHandler{students: students, db: database}
}

// GetByGroup обрабатывает GET /groups/{id}/schedule?day=Пн.
func (h *ScheduleHandler) GetByGroup(w http.ResponseWriter, r *http.Request) {
	groupID := urlParam(r, "id")
	day := queryParam(r, "day")

	slog.InfoContext(r.Context(), "GET /groups/:id/schedule",
		"group_id", groupID,
		"day", day,
	)

	schedule, err := h.students.GetSchedule(r.Context(), groupID, day)
	if err != nil {
		slog.ErrorContext(r.Context(), "schedule lookup failed", "group_id", groupID, "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "group schedule",
		"group_id", groupID,
		"entries", len(schedule),
	)
	WriteJSON(w, http.StatusOK, schedule)
}

// ListAll обрабатывает GET /schedule — всё расписание.
func (h *ScheduleHandler) ListAll(w http.ResponseWriter, r *http.Request) {
	slog.InfoContext(r.Context(), "GET /schedule (list all)")

	schedule, err := repository.ListAllSchedule(r.Context(), h.db)
	if err != nil {
		slog.ErrorContext(r.Context(), "schedule list failed", "error", err)
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}

	slog.InfoContext(r.Context(), "all schedule", "entries", len(schedule))
	WriteJSON(w, http.StatusOK, schedule)
}
