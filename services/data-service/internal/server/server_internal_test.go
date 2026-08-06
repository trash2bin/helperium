package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestInitLogger(t *testing.T) {
	t.Setenv("DS_LOG_LEVEL", "debug")
	InitLogger()
	// If no panic, it works. Read-only check.
}

func TestInitLogger_Default(t *testing.T) {
	os.Unsetenv("DS_LOG_LEVEL")
	InitLogger()
}

func TestResolveRequestTimeout_Env(t *testing.T) {
	t.Setenv("DS_REQUEST_TIMEOUT", "99")
	cfg := &config.Config{}
	got := ResolveRequestTimeout(cfg)
	if got != 99 {
		t.Errorf("ResolveRequestTimeout = %d, want 99", got)
	}
}

func TestResolveRequestTimeout_FromConfig(t *testing.T) {
	os.Unsetenv("DS_REQUEST_TIMEOUT")
	timeout := 45
	cfg := &config.Config{Server: &config.ServerConfig{RequestTimeoutSeconds: &timeout}}
	got := ResolveRequestTimeout(cfg)
	if got != 45 {
		t.Errorf("ResolveRequestTimeout = %d, want 45", got)
	}
}

func TestResolveRequestTimeout_Default(t *testing.T) {
	os.Unsetenv("DS_REQUEST_TIMEOUT")
	cfg := &config.Config{}
	got := ResolveRequestTimeout(cfg)
	if got != 30 {
		t.Errorf("ResolveRequestTimeout = %d, want 30 (default)", got)
	}
}

func TestResolveBodyLimit_Env(t *testing.T) {
	t.Setenv("DS_BODY_LIMIT_MB", "5")
	cfg := &config.Config{}
	got := ResolveBodyLimit(cfg)
	if got != 5<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 5<<20)
	}
}

func TestResolveBodyLimit_FromConfig(t *testing.T) {
	os.Unsetenv("DS_BODY_LIMIT_MB")
	mb := 20
	cfg := &config.Config{Server: &config.ServerConfig{BodyLimitMB: &mb}}
	got := ResolveBodyLimit(cfg)
	if got != 20<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 20<<20)
	}
}

func TestResolveBodyLimit_Default(t *testing.T) {
	os.Unsetenv("DS_BODY_LIMIT_MB")
	cfg := &config.Config{}
	got := ResolveBodyLimit(cfg)
	if got != 10<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 10<<20)
	}
}

func TestResolveMaxConcurrent_Env(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "250")
	cfg := &config.Config{}
	got := ResolveMaxConcurrent(cfg)
	if got != 250 {
		t.Errorf("ResolveMaxConcurrent = %d, want 250", got)
	}
}

func TestResolveMaxConcurrent_FromConfig(t *testing.T) {
	os.Unsetenv("DS_MAX_CONCURRENT")
	mc := 50
	cfg := &config.Config{Server: &config.ServerConfig{MaxConcurrent: &mc}}
	got := ResolveMaxConcurrent(cfg)
	if got != 50 {
		t.Errorf("ResolveMaxConcurrent = %d, want 50", got)
	}
}

func TestResolveMaxConcurrent_Default(t *testing.T) {
	os.Unsetenv("DS_MAX_CONCURRENT")
	cfg := &config.Config{}
	got := ResolveMaxConcurrent(cfg)
	if got != 100 {
		t.Errorf("ResolveMaxConcurrent = %d, want 100 (default)", got)
	}
}

func TestResolveIntEnv_Invalid(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "notanumber")
	got := resolveIntEnv("DS_MAX_CONCURRENT", 0, 50)
	if got != 50 {
		t.Errorf("resolveIntEnv with invalid value = %d, want 50 (default)", got)
	}
}

func TestResolveIntEnv_Negative(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "-5")
	got := resolveIntEnv("DS_MAX_CONCURRENT", 10, 50)
	if got != 10 {
		t.Errorf("resolveIntEnv with negative = %d, want 10 (fallback)", got)
	}
}

// TestRecoveryMiddleware_ContentType — проверяет что RecoveryMiddleware возвращает
// правильный Content-Type и тело парсится как JSON.
func TestRecoveryMiddleware_ContentType(t *testing.T) {
	handler := RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("test panic")
	}))

	req := httptest.NewRequest(http.MethodGet, "/panic", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}

	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}

	// Тело должно парситься как JSON
	var body map[string]string
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("response body is not valid JSON: %v", err)
	}
	if body["error"] != "internal server error" {
		t.Errorf("expected error=internal server error, got %q", body["error"])
	}
}

// TestBodyLimitMiddleware_ContentType — проверяет что BodyLimitMiddleware возвращает
// правильный Content-Type при превышении лимита.
func TestBodyLimitMiddleware_ContentType(t *testing.T) {
	handler := BodyLimitMiddleware(10)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodPost, "/test", strings.NewReader("this body is too long for the limit"))
	req.ContentLength = 100
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("expected 413, got %d", w.Code)
	}

	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}

	var body map[string]string
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("response body is not valid JSON: %v", err)
	}
	if body["error"] != "body_too_large" {
		t.Errorf("expected error=body_too_large, got %q", body["error"])
	}
}

func TestConfigValue_Nil(t *testing.T) {
	got := configValue(nil, func(c *config.Config) *int { return nil })
	if got != 0 {
		t.Errorf("configValue(nil) = %d, want 0", got)
	}
}

func TestConfigValue_NilServer(t *testing.T) {
	cfg := &config.Config{Server: nil}
	got := configValue(cfg, func(c *config.Config) *int {
		if c.Server != nil {
			return c.Server.MaxConcurrent
		}
		return nil
	})
	if got != 0 {
		t.Errorf("configValue(nil server) = %d, want 0", got)
	}
}
