package httpclient

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestNew_DefaultValues(t *testing.T) {
	// Clear env variables that might be set in dev
	os.Unsetenv("DATA_SERVICE_URL")
	os.Unsetenv("DATA_SERVICE_TIMEOUT")

	c := New()
	if c.baseURL != "http://127.0.0.1:8084" {
		t.Errorf("baseURL = %q, want %q", c.baseURL, "http://127.0.0.1:8084")
	}
	if c.http.Timeout != 30*time.Second {
		t.Errorf("Timeout = %v, want %v", c.http.Timeout, 30*time.Second)
	}
}

func TestNew_CustomBaseURL(t *testing.T) {
	t.Setenv("DATA_SERVICE_URL", "http://custom:9999")
	c := New()
	if c.baseURL != "http://custom:9999" {
		t.Errorf("baseURL = %q, want %q", c.baseURL, "http://custom:9999")
	}
}

func TestNew_CustomBaseURLTrailingSlash(t *testing.T) {
	t.Setenv("DATA_SERVICE_URL", "http://custom:8084/")
	c := New()
	if c.baseURL != "http://custom:8084" {
		t.Errorf("baseURL = %q, want %q", c.baseURL, "http://custom:8084")
	}
}

func TestNew_CustomTimeout(t *testing.T) {
	t.Setenv("DATA_SERVICE_TIMEOUT", "15")
	c := New()
	if c.http.Timeout.String() != "15s" {
		t.Errorf("Timeout = %v, want 15s", c.http.Timeout)
	}
}

func TestNew_InvalidTimeoutFallsBack(t *testing.T) {
	t.Setenv("DATA_SERVICE_TIMEOUT", "not-a-number")
	c := New()
	if c.http.Timeout != 30*time.Second {
		t.Errorf("Timeout = %v, want default %v", c.http.Timeout, 30*time.Second)
	}
}

func TestCall_SuccessJSONObject(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			t.Errorf("method = %q, want GET", r.Method)
		}
		if r.URL.Path != "/students/abc-123" {
			t.Errorf("path = %q, want /students/abc-123", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"id":"abc-123","full_name":"Иван","course":2}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	result, err := c.Call(context.Background(), "/students/{id}", map[string]any{"id": "abc-123"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
	obj, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("result type = %T, want map[string]any", result)
	}
	if obj["id"] != "abc-123" {
		t.Errorf("obj[id] = %v, want abc-123", obj["id"])
	}
	if obj["course"] != float64(2) {
		t.Errorf("obj[course] = %v, want 2", obj["course"])
	}
}

func TestCall_SuccessJSONArray(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`[{"id":"1","name":"Alice"},{"id":"2","name":"Bob"}]`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	result, err := c.Call(context.Background(), "/students", map[string]any{"name": "Alice"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
	arr, ok := result.([]any)
	if !ok {
		t.Fatalf("result type = %T, want []any", result)
	}
	if len(arr) != 2 {
		t.Errorf("len(arr) = %d, want 2", len(arr))
	}
}

func TestCall_NotFoundReturnsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"error": "not found"}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students/{id}", map[string]any{"id": "nonexistent"})
	if err == nil {
		t.Fatalf("Call() expected error for 404, got nil")
	}
}

func TestCall_ServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("internal error"))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students", nil)
	if err == nil {
		t.Fatal("Call() expected error for 500, got nil")
	}
}

func TestCall_EmptyResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`null`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	result, err := c.Call(context.Background(), "/health", nil)
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
	if result != nil {
		t.Errorf("result = %v, want nil", result)
	}
}

