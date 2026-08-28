package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ── C1b: viewer must not receive agent llm_config.api_key ───────────────

const testAgentAPIKey = "sk-live-secret-agent-key-12345"

// newAgentProxyTestServer builds a Server whose api-service is a test upstream
// returning an AgentResponse whose llm_config carries a plaintext api_key —
// the api-service Agent DTO has no server-side masking for this field.
func newAgentProxyTestServer(t *testing.T) *Server {
	t.Helper()

	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		agentJSON := `{"name":"shop-agent","description":"","tenant_ids":["shop"],` +
			`"llm_config":{"provider":"openai","api_key":"` + testAgentAPIKey + `","model":"gpt-4o-mini"},` +
			`"provider_priority":["openai"],"abuse_config":null,"system_prompt":null,"voice_config":null,"widget_config":null}`
		if r.URL.Path == "/api/agents" {
			// Mirror api-service AgentListResponse shape.
			_, _ = w.Write([]byte(`{"agents":[` + agentJSON + `]}`))
			return
		}
		_, _ = w.Write([]byte(agentJSON))
	}))
	t.Cleanup(upstream.Close)

	return New(Options{
		Addr:           ":0",
		DataSvcURL:     "http://127.0.0.1:1", // unused by agent proxies
		ApiSvcURL:      upstream.URL,
		ApiBearerToken: "api-token",
		AdminToken:     "admin-secret",
		ViewerToken:    "viewer-secret",
	})
}

