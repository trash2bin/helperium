package server

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/handlers"
	"github.com/agent-tutor/data-service/internal/repository"
	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
)

// NewRouter создаёт chi-роутер со всеми middleware и маршрутами.
func NewRouter(database db.DB) http.Handler {
	r := chi.NewRouter()

	// Глобальные middleware
	r.Use(RecoveryMiddleware)
	r.Use(RequestIDMiddleware)
	r.Use(StructuredLoggingMiddleware)
	r.Use(chimw.RealIP)

	// Инициализация репозиториев
	studentRepo := repository.NewStudentRepo(database)
	teacherRepo := repository.NewTeacherRepo(database)
	gradeRepo := repository.NewGradeRepo(database)
	disciplineRepo := repository.NewDisciplineRepo(database)

	// Инициализация обработчиков
	studentHandler := handlers.NewStudentHandler(studentRepo)
	teacherHandler := handlers.NewTeacherHandler(teacherRepo)
	gradeHandler := handlers.NewGradeHandler(gradeRepo)
	disciplineHandler := handlers.NewDisciplineHandler(disciplineRepo)
	scheduleHandler := handlers.NewScheduleHandler(studentRepo, database)
	statsHandler := handlers.NewStatsHandler(repository.NewStatsRepo(database))

	// ── Системные ──
	r.Get("/health", healthHandler(database))
	r.Get("/stats", statsHandler.GetStats)
	r.Get("/docs", swaggerHandler)
	r.Get("/openapi.json", openapiHandler)

	// ── Студенты ──
	r.Get("/students/{id}", studentHandler.GetByID)
	r.Get("/students", studentHandler.FindByName)
	r.Get("/students/{id}/disciplines", studentHandler.GetDisciplines)
	r.Get("/students/{id}/grades", gradeHandler.GetByStudent)
	r.Get("/grades", gradeHandler.ListAll)

	// ── Преподаватели ──
	r.Get("/teachers", teacherHandler.FindByName)
	r.Get("/teachers/{name}/schedule", teacherHandler.GetSchedule)

	// ── Расписание ──
	r.Get("/groups/{id}/schedule", scheduleHandler.GetByGroup)
	r.Get("/schedule", scheduleHandler.ListAll)

	// ── Дисциплины ──
	r.Get("/disciplines", disciplineHandler.GetAll)

	slog.Info("routes registered",
		"api_count", 8,
		"system_count", 3,
	)

	return r
}

// healthHandler возвращает статус сервиса и БД.
func healthHandler(database db.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		dbStatus := "ok"
		if err := database.PingContext(ctx); err != nil {
			dbStatus = "error"
			slog.ErrorContext(ctx, "health check: db ping failed", "error", err)
		}

		status := "ok"
		if dbStatus != "ok" {
			status = "degraded"
		}

		handlers.WriteJSON(w, http.StatusOK, map[string]string{
			"status": status,
			"db":     dbStatus,
		})
	}
}