func TestCall_QueryParams(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("name") != "Alice" {
			t.Errorf("query name = %q, want Alice", r.URL.Query().Get("name"))
		}
		if r.URL.Query().Get("limit") != "10" {
			t.Errorf("query limit = %q, want 10", r.URL.Query().Get("limit"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students", map[string]any{"name": "Alice", "limit": 10})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_MixedPathAndQueryParams(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/students/42/grades" {
			t.Errorf("path = %q, want /students/42/grades", r.URL.Path)
		}
		if r.URL.Query().Get("discipline_id") != "d1" {
			t.Errorf("query discipline_id = %q, want d1", r.URL.Query().Get("discipline_id"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students/{id}/grades", map[string]any{"id": "42", "discipline_id": "d1"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_PathParamEscaping(t *testing.T) {
	// SQL injection attempt via path param should have quotes escaped on the wire.
	// url.PathEscape escapes ' → %27 and space → %20 but leaves = (RFC-safe in path).
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		uri := r.URL.RequestURI()
		// Single quotes must be escaped — this is the SQL injection vector
		if strings.Contains(uri, "'") {
			t.Errorf("URI contains unescaped single quote, SQL injection: %q", uri)
		}
		// Decoded path must round-trip to original
		if r.URL.Path != "/students/abc' OR '1'='1" {
			t.Errorf("decoded path = %q, want original input", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students/{id}", map[string]any{"id": "abc' OR '1'='1"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_PathParamEscaping_Slash(t *testing.T) {
	// Path traversal attempt via path param should be URL-escaped
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		uri := r.URL.RequestURI()
		// ../ must NOT appear in the URI — it should be %2E%2E%2F
		if strings.Contains(uri, "../") {
			t.Errorf("URI contains unescaped '../': %q", uri)
		}
		// Decoded path must match the original (round-trip safe)
		if r.URL.Path != "/students/../../secret" {
			t.Errorf("decoded path = %q, want original input", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students/{id}", map[string]any{"id": "../../secret"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_QueryParamEscaping(t *testing.T) {
	// SQL injection via query param should be URL-escaped
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw := r.URL.RawQuery
		// Raw query must not contain raw quotes or spaces
		if strings.Contains(raw, "'") {
			t.Errorf("raw query contains unescaped quote: %q", raw)
		}
		if strings.Contains(raw, " ") {
			t.Errorf("raw query contains unescaped space: %q", raw)
		}
		// Decoded values must match the original
		vals := r.URL.Query()
		if vals.Get("name") != "Robert'; DROP TABLE Students;--" {
			t.Errorf("query name = %q, want injection payload", vals.Get("name"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/students", map[string]any{"name": "Robert'; DROP TABLE Students;--"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_QueryParamEscaping_SpecialChars(t *testing.T) {
	// Special chars in multi query params
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		vals := r.URL.Query()
		if vals.Get("q") != "a&b=c" {
			t.Errorf("query q = %q, want 'a&b=c'", vals.Get("q"))
		}
		if vals.Get("x") != "1+1=2" {
			t.Errorf("query x = %q, want '1+1=2'", vals.Get("x"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/search", map[string]any{"q": "a&b=c", "x": "1+1=2"})
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_NoParams(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.RawQuery != "" {
			t.Errorf("RawQuery = %q, want empty", r.URL.RawQuery)
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/health", nil)
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_AcceptHeader(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Accept") != "application/json" {
			t.Errorf("Accept header = %q, want application/json", r.Header.Get("Accept"))
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/health", nil)
	if err != nil {
		t.Fatalf("Call() returned error: %v", err)
	}
}

func TestCall_InvalidJSONInResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{invalid`))
	}))
	defer srv.Close()

	c := &Client{
		baseURL: srv.URL,
		http:    srv.Client(),
	}

	_, err := c.Call(context.Background(), "/health", nil)
	if err == nil {
		t.Fatal("Call() expected error for invalid JSON response, got nil")
	}
}

// ════════════════════════════════════════════════════════════════
// SSRF Protection Tests
// ════════════════════════════════════════════════════════════════

func TestSSRF_ValidateURL_BlocksPrivateIPv4Ranges(t *testing.T) {
	tests := []struct {
		name string
		url  string
	}{
		{"127.0.0.1 loopback", "http://127.0.0.1:8084/api"},
		{"127.0.0.2 loopback", "http://127.0.0.2:8084/api"},
		{"10.0.0.1 /8", "http://10.0.0.1:8084/api"},
		{"10.255.255.255 /8", "http://10.255.255.255:8084/api"},
		{"192.168.1.1 /16", "http://192.168.1.1:8084/api"},
		{"192.168.0.0 /16", "http://192.168.0.1:8084/api"},
		{"172.16.0.1 /12", "http://172.16.0.1:8084/api"},
		{"172.31.255.255 /12", "http://172.31.255.255:8084/api"},
		{"169.254.169.254 metadata", "http://169.254.169.254:8084/api"},
		{"169.254.1.1 link-local", "http://169.254.1.1:8084/api"},
		{"100.64.0.1 CGNAT", "http://100.64.0.1:8084/api"},
		{"0.0.0.0 current network", "http://0.0.0.0:8084/api"},
		{"localhost hostname", "http://localhost:8084/api"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateURL(tt.url)
			if err == nil {
				t.Errorf("ValidateURL(%q) = nil, want error (private IP blocked)", tt.url)
			}
		})
	}
}

func TestSSRF_ValidateURL_AllowsPublicIPs(t *testing.T) {
	tests := []struct {
		name string
		url  string
	}{
		{"Google DNS", "http://8.8.8.8:8084/api"},
		{"Cloudflare DNS", "http://1.1.1.1:8084/api"},
		{"Quad9", "http://9.9.9.9:8084/api"},
		{"hostname with public DNS", "http://example.com:8084/api"},
		{"HTTPS public hostname", "https://api.example.com/v1/data"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateURL(tt.url)
			if err != nil {
				t.Errorf("ValidateURL(%q) = %v, want nil (should allow public)", tt.url, err)
			}
		})
	}
}

func TestSSRF_ValidateURL_InvalidURL(t *testing.T) {
	tests := []struct {
		name string
		url  string
	}{
		{"empty", ""},
		{"no scheme", "127.0.0.1:8084/api"},
		{"relative path", "/api/data"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateURL(tt.url)
			if err == nil {
				t.Errorf("ValidateURL(%q) = nil, want error for invalid URL", tt.url)
			}
		})
	}
}

func TestSSRF_New_LogsWarningForPrivateIP(t *testing.T) {
	// New() should not panic or error when DATA_SERVICE_URL points to private IP
	// (dev default is 127.0.0.1). It should log a warning but still succeed.
	os.Unsetenv("DATA_SERVICE_URL")
	os.Unsetenv("DATA_SERVICE_TIMEOUT")

	c := New()
	if c == nil {
		t.Fatal("New() returned nil for private IP URL")
	}
	if c.baseURL != "http://127.0.0.1:8084" {
		t.Errorf("baseURL = %q, want http://127.0.0.1:8084", c.baseURL)
	}
}
