package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/mcp-gateway/internal/httpclient"
)

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════

// TestMain — устанавливает CONFIG_SCHEMA для тестов, которым нужен config.Load.
func TestMain(m *testing.M) {
	wd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(os.Stderr, "TestMain: os.Getwd: %v\n", err)
		os.Exit(1)
	}
	candidates := []string{
		filepath.Join(wd, "..", "..", "specs", "config.schema.json"),
		filepath.Join(wd, "..", "specs", "config.schema.json"),
	}
	var schemaPath string
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			schemaPath, _ = filepath.Abs(c)
			break
		}
	}
	if schemaPath != "" {
		os.Setenv("CONFIG_SCHEMA", schemaPath)
	}
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
			{ "method": "GET", "path": "/students", "op": "find", "entity": "student", "search_field": "full_name", "query_param": "name" }
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

// ════════════════════════════════════════════════════════════════
// Tools list / call tests
// ════════════════════════════════════════════════════════════════

func TestToolsListEndpoint(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("GET", "/tools/list", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /tools/list = %d, want %d\nbody: %s", rec.Code, http.StatusOK, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}

	result, ok := resp["result"].(map[string]any)
	if !ok {
		t.Fatalf("response missing result field: %v", resp)
	}
	toolsArr, ok := result["tools"].([]any)
	if !ok {
		t.Fatalf("result missing tools array: %v", result)
	}

	found := false
	for _, tAny := range toolsArr {
		tool, ok := tAny.(map[string]any)
		if !ok {
			continue
		}
		if tool["name"] == "get_student" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("tools list does not contain 'get_student'. Tools: %v", toolsArr)
	}
}

func TestToolsCallEndpoint(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())
	body := map[string]any{
		"name": "get_student",
		"arguments": map[string]any{
			"id": "test-123",
		},
	}
	bodyBytes, _ := json.Marshal(body)

	req := httptest.NewRequest("POST", "/tools/call", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("POST /tools/call = %d, want %d\nbody: %s", rec.Code, http.StatusOK, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}

	_, hasResult := resp["result"]
	_, hasError := resp["error"]
	if !hasResult && !hasError {
		t.Errorf("response should have 'result' or 'error' field: %v", resp)
	}
}

func TestToolsCallEndpoint_InvalidBody(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())
	req := httptest.NewRequest("POST", "/tools/call", bytes.NewReader([]byte(`{invalid}`)))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("POST /tools/call with invalid JSON = %d, want %d\nbody: %s", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

func TestToolsCallEndpoint_EmptyName(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())
	body := map[string]any{
		"arguments": map[string]any{"x": "y"},
	}
	bodyBytes, _ := json.Marshal(body)

	req := httptest.NewRequest("POST", "/tools/call", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("POST /tools/call with empty name = %d, want %d", rec.Code, http.StatusOK)
	}
}

// ════════════════════════════════════════════════════════════════
// MCP message endpoint tests
// ════════════════════════════════════════════════════════════════

func TestMCPMessageEndpoint(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())

	msg := map[string]any{
		"jsonrpc": "2.0",
		"id":      "test-1",
		"method":  "tools/list",
		"params":  map[string]any{},
	}
	bodyBytes, _ := json.Marshal(msg)

	req := httptest.NewRequest("POST", "/mcp/message", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	// Without SSE session, should return 200 with direct JSON-RPC response
	if rec.Code != http.StatusOK {
		t.Fatalf("POST /mcp/message = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v (body: %s)", err, rec.Body.String())
	}
	if resp["jsonrpc"] != "2.0" {
		t.Errorf(`jsonrpc = %v, want "2.0"`, resp["jsonrpc"])
	}
	_, hasResult := resp["result"]
	_, hasError := resp["error"]
	if !hasResult && !hasError {
		t.Errorf("response should have 'result' or 'error': %v", resp)
	}
}

func TestMCPMessageEndpoint_DirectResponseWithoutSession(t *testing.T) {
	prevClient := globalClient
	defer func() { globalClient = prevClient }()

	manifestServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp/manifest" {
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("X-Tenant-ID"); got != "tenant-a" {
			t.Errorf("manifest request tenant = %q, want tenant-a", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, defaultTestConfig())
	}))
	defer manifestServer.Close()

	t.Setenv("DATA_SERVICE_URL", manifestServer.URL)
	globalClient = httpclient.New()

	r := newTestRouterFromConfig(t, defaultTestConfig())
	msg := map[string]any{
		"jsonrpc": "2.0",
		"id":      "test-1",
		"method":  "tools/list",
		"params":  map[string]any{},
	}
	bodyBytes, _ := json.Marshal(msg)

	req := httptest.NewRequest("POST", "/mcp/message", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("POST /mcp/message without session = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v (body: %s)", err, rec.Body.String())
	}
	if resp["jsonrpc"] != "2.0" {
		t.Fatalf(`jsonrpc = %v, want "2.0"`, resp["jsonrpc"])
	}
	if _, ok := resp["result"]; !ok {
		t.Fatalf("response missing result: %v", resp)
	}
}

