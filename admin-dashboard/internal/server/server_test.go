package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestHealthEndpoint(t *testing.T) {
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, w.Code)
	}

	var body map[string]string
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response body: %v", err)
	}

	if body["status"] != "ok" {
		t.Errorf("expected status 'ok', got '%s'", body["status"])
	}
}

func TestHealthEndpointWithoutToken(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: ""})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, w.Code)
	}

	var body map[string]string
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response body: %v", err)
	}

	if body["status"] != "ok" {
		t.Errorf("expected status 'ok', got '%s'", body["status"])
	}
}

// clearCORS unsets CORS_ALLOW_ORIGINS and returns a restore func.
func clearCORS() func() {
	prev, ok := os.LookupEnv("CORS_ALLOW_ORIGINS")
	os.Unsetenv("CORS_ALLOW_ORIGINS")
	if ok {
		return func() { os.Setenv("CORS_ALLOW_ORIGINS", prev) }
	}
	return func() {}
}

// withCORS sets CORS_ALLOW_ORIGINS and returns a restore func.
func withCORS(val string) func() {
	prev, ok := os.LookupEnv("CORS_ALLOW_ORIGINS")
	os.Setenv("CORS_ALLOW_ORIGINS", val)
	if ok {
		return func() { os.Setenv("CORS_ALLOW_ORIGINS", prev) }
	}
	return func() { os.Unsetenv("CORS_ALLOW_ORIGINS") }
}

func TestCORSAllowOrigin_Default(t *testing.T) {
	defer clearCORS()()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(w, req)

	got := w.Header().Get("Access-Control-Allow-Origin")
	if got != "http://localhost:8080" {
		t.Errorf("Access-Control-Allow-Origin = %q, want %q", got, "http://localhost:8080")
	}
}

func TestCORSAllowOrigin_Custom(t *testing.T) {
	defer withCORS("https://example.com")()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(w, req)

	got := w.Header().Get("Access-Control-Allow-Origin")
	if got != "https://example.com" {
		t.Errorf("Access-Control-Allow-Origin = %q, want %q", got, "https://example.com")
	}
}

func TestCORSAllowOrigin_Empty(t *testing.T) {
	defer withCORS("")()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(w, req)

	got := w.Header().Get("Access-Control-Allow-Origin")
	if got != "http://localhost:8080" {
		t.Errorf("Access-Control-Allow-Origin = %q, want %q", got, "http://localhost:8080")
	}
}

func TestCORSBlockEvilOrigin(t *testing.T) {
	defer withCORS("http://localhost:8080")()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	req.Header.Set("Origin", "http://evil.com")
	router.ServeHTTP(w, req)

	got := w.Header().Get("Access-Control-Allow-Origin")
	if got == "http://evil.com" || got == "*" {
		t.Errorf("evil origin should be blocked, got %q", got)
	}
}

func TestCORSAllowHeadersNotWildcard(t *testing.T) {
	defer clearCORS()()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodOptions, "/api/tenants", nil)
	req.Header.Set("Origin", "http://localhost:8080")
	req.Header.Set("Access-Control-Request-Method", "GET")
	router.ServeHTTP(w, req)

	got := w.Header().Get("Access-Control-Allow-Headers")
	if got == "" {
		t.Fatal("Access-Control-Allow-Headers is empty")
	}
	if got == "*" {
		t.Errorf("Access-Control-Allow-Headers should not be wildcard, got %q", got)
	}
}

func TestStaticFileSecurityHeaders(t *testing.T) {
	defer clearCORS()()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, w.Code)
	}

	csp := w.Header().Get("Content-Security-Policy")
	if csp == "" {
		t.Error("Content-Security-Policy header is missing on static file response")
	}

	xcto := w.Header().Get("X-Content-Type-Options")
	if xcto != "nosniff" {
		t.Errorf("X-Content-Type-Options = %q, want %q", xcto, "nosniff")
	}
}

