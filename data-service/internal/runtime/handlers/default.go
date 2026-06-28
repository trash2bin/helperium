package handlers

import (
	"encoding/json"
	"net/http"
)

// NotFoundHandler отправляет стандартную 404 ошибку.
func NotFoundHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotFound)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":   "not_found",
		"message": "Resource not found",
	})
}

// MethodNotAllowedHandler отправляет стандартную 405 ошибку.
func MethodNotAllowedHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusMethodNotAllowed)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":   "method_not_allowed",
		"message": "HTTP method not allowed for this endpoint",
	})
}