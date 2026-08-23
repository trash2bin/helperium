// Package handlers содержит HTTP-обработчики для config-driven runtime.
package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/helperium-go/config"
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

// queryResponseTimeoutMargin reserves enough time for a query handler to map
// a dependency timeout to a deterministic JSON response before the outer HTTP
// timeout middleware writes its fallback response.
const queryResponseTimeoutMargin = time.Second

// queryCtx returns a context with a per-query timeout when QueryTimeout > 0.
// If the request already has a nearer deadline, it reserves a small response
// window to avoid racing the outer HTTP timeout handler.
func (c *Context) queryCtx(r *http.Request) (context.Context, context.CancelFunc) {
	if c.QueryTimeout <= 0 {
		return r.Context(), nil
	}

	timeout := c.QueryTimeout
	if deadline, ok := r.Context().Deadline(); ok {
		remaining := time.Until(deadline)
		if remaining > queryResponseTimeoutMargin {
			maxQueryTimeout := remaining - queryResponseTimeoutMargin
			if timeout > maxQueryTimeout {
				timeout = maxQueryTimeout
			}
		}
	}
	return context.WithTimeout(r.Context(), timeout)
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

// ErrorResponse is the stable JSON envelope for runtime handler failures.
// Error is retained for existing REST clients; ErrorCode is the explicit
// machine-readable contract consumed by the MCP gateway.
type ErrorResponse struct {
	Error     string `json:"error"`
	ErrorCode string `json:"error_code"`
	Message   string `json:"message"`
}

// RespondError sends a backward-compatible error response with a stable code.
func RespondError(w http.ResponseWriter, status int, code, message string) {
	RespondJSON(w, status, ErrorResponse{
		Error:     code,
		ErrorCode: code,
		Message:   message,
	})
}
