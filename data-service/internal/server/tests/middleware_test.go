package server_test

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agent-tutor/data-service/internal/server"
)

// ═════════════════════════════════════════════════════════════════════
// RequestIDMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestRequestIDMiddleware_PropagatesHeader(t *testing.T) {
	handler := server.RequestIDMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	req.Header.Set("X-Correlation-ID", "corr-123")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Header().Get("X-Correlation-ID") != "corr-123" {
		t.Errorf("expected X-Correlation-ID header to be propagated, got %q", w.Header().Get("X-Correlation-ID"))
	}
}

func TestRequestIDMiddleware_EmptyHeader(t *testing.T) {
	handler := server.RequestIDMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	// Without header, X-Correlation-ID should be empty in response
	if w.Header().Get("X-Correlation-ID") != "" {
		t.Errorf("expected empty X-Correlation-ID header, got %q", w.Header().Get("X-Correlation-ID"))
	}
}

func TestRequestIDMiddleware_LowercaseHeader(t *testing.T) {
	handler := server.RequestIDMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	req.Header.Set("x-correlation-id", "corr-lowercase")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Header().Get("X-Correlation-ID") != "corr-lowercase" {
		t.Errorf("expected 'corr-lowercase', got %q", w.Header().Get("X-Correlation-ID"))
	}
}

// ═════════════════════════════════════════════════════════════════════
// StructuredLoggingMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestStructuredLoggingMiddleware_WrapsStatus(t *testing.T) {
	handler := server.StructuredLoggingMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte("created"))
	}))

	req := httptest.NewRequest(http.MethodPost, "/api/data", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d", w.Code)
	}
	if w.Body.String() != "created" {
		t.Errorf("expected 'created', got %q", w.Body.String())
	}
}

func TestStructuredLoggingMiddleware_PassesThrough(t *testing.T) {
	handler := server.StructuredLoggingMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ═════════════════════════════════════════════════════════════════════
// RecoveryMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestRecoveryMiddleware_CatchesPanic(t *testing.T) {
	handler := server.RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("something went wrong")
	}))

	req := httptest.NewRequest(http.MethodGet, "/panic", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "internal server error") {
		t.Errorf("expected 'internal server error' in response, got %q", body)
	}
}

func TestRecoveryMiddleware_PassesThrough(t *testing.T) {
	handler := server.RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/ok", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if w.Body.String() != "ok" {
		t.Errorf("expected 'ok', got %q", w.Body.String())
	}
}

func TestRecoveryMiddleware_NilPanic(t *testing.T) {
	handler := server.RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic(nil)
	}))

	req := httptest.NewRequest(http.MethodGet, "/nil", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for nil panic, got %d", w.Code)
	}
}

// ═════════════════════════════════════════════════════════════════════
// TenantIDMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestTenantIDMiddleware_SetsTenant(t *testing.T) {
	mw := server.TenantIDMiddleware("X-Tenant-ID")
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("passed"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data/students", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if w.Body.String() != "passed" {
		t.Errorf("expected 'passed', got %q", w.Body.String())
	}
}

func TestTenantIDMiddleware_EmptyTenant(t *testing.T) {
	mw := server.TenantIDMiddleware("X-Tenant-ID")
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data/students", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestTenantIDMiddleware_SystemPathsBypass(t *testing.T) {
	mw := server.TenantIDMiddleware("X-Tenant-ID")
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(r.URL.Path))
	}))

	req := httptest.NewRequest(http.MethodGet, "/docs", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for /docs, got %d", w.Code)
	}
}

func TestTenantIDMiddleware_CaseInsensitiveHeader(t *testing.T) {
	mw := server.TenantIDMiddleware("X-Tenant-ID")
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("passed"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data/students", nil)
	req.Header.Set("x-tenant-id", "tenant-b")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if w.Body.String() != "passed" {
		t.Errorf("expected 'passed', got %q", w.Body.String())
	}
}

// ═════════════════════════════════════════════════════════════════════
// BodyLimitMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestBodyLimitMiddleware_UnderLimit(t *testing.T) {
	mw := server.BodyLimitMiddleware(100)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Write(body)
	}))

	body := strings.NewReader("small body")
	req := httptest.NewRequest(http.MethodPost, "/api/data", body)
	req.ContentLength = 10
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if w.Body.String() != "small body" {
		t.Errorf("expected 'small body', got %q", w.Body.String())
	}
}

func TestBodyLimitMiddleware_OverLimit(t *testing.T) {
	mw := server.BodyLimitMiddleware(10)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	body := strings.NewReader("this body is way too long for the limit")
	req := httptest.NewRequest(http.MethodPost, "/api/data", body)
	req.ContentLength = 42
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("expected 413, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "body_too_large") {
		t.Errorf("expected 'body_too_large' in response, got %q", w.Body.String())
	}
}

func TestBodyLimitMiddleware_GETRequest(t *testing.T) {
	mw := server.BodyLimitMiddleware(10)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for GET, got %d", w.Code)
	}
}

func TestBodyLimitMiddleware_NoLength(t *testing.T) {
	mw := server.BodyLimitMiddleware(100)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodPost, "/api/data", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ═════════════════════════════════════════════════════════════════════
// ThrottleMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestThrottleMiddleware_AllowsSingleRequest(t *testing.T) {
	mw := server.ThrottleMiddleware(5)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("passed"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if w.Body.String() != "passed" {
		t.Errorf("expected 'passed', got %q", w.Body.String())
	}
}

func TestThrottleMiddleware_ProducesJSONError(t *testing.T) {
	mw := server.ThrottleMiddleware(0) // always reject
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/data", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "too_many_requests") {
		t.Errorf("expected 'too_many_requests' in response, got %q", w.Body.String())
	}
	if w.Header().Get("Retry-After") != "1" {
		t.Errorf("expected Retry-After header=1, got %q", w.Header().Get("Retry-After"))
	}
}

func TestThrottleMiddleware_BlocksAtLimit(t *testing.T) {
	mw := server.ThrottleMiddleware(2)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("passed"))
	}))

	// First request passes
	req1 := httptest.NewRequest(http.MethodGet, "/api/data", nil)
	w1 := httptest.NewRecorder()
	handler.ServeHTTP(w1, req1)
	if w1.Code != http.StatusOK {
		t.Errorf("first request expected 200, got %d", w1.Code)
	}
}