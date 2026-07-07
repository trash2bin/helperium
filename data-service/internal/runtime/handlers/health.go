package handlers

import (
	"context"
	"net/http"
	"time"
)

// HealthHandler возвращает статус сервиса и БД.
func HealthHandler(c *Context) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		dbStatus := "ok"
		if err := c.DB.PingContext(ctx); err != nil {
			dbStatus = "error"
		}

		status := "ok"
		if dbStatus == "error" {
			status = "degraded"
		}

		RespondJSON(w, http.StatusOK, map[string]string{
			"status": status,
			"db":     dbStatus,
		})
	}
}