func TestStaticFileSecurityHeaders_JSFile(t *testing.T) {
	defer clearCORS()()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/app.js", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, w.Code)
	}

	csp := w.Header().Get("Content-Security-Policy")
	if csp == "" {
		t.Error("Content-Security-Policy header is missing on static JS file response")
	}

	xcto := w.Header().Get("X-Content-Type-Options")
	if xcto != "nosniff" {
		t.Errorf("X-Content-Type-Options = %q, want %q", xcto, "nosniff")
	}
}

func TestStaticFileSecurityHeaders_CSSFile(t *testing.T) {
	defer clearCORS()()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/styles.css", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, w.Code)
	}

	csp := w.Header().Get("Content-Security-Policy")
	if csp == "" {
		t.Error("Content-Security-Policy header is missing on static CSS file response")
	}

	xcto := w.Header().Get("X-Content-Type-Options")
	if xcto != "nosniff" {
		t.Errorf("X-Content-Type-Options = %q, want %q", xcto, "nosniff")
	}
}

// ── RBAC Tests ──

func TestAdminToken_AllMethodsAllowed(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret", ViewerToken: "viewer-secret"})
	router := s.Router()

	methods := []string{http.MethodGet, http.MethodPost, http.MethodPut, http.MethodDelete}
	for _, method := range methods {
		t.Run(method, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(method, "/api/tenants", nil)
			req.Header.Set("Authorization", "Bearer admin-secret")
			router.ServeHTTP(w, req)
			// 502 = proxy bad gateway (expected — no upstream), not 401/403
			if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
				t.Errorf("admin %s /api/tenants = %d, want anything but 401/403", method, w.Code)
			}
		})
	}
}

func TestViewerToken_GetAllowed_PostPutDeleteForbidden(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret", ViewerToken: "viewer-secret"})
	router := s.Router()

	t.Run("GET allowed", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/tenants", nil)
		req.Header.Set("Authorization", "Bearer viewer-secret")
		router.ServeHTTP(w, req)
		// 502 = proxy bad gateway (expected), not 401/403
		if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
			t.Errorf("viewer GET /api/tenants = %d, want anything but 401/403", w.Code)
		}
	})

	t.Run("POST forbidden", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/api/tenants", nil)
		req.Header.Set("Authorization", "Bearer viewer-secret")
		router.ServeHTTP(w, req)
		if w.Code != http.StatusForbidden {
			t.Errorf("viewer POST /api/tenants = %d, want 403", w.Code)
		}
	})

	t.Run("PUT forbidden", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPut, "/api/tenants/test/config", nil)
		req.Header.Set("Authorization", "Bearer viewer-secret")
		router.ServeHTTP(w, req)
		if w.Code != http.StatusForbidden {
			t.Errorf("viewer PUT /api/tenants/test/config = %d, want 403", w.Code)
		}
	})

	t.Run("DELETE forbidden", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodDelete, "/api/tenants/test", nil)
		req.Header.Set("Authorization", "Bearer viewer-secret")
		router.ServeHTTP(w, req)
		if w.Code != http.StatusForbidden {
			t.Errorf("viewer DELETE /api/tenants/test = %d, want 403", w.Code)
		}
	})
}

func TestNoToken_ReturnsUnauthorized(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret"})
	router := s.Router()

	t.Run("no auth header", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/tenants", nil)
		router.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("no auth = %d, want 401", w.Code)
		}
	})

	t.Run("wrong token", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/tenants", nil)
		req.Header.Set("Authorization", "Bearer wrong-token")
		router.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("wrong token = %d, want 401", w.Code)
		}
	})
}

func TestNoTokensConfigured_ReturnsServerError(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "", ViewerToken: ""})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/tenants", nil)
	router.ServeHTTP(w, req)
	if w.Code != http.StatusInternalServerError {
		t.Errorf("no tokens = %d, want 500", w.Code)
	}
}

func TestStaticFilesBypassAuth(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret"})
	router := s.Router()

	paths := []string{"/", "/styles.css", "/app.js", "/static/logo.svg", "/js/store.js", "/health", "/api/health"}
	for _, p := range paths {
		t.Run(p, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, p, nil)
			router.ServeHTTP(w, req)
			if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
				t.Errorf("static path %s returned %d, should bypass auth", p, w.Code)
			}
		})
	}
}