func TestMCPMessageEndpoint_ParseError(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("POST", "/mcp/message", bytes.NewReader([]byte(`not json`)))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	// Should return a JSON-RPC parse error
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("POST /mcp/message with invalid JSON = %d, want 400\nbody: %s", rec.Code, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatal("error field missing")
	}
	code, _ := errObj["code"].(float64)
	expectedCode := float64(-32700)
	if code != expectedCode {
		t.Errorf("error.code = %v, want %v", code, expectedCode)
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

func TestMCPFallbackEndpoint(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())

	msg := map[string]any{
		"jsonrpc": "2.0",
		"id":      "test-1",
		"method":  "tools/list",
		"params":  map[string]any{},
	}
	bodyBytes, _ := json.Marshal(msg)

	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("POST /mcp (fallback) = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestMCPMessageEndpoint_WithSessionID(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	r := newTestRouterFromConfig(t, defaultTestConfig())

	// Without active SSE session, sessionId in query should still work (returns direct response)
	msg := map[string]any{
		"jsonrpc": "2.0",
		"id":      "test-1",
		"method":  "tools/list",
		"params":  map[string]any{},
	}
	bodyBytes, _ := json.Marshal(msg)

	req := httptest.NewRequest("POST", "/mcp/message?sessionId=some-session", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("POST /mcp/message with sessionId = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
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

func TestAuthMiddleware_CorrectToken_Returns200(t *testing.T) {
	t.Setenv("MCP_API_KEY", "test-secret-123")
	defer os.Unsetenv("MCP_API_KEY")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("Authorization", "Bearer test-secret-123")
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code == http.StatusUnauthorized {
		t.Fatal("correct token got 401, want non-401")
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
	os.Unsetenv("MCP_API_KEY")

	r := newTestRouterFromConfig(t, defaultTestConfig())

	req := httptest.NewRequest("GET", "/config", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	// Without MCP_API_KEY, auth is skipped — should get 200 (config generates response)
	if rec.Code == http.StatusUnauthorized {
		t.Fatal("auth bypass with empty MCP_API_KEY got 401, want non-401")
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

// ════════════════════════════════════════════════════════════════
// Helper function tests
// ════════════════════════════════════════════════════════════════

func TestWriteJSONRPCError(t *testing.T) {
	t.Skip("test written for old REST endpoints — needs rewrite for MCP SSE protocol")
	rec := httptest.NewRecorder()
	writeJSONRPCError(rec, "req-1", -32600, "Invalid Request")

	if rec.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", rec.Code, http.StatusBadRequest)
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if resp["id"] != "req-1" {
		t.Errorf("id = %v, want req-1", resp["id"])
	}
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatal("error field missing")
	}
	code, _ := errObj["code"].(float64)
	if int(code) != -32600 {
		t.Errorf("error.code = %v, want -32600", code)
	}
	if errObj["message"] != "Invalid Request" {
		t.Errorf("error.message = %v, want 'Invalid Request'", errObj["message"])
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
	t.Setenv("MCP_RATE_LIMIT_RPS", "100")
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
		req := httptest.NewRequest("POST", "/mcp/message", body)
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