func TestViewerAgentListMasksLlmConfigAPIKey(t *testing.T) {
	s := newAgentProxyTestServer(t)

	w := requestWithToken(t, s, http.MethodGet, "/api/agents", "viewer-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("viewer agents status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if body := w.Body.String(); strings.Contains(body, testAgentAPIKey) {
		t.Fatalf("viewer agent list leaked llm_config.api_key: %s", body)
	}

	var payload struct {
		Agents []struct {
			LlmConfig *struct {
				APIKey string `json:"api_key"`
			} `json:"llm_config"`
		} `json:"agents"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v; body=%s", err, w.Body.String())
	}
	if len(payload.Agents) == 0 || payload.Agents[0].LlmConfig == nil {
		t.Fatalf("expected one agent with llm_config; body=%s", w.Body.String())
	}
	if payload.Agents[0].LlmConfig.APIKey != maskedDSN {
		t.Errorf("api_key = %q, want %q", payload.Agents[0].LlmConfig.APIKey, maskedDSN)
	}
}

func TestViewerAgentGetMasksLlmConfigAPIKey(t *testing.T) {
	s := newAgentProxyTestServer(t)

	w := requestWithToken(t, s, http.MethodGet, "/api/agents/shop-agent", "viewer-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("viewer agent status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if body := w.Body.String(); strings.Contains(body, testAgentAPIKey) {
		t.Fatalf("viewer agent response leaked llm_config.api_key: %s", body)
	}
}

func TestAdminAgentGetPreservesLlmConfigAPIKey(t *testing.T) {
	// The admin UI round-trips agent llm_config via GET-then-PUT, so the real
	// value must survive for the admin role.
	s := newAgentProxyTestServer(t)

	w := requestWithToken(t, s, http.MethodGet, "/api/agents/shop-agent", "admin-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("admin agent status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if body := w.Body.String(); !strings.Contains(body, testAgentAPIKey) {
		t.Fatalf("admin agent response lost llm_config.api_key: %s", body)
	}
}

// ── C1: viewer must not receive readonly_dsn ────────────────────────────────

const (
	testReadonlyDSN = "postgres://readonly-user:readonly-pass@db.internal:5432/shop"
	maskedDSN       = "***masked***"
)

// newConfigProxyTestServer builds a Server whose data-service is a test
// upstream returning a tenant config DTO that contains a real readonly_dsn.
func newConfigProxyTestServer(t *testing.T) (*Server, *bool) {
	t.Helper()

	sawViewerRole := false
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/admin/config" {
			// Mirror the data-service adminDataSourceResponse shape.
			role := r.Header.Get("X-Test-Role")
			if role == "viewer" {
				sawViewerRole = true
			}
			// The upstream can never know the dashboard role; it always
			// returns the full DTO. Masking is the dashboard's job.
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"version": 1,
				"data_source": map[string]any{
					"driver":           "postgres",
					"read_only":        true,
					"readonly_dsn":     testReadonlyDSN,
					"has_readonly_dsn": true,
				},
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	t.Cleanup(upstream.Close)

	s := New(Options{
		Addr:        ":0",
		DataSvcURL:  upstream.URL,
		AdminToken:  "admin-secret",
		ViewerToken: "viewer-secret",
	})
	// The upstream handler reads the dashboard role from this header so the
	// test can assert which request path (viewer vs admin) hit the proxy.
	_ = sawViewerRole
	return s, &sawViewerRole
}

func requestWithToken(t *testing.T, s *Server, method, path, token string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(method, path, nil)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	s.Router().ServeHTTP(w, req)
	return w
}

func TestViewerGetTenantConfigMasksReadonlyDSN(t *testing.T) {
	s, _ := newConfigProxyTestServer(t)

	w := requestWithToken(t, s, http.MethodGet, "/api/tenants/shop/config", "viewer-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("viewer config status = %d, want 200; body=%s", w.Code, w.Body.String())
	}

	body := w.Body.String()
	if strings.Contains(body, testReadonlyDSN) {
		t.Fatalf("viewer response leaked readonly_dsn: %s", body)
	}
	if strings.Contains(body, "readonly-pass") || strings.Contains(body, "db.internal") {
		t.Fatalf("viewer response leaked DSN fragments: %s", body)
	}

	var payload struct {
		DataSource struct {
			ReadonlyDSN    string `json:"readonly_dsn"`
			HasReadonlyDSN bool   `json:"has_readonly_dsn"`
		} `json:"data_source"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v; body=%s", err, body)
	}
	if payload.DataSource.ReadonlyDSN != maskedDSN {
		t.Errorf("readonly_dsn = %q, want %q", payload.DataSource.ReadonlyDSN, maskedDSN)
	}
	if !payload.DataSource.HasReadonlyDSN {
		t.Errorf("has_readonly_dsn = false, want true (viewer must still see that a DSN exists)")
	}
}

func TestAdminGetTenantConfigPreservesReadonlyDSN(t *testing.T) {
	s, _ := newConfigProxyTestServer(t)

	w := requestWithToken(t, s, http.MethodGet, "/api/tenants/shop/config", "admin-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("admin config status = %d, want 200; body=%s", w.Code, w.Body.String())
	}

	var payload struct {
		DataSource struct {
			ReadonlyDSN string `json:"readonly_dsn"`
		} `json:"data_source"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if payload.DataSource.ReadonlyDSN != testReadonlyDSN {
		t.Errorf("admin readonly_dsn = %q, want byte-for-byte %q (needed for round-trip PUT)",
			payload.DataSource.ReadonlyDSN, testReadonlyDSN)
	}
}

func TestViewerMaskingDoesNotTouchNonConfigProxies(t *testing.T) {
	// /admin/tenants responses carry no DSN; masking must not corrupt them.
	var upstreamBody string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamBody = `{"tenants":[{"id":"shop","driver":"postgres","entities":3,"endpoints":5,"healthy":true,"created_at":"2026-08-28T00:00:00Z"}]}`
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(upstreamBody))
	}))
	defer upstream.Close()

	s := New(Options{
		Addr:        ":0",
		DataSvcURL:  upstream.URL,
		AdminToken:  "admin-secret",
		ViewerToken: "viewer-secret",
	})

	w := requestWithToken(t, s, http.MethodGet, "/api/tenants", "viewer-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("tenants list status = %d, want 200", w.Code)
	}
	if w.Body.String() != upstreamBody {
		t.Errorf("tenants list body mutated: got %s, want %s", w.Body.String(), upstreamBody)
	}
}

// ── C2: constant-time token compare ─────────────────────────────────────────

func TestAuthMiddlewareBehaviorParity(t *testing.T) {
	const (
		adminToken  = "admin-secret"
		viewerToken = "viewer-secret"
	)
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	})
	handler := authMiddleware(adminToken, viewerToken)(next)

	cases := []struct {
		name      string
		auth      string
		wantState int
	}{
		{"valid admin", "Bearer " + adminToken, http.StatusTeapot},
		{"valid viewer", "Bearer " + viewerToken, http.StatusTeapot},
		{"invalid token", "Bearer wrong-token", http.StatusUnauthorized},
		{"empty bearer", "Bearer ", http.StatusUnauthorized},
		{"no auth header", "", http.StatusUnauthorized},
		{"malformed scheme", "Basic " + adminToken, http.StatusUnauthorized},
		{"prefix only partial", "Bearer admin", http.StatusUnauthorized},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
			if tc.auth != "" {
				req.Header.Set("Authorization", tc.auth)
			}
			res := httptest.NewRecorder()
			handler.ServeHTTP(res, req)
			if res.Code != tc.wantState {
				t.Errorf("status = %d, want %d", res.Code, tc.wantState)
			}
		})
	}
}

// ── C3: proxy response header filtering ─────────────────────────────────────

func TestProxyToDataServiceFiltersUpstreamHeaders(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Request-Id", "req-123")
		w.Header().Set("Set-Cookie", "session=secret; HttpOnly; Path=/")
		w.Header().Set("Transfer-Encoding", "chunked")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Add("Set-Cookie", "second=cookie; Path=/")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()

	s := New(Options{
		Addr:       ":0",
		DataSvcURL: upstream.URL,
		AdminToken: "admin-secret",
	})

	w := requestWithToken(t, s, http.MethodGet, "/api/tenants", "admin-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("proxy status = %d, want 200; body=%s", w.Code, w.Body.String())
	}

	if got := w.Header().Values("Set-Cookie"); len(got) != 0 {
		t.Errorf("Set-Cookie leaked to client: %v", got)
	}
	for _, hop := range []string{"Transfer-Encoding", "Connection", "Keep-Alive", "Upgrade", "Proxy-Authenticate", "Proxy-Authorization", "Trailer", "Te"} {
		if got := w.Header().Values(hop); len(got) > 0 {
			t.Errorf("hop-by-hop header %s forwarded: %v", hop, got)
		}
	}
	if got := w.Header().Get("X-Request-Id"); got != "req-123" {
		t.Errorf("X-Request-Id = %q, want req-123 (safe headers must pass through)", got)
	}
	if got := w.Header().Get("Content-Type"); !strings.HasPrefix(got, "application/json") {
		t.Errorf("Content-Type = %q, want application/json passthrough", got)
	}
	if w.Body.String() != `{"ok":true}` {
		t.Errorf("body mutated: %s", w.Body.String())
	}
}
