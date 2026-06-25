// Package handlers содержит HTTP-обработчики для data-service.
// Обработчики НЕ знают SQL, имён таблиц или колонок.
// Они вызывают методы репозитория и возвращают JSON.
package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
)

// ── Общие хелперы ──

func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("failed to write JSON response", "error", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	WriteJSON(w, status, map[string]string{"error": msg})
}

func writeNotFound(w http.ResponseWriter) {
	writeError(w, http.StatusNotFound, "not found")
}

// urlParam извлекает URL-параметр из chi-роутера.
func urlParam(r *http.Request, key string) string {
	return chi.URLParam(r, key)
}

// queryParam извлекает query-параметр (возвращает nil если не задан).
func queryParam(r *http.Request, key string) *string {
	v := r.URL.Query().Get(key)
	if v == "" {
		return nil
	}
	return &v
}
