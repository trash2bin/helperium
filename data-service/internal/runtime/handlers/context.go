// Package handlers содержит HTTP-обработчики для config-driven runtime.
package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
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

	// Auth — multi-tenancy row-level isolation (фаза 3.7).
	Auth *config.AuthConfig

	// TenantIDFunc извлекает tenant_id из HTTP request context.
	// Устанавливается TenantIDMiddleware в endpoint_builder.
	TenantIDFunc func(r *http.Request) string

	// QueryTimeout — per-query timeout. 0 = без таймаута.
	// Применяется ко всем QueryContext/QueryRowContext вызовам.
	QueryTimeout time.Duration
}

// queryCtx возвращает контекст с таймаутом, если QueryTimeout > 0.
func (c *Context) queryCtx(r *http.Request) (context.Context, context.CancelFunc) {
	if c.QueryTimeout > 0 {
		return context.WithTimeout(r.Context(), c.QueryTimeout)
	}
	return r.Context(), nil
}

// tenantID извлекает tenant_id из request с помощью TenantIDFunc.
func (c *Context) tenantID(r *http.Request) string {
	if c.TenantIDFunc == nil {
		return ""
	}
	return c.TenantIDFunc(r)
}

// RespondJSON отправляет JSON-ответ с заданным статусом.
func RespondJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// RespondError отправляет стандартную ошибку.
func RespondError(w http.ResponseWriter, status int, code, message string) {
	RespondJSON(w, status, map[string]string{
		"error":   code,
		"message": message,
	})
}
