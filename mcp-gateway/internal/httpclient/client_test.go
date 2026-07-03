package httpclient

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestNew_DefaultValues(t *testing.T) {
	// Clear env variables that might be set in dev
	os.Unsetenv("DATA_SERVICE_URL")
	os.Unsetenv("DATA_SERVICE_TIMEOUT")

	c := New()
	if c.baseURL != defaultBaseURL {
		t.Errorf("baseURL = %q, want %q", c.baseURL, defaultBaseURL)
	}
	if c.http.Timeout != defaultTimeout {
		t.Errorf("Timeout = %v, want %v", c.http.Timeout, defaultTimeout)
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
	if c.http.Timeout != defaultTimeout {
		t.Errorf("Timeout = %v, want default %v", c.http.Timeout, defaultTimeout)
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