func TestViewerCanAccessHealthAndStatic(t *testing.T) {
	s := New(Options{Addr: ":0", ViewerToken: "viewer-secret"})
	router := s.Router()

	paths := []string{"/", "/styles.css", "/health", "/api/health", "/i18n.json", "/metrics"}
	for _, p := range paths {
		t.Run(p, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, p, nil)
			// No auth needed for static/health
			router.ServeHTTP(w, req)
			if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
				t.Errorf("viewer public path %s returned %d, should bypass auth", p, w.Code)
			}
		})
	}
}

// ── Dashboard Role Tests (P1.9) ──

func TestViewerDashboardReturnsViewerRole(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret", ViewerToken: "viewer-secret"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	req.Header.Set("Authorization", "Bearer viewer-secret")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("viewer dashboard = %d, want 200", w.Code)
	}

	var body map[string]any
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body["role"] != "viewer" {
		t.Errorf("role = %v, want viewer", body["role"])
	}
}

func TestAdminDashboardReturnsAdminRole(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret", ViewerToken: "viewer-secret"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	req.Header.Set("Authorization", "Bearer admin-secret")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("admin dashboard = %d, want 200", w.Code)
	}

	var body map[string]any
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body["role"] != "admin" {
		t.Errorf("role = %v, want admin", body["role"])
	}
}

// ── Audit Function Tests (P1.10) ──

func TestIsIDLike_SlugTenant(t *testing.T) {
	cases := []struct {
		input    string
		expected bool
	}{
		{"client-name", true},
		{"my_prod", true},
		{"tenant-123", true},
		{"prod_env", true},
		{"a-b-c", true},
		{"x", false},           // too short
		{"a", false},            // too short
		{"ab", false},           // too short
		{"abc", true},           // min length
		{strings.Repeat("a", 65), false}, // too long
		{"uuid-with-dashes", true},
		{"UPPERCASE", true},
		{"Mixed-Case_123", true},
	}
	for _, tc := range cases {
		if got := isIDLike(tc.input); got != tc.expected {
			t.Errorf("isIDLike(%q) = %v, want %v", tc.input, got, tc.expected)
		}
	}
}

func TestAuditResource_SlugTenant(t *testing.T) {
	cases := []struct {
		path     string
		expected string
	}{
		{"/api/tenants/client-name/config", "tenant:client-name"},
		{"/api/tenants/my_prod/tools/foo/approve", "tenant:my_prod"},
		{"/api/tenants/tenant-123/introspect", "tenant:tenant-123"},
		{"/api/agents/my-agent/abuse", "agent:my-agent"},
		{"/api/llm-providers/my-provider/toggle", "llm-provider:my-provider"},
	}
	for _, tc := range cases {
		if got := auditResource(tc.path); got != tc.expected {
			t.Errorf("auditResource(%q) = %q, want %q", tc.path, got, tc.expected)
		}
	}
}

func TestAuditResource_UnknownPath(t *testing.T) {
	cases := []struct {
		path     string
		expected string
	}{
		{"/api/new-endpoint", "path:new-endpoint"},
		{"/api/foo/bar/baz", "path:foo"},
		{"/api/unknown", "path:unknown"},
	}
	for _, tc := range cases {
		if got := auditResource(tc.path); got != tc.expected {
			t.Errorf("auditResource(%q) = %q, want %q", tc.path, got, tc.expected)
		}
	}
}

func TestAuditAuthFailureLogged(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-secret", ViewerToken: "viewer-secret"})
	router := s.Router()

	// Viewer trying to DELETE should be logged as auth.failed
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodDelete, "/api/tenants/test", nil)
	req.Header.Set("Authorization", "Bearer viewer-secret")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", w.Code)
	}

	// Check audit log contains auth.failed
	entries := s.auditStore.Recent(10)
	found := false
	for _, e := range entries {
		if e.Action == "auth.failed" && e.Resource == "tenant:test" {
			found = true
			if !strings.Contains(e.Details, "-> 403") {
				t.Errorf("details should contain '-> 403', got %q", e.Details)
			}
			break
		}
	}
	if !found {
		t.Errorf("audit log should contain auth.failed for tenant:test, got: %v", entries)
	}
}
