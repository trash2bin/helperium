// Package handlers содержит HTTP-обработчики для config-driven runtime.
package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/agent-tutor/data-service/internal/runtime"
)

// URLParamFunc извлекает параметр пути из запроса.
// В chi-режиме — chi.URLParam, можно замокать для тестов.
type URLParamFunc func(r *http.Request, name string) string

// Context — обогащённый контекст запроса для generic-обработчиков.
type Context struct {
	DB            runtime.AdapterSubset
	Adapter       runtime.AdapterSubset
	Builder       *runtime.Builder
	Resolver      *runtime.EntityResolver
	CustomQueries map[string]runtime.CustomQuery
	URLParam      URLParamFunc
}

// RespondJSON отправляет JSON-ответ с заданным статусом.
func RespondJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		// Игнорируем ошибку кодирования — статус уже отправлен.
	}
}

// RespondError отправляет стандартную ошибку.
func RespondError(w http.ResponseWriter, status int, code, message string) {
	RespondJSON(w, status, map[string]string{
		"error":   code,
		"message": message,
	})
}