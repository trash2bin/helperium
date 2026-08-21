package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/mcp-gateway/internal/httpclient"
)

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════

// TestMain — инициализация для тестов, которым нужен config.Load.
// CONFIG_SCHEMA больше не нужен — валидация через Go-типы.
func TestMain(m *testing.M) {
	os.Exit(m.Run())
}

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════

func writeTestConfig(t *testing.T, data string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(data), 0644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return path
}

func defaultTestConfig() string {
	return `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"entities": [
			{
				"name": "student",
				"table": "students",
				"id_column": "id",
				"description": "Student",
				"fields": [
					{ "name": "id", "column": "id", "type": "string", "nullable": false, "primary_key": true },
					{ "name": "full_name", "column": "name", "type": "string", "nullable": false }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/health", "op": "builtin_health", "description": "Health check" },
			{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student", "description": "Get by ID" },
			{ "method": "GET", "path": "/students", "op": "strategy", "strategy": "grep", "entity": "student", "description": "Search student" }
		],
		"mcp_tools": [
			{
				"name": "get_student",
				"endpoint": "/students/{id}",
				"description": "Get student",
				"params": [{ "name": "id", "type": "string", "required": true }]
			}
		]
	}`
}

func newTestRouterFromConfig(t *testing.T, cfgJSON string) *chi.Mux {
	t.Helper()
	path := writeTestConfig(t, cfgJSON)
	_, err := config.Load(path)
	if err != nil {
		t.Fatalf("config.Load: %v", err)
	}
	return buildRouter()
}

// configureManifestClient provides the same initialized downstream dependency
// that production main() installs before building the router. Auth tests that
// expect an accepted request to reach /config must prove the handler succeeds,
// not merely that auth did not return 401 before a recovered panic.
func configureManifestClient(t *testing.T) {
	t.Helper()
	previousClient := globalClient
	t.Cleanup(func() { globalClient = previousClient })

	manifestServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp/manifest" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, defaultTestConfig())
	}))
	t.Cleanup(manifestServer.Close)

	t.Setenv("DATA_SERVICE_URL", manifestServer.URL)
	globalClient = httpclient.New()
}

// unsetEnv removes a variable for the duration of one test and restores its
// previous literal presence or absence afterwards. Use this when the test
// contract distinguishes an unset variable from an explicitly empty value.
func unsetEnv(t *testing.T, key string) {
	t.Helper()
	previous, wasSet := os.LookupEnv(key)
	if err := os.Unsetenv(key); err != nil {
		t.Fatalf("unset %s: %v", key, err)
	}
	t.Cleanup(func() {
		if wasSet {
			_ = os.Setenv(key, previous)
			return
		}
		_ = os.Unsetenv(key)
	})
}

// ════════════════════════════════════════════════════════════════
// Health endpoint tests
// ════════════════════════════════════════════════════════════════

func TestHealthEndpoint(t *testing.T) {
	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("GET", "/health", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /health = %d, want %d\nbody: %s", rec.Code, http.StatusOK, rec.Body.String())
	}

	var body map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if body["status"] != "ok" {
		t.Errorf(`body["status"] = %q, want "ok"`, body["status"])
	}
}

func TestHealthEndpoint_ContentType(t *testing.T) {
	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("GET", "/health", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	ct := rec.Header().Get("Content-Type")
	if !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
}

func TestDebugConfigAlias(t *testing.T) {
	prevClient := globalClient
	defer func() { globalClient = prevClient }()

	manifestServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp/manifest" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, defaultTestConfig())
	}))
	defer manifestServer.Close()

	t.Setenv("DATA_SERVICE_URL", manifestServer.URL)
	globalClient = httpclient.New()

	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /config = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	var cfg map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &cfg); err != nil {
		t.Fatalf("unmarshal response: %v (body: %s)", err, rec.Body.String())
	}
	if cfg["version"] == nil {
		t.Fatalf("config response missing version: %v", cfg)
	}
}

// ════════════════════════════════════════════════════════════════
// Auth middleware tests
// ═══════════════════════════════════════════════���════════════════

func TestAuthMiddleware_HealthEndpointExcluded(t *testing.T) {
	t.Setenv("MCP_API_KEY", "test-secret-123")
	defer os.Unsetenv("MCP_API_KEY")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/health", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("GET /health without token = %d, want 200", rec.Code)
	}
}

func TestAuthMiddleware_CorrectToken_ReachesWorkingConfigHandler(t *testing.T) {
	t.Setenv("MCP_API_KEY", "test-secret-123")
	configureManifestClient(t)

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("Authorization", "Bearer test-secret-123")
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	t.Logf("correct token GET /config status=%d body=%s", rec.Code, rec.Body.String())
	if rec.Code != http.StatusOK {
		t.Fatalf("correct token GET /config = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	var cfg map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &cfg); err != nil {
		t.Fatalf("unmarshal config response: %v (body: %s)", err, rec.Body.String())
	}
	if cfg["version"] == nil {
		t.Fatalf("config response missing version: %v", cfg)
	}
}

func TestAuthMiddleware_WrongToken_Returns401(t *testing.T) {
	t.Setenv("MCP_API_KEY", "test-secret-123")
	defer os.Unsetenv("MCP_API_KEY")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Errorf("wrong token = %d, want 401", rec.Code)
	}
}

func TestAuthMiddleware_NoKeyEnv_SkipsAuth(t *testing.T) {
	unsetEnv(t, "MCP_API_KEY")
	configureManifestClient(t)

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	t.Logf("absent MCP_API_KEY GET /config status=%d body=%s", rec.Code, rec.Body.String())
	// Development opt-out still has to reach a working downstream handler.
	if rec.Code != http.StatusOK {
		t.Fatalf("absent MCP_API_KEY GET /config = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestAuthMiddleware_EmptyKeyEnv_SkipsAuth(t *testing.T) {
	t.Setenv("MCP_API_KEY", "")
	configureManifestClient(t)

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	t.Logf("empty MCP_API_KEY GET /config status=%d body=%s", rec.Code, rec.Body.String())
	if rec.Code != http.StatusOK {
		t.Fatalf("empty MCP_API_KEY GET /config = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestAuthMiddleware_InvalidAuthScheme_Returns401(t *testing.T) {
	t.Setenv("MCP_API_KEY", "test-secret-123")
	defer os.Unsetenv("MCP_API_KEY")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("Authorization", "Basic dGVzdDp0ZXN0")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Errorf("Basic auth = %d, want 401", rec.Code)
	}
}

func TestValidateStartupConfigurationRequiresKeyWhenEnabled(t *testing.T) {
	t.Setenv("MCP_REQUIRE_AUTH", "true")
	t.Setenv("MCP_API_KEY", "")
	if err := validateStartupConfiguration(); err == nil {
		t.Fatal("MCP_REQUIRE_AUTH=true without MCP_API_KEY should fail validation")
	}

	t.Setenv("MCP_API_KEY", "test-secret-123")
	if err := validateStartupConfiguration(); err != nil {
		t.Fatalf("MCP_REQUIRE_AUTH=true with key: %v", err)
	}
}

func TestOriginMiddlewareRejectsUnexpectedBrowserOrigin(t *testing.T) {
	t.Setenv("MCP_ALLOWED_ORIGINS", "https://console.example.test")
	r := newTestRouterFromConfig(t, defaultTestConfig())

	for _, tc := range []struct {
		name   string
		origin string
		want   int
	}{
		{name: "service client without Origin", want: http.StatusOK},
		{name: "allowed browser origin", origin: "https://console.example.test", want: http.StatusOK},
		{name: "unexpected browser origin", origin: "https://attacker.invalid", want: http.StatusForbidden},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/health", nil)
			if tc.origin != "" {
				req.Header.Set("Origin", tc.origin)
			}
			rec := httptest.NewRecorder()
			r.ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Errorf("GET /health with Origin %q = %d, want %d", tc.origin, rec.Code, tc.want)
			}
		})
	}
}

func TestValidateTenantScopeRejectsDuplicatesAndExcess(t *testing.T) {
	previous := MaxTenantsPerScope
	MaxTenantsPerScope = 2
	t.Cleanup(func() { MaxTenantsPerScope = previous })

	if err := validateTenantScope([]string{"tenant-a", "tenant-a"}); !errors.Is(err, errDuplicateTenantInScope) {
		t.Fatalf("duplicate tenant scope error = %v, want %v", err, errDuplicateTenantInScope)
	}
	if err := validateTenantScope([]string{"tenant-a", "tenant-b", "tenant-c"}); !errors.Is(err, errTooManyTenantsPerScope) {
		t.Fatalf("oversized tenant scope error = %v, want %v", err, errTooManyTenantsPerScope)
	}
	if err := validateTenantScope([]string{"tenant-a", "tenant-b"}); err != nil {
		t.Fatalf("valid composite scope rejected: %v", err)
	}
}

func TestValidateTenantScopeRejectsMalformedTenantIDs(t *testing.T) {
	for _, tenantID := range []string{
		"../../etc",
		"tenant/other",
		"tenant.with.dot",
		"-tenant",
		strings.Repeat("a", 129),
	} {
		t.Run(tenantID, func(t *testing.T) {
			if err := validateTenantScope([]string{tenantID}); !errors.Is(err, errInvalidTenantIDInScope) {
				t.Fatalf("validateTenantScope(%q) error = %v, want %v", tenantID, err, errInvalidTenantIDInScope)
			}
		})
	}

	if err := validateTenantScope([]string{"tenant_123", "tenant-a"}); err != nil {
		t.Fatalf("valid tenant IDs rejected: %v", err)
	}
}

func TestNotFoundRoutes(t *testing.T) {
	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("GET", "/nonexistent", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("GET /nonexistent = %d, want %d", rec.Code, http.StatusNotFound)
	}
}

func TestLegacyMCPRoutesAreNotRegistered(t *testing.T) {
	r := newTestRouterFromConfig(t, defaultTestConfig())
	for _, path := range []string{"/", "/sse", "/mcp/message", "/mcp/v2"} {
		t.Run(path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			rec := httptest.NewRecorder()
			r.ServeHTTP(rec, req)
			if rec.Code != http.StatusNotFound {
				t.Errorf("GET %s = %d, want 404", path, rec.Code)
			}
		})
	}
}

func TestResolveTenantIDsUsesHeaderOnly(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/mcp?tenant=query-tenant", nil)
	if got := resolveTenantIDs(req); len(got) != 0 {
		t.Fatalf("query parameter selected tenant scope: got %v, want no tenant IDs", got)
	}

	req.Header.Set("X-Tenant-ID", "header-a, header-b")
	if got, want := strings.Join(resolveTenantIDs(req), ","), "header-a,header-b"; got != want {
		t.Fatalf("resolveTenantIDs() = %q, want %q", got, want)
	}
}

func TestStreamableTenantRegistryRejectsNewScopeAtCapacity(t *testing.T) {
	registry := &streamableTenantRegistry{
		handlers: map[string]http.Handler{"tenant-a": http.NotFoundHandler()},
		max:      1,
	}
	req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
	req.Header.Set("X-Tenant-ID", "tenant-b")
	rec := httptest.NewRecorder()

	registry.serveHTTP(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("new tenant scope at capacity = %d, want 503; body=%q", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "too many active Streamable HTTP tenant scopes") {
		t.Errorf("503 body = %q, want capacity error message", rec.Body.String())
	}
}

func TestConcurrentRequests(t *testing.T) {
	r := newTestRouterFromConfig(t, defaultTestConfig())
	done := make(chan bool, 20)
	for i := 0; i < 20; i++ {
		go func() {
			req := httptest.NewRequest("GET", "/health", nil)
			rec := httptest.NewRecorder()
			r.ServeHTTP(rec, req)
			if rec.Code != http.StatusOK {
				t.Errorf("concurrent GET /health = %d, want %d", rec.Code, http.StatusOK)
			}
			done <- true
		}()
	}
	for i := 0; i < 20; i++ {
		<-done
	}
}

// ════════════════════════════════════════════════════════════════
// Rate limiting tests (TDD: failing tests first)
// ════════════════════════════════════════════════════════════════

func TestRateLimit_AllowsUpToBurst(t *testing.T) {
	// Use a high RPS but small burst so tests are fast
	rl := newRateLimiter(1000, 10) // 1000 rps, burst 10

	// First 10 requests should succeed (burst capacity)
	for i := 0; i < 10; i++ {
		if !rl.Allow("192.168.1.1") {
			t.Fatalf("request %d should be allowed (within burst)", i+1)
		}
	}
}

func TestRateLimit_BurstBlocksExcess(t *testing.T) {
	rps := 1000
	burst := 5
	rl := newRateLimiter(rps, burst)

	// Use burst requests
	for i := 0; i < burst; i++ {
		if !rl.Allow("192.168.1.1") {
			t.Fatalf("request %d should be allowed", i+1)
		}
	}

	// Next request should be blocked (no time elapsed)
	if rl.Allow("192.168.1.1") {
		t.Error("request should be blocked after burst exhausted")
	}
}

func TestRateLimit_PerIPIsolation(t *testing.T) {
	rl := newRateLimiter(1000, 5)

	// Exhaust burst for IP A
	for i := 0; i < 5; i++ {
		rl.Allow("10.0.0.1")
	}

	// IP B should still have its own burst
	for i := 0; i < 5; i++ {
		if !rl.Allow("10.0.0.2") {
			t.Fatalf("IP B request %d should be allowed (separate bucket)", i+1)
		}
	}

	// IP A should be blocked
	if rl.Allow("10.0.0.1") {
		t.Error("IP A should still be blocked")
	}
}

func TestRateLimit_ReplenishesTokensOverTime(t *testing.T) {
	// Set RPS to 10, burst 2 — tokens replenish at ~1 per 100ms
	rl := newRateLimiter(10, 2)

	// Use burst
	for i := 0; i < 2; i++ {
		rl.Allow("10.0.0.1")
	}

	// Should be blocked
	if rl.Allow("10.0.0.1") {
		t.Fatal("should be blocked right after burst")
	}

	// Advance time by 200ms — should have ~2 new tokens
	rl.advanceTime("10.0.0.1", 200*time.Millisecond)

	if !rl.Allow("10.0.0.1") {
		t.Error("should have replenished after 200ms")
	}
}

func TestRateLimitMiddleware_EnforcesOnPOST(t *testing.T) {
	// Override rate limit to very low for test
	t.Setenv("MCP_RATE_LIMIT_RPS", "1")
	t.Setenv("MCP_RATE_LIMIT_BURST", "3")

	prevClient := globalClient
	defer func() { globalClient = prevClient }()

	ds := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(defaultTestConfig()))
	}))
	defer ds.Close()
	t.Setenv("DATA_SERVICE_URL", ds.URL)
	globalClient = httpclient.New()

	r := newTestRouterFromConfig(t, defaultTestConfig())

	msg := map[string]any{
		"jsonrpc": "2.0",
		"id":      "1",
		"method":  "tools/list",
		"params":  map[string]any{},
	}
	bodyBytes, _ := json.Marshal(msg)

	sendPost := func() int {
		body := bytes.NewReader(bodyBytes)
		req := httptest.NewRequest("POST", "/mcp", body)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Tenant-ID", "default")
		req.RemoteAddr = "10.0.0.99:54321"
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, req)
		return rec.Code
	}

	// First 3 should succeed (burst)
	for i := 0; i < 3; i++ {
		if code := sendPost(); code == http.StatusTooManyRequests {
			t.Fatalf("POST %d should be allowed, got 429", i+1)
		}
	}

	// 4th should be rate limited
	code := sendPost()
	if code != http.StatusTooManyRequests {
		t.Errorf("expected 429 after burst, got %d", code)
	}
}

func TestRateLimitMiddleware_DoesNotBlockHealth(t *testing.T) {
	t.Setenv("MCP_RATE_LIMIT_RPS", "1")
	t.Setenv("MCP_RATE_LIMIT_BURST", "1")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	// Health should always work regardless of rate limit
	for i := 0; i < 10; i++ {
		req := httptest.NewRequest("GET", "/health", nil)
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("GET /health iteration %d = %d, want 200 (health should not be rate limited)", i+1, rec.Code)
		}
	}
}

func TestMinimalConfig(t *testing.T) {
	cfg := `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"endpoints": [
			{ "method": "GET", "path": "/health", "op": "builtin_health" }
		]
	}`
	r := newTestRouterFromConfig(t, cfg)
	req := httptest.NewRequest("GET", "/health", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("GET /health with minimal config = %d, want %d", rec.Code, http.StatusOK)
	}
}
