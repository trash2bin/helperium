package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/mcp-gateway/internal/httpclient"
)

// Regression test: an unknown (but well-formed) X-Tenant-ID must not surface
// as HTTP 500 "Failed to create MCP server".
//
// Live probe (2026-09-12): POST /mcp with `X-Tenant-ID: nosuchtenant`
// returned 500 even though data-service answered 404 tenant_not_found on
// /mcp/manifest. 500 implies server failure while the real cause is a client
// error (unknown scope) — the gateway must map the upstream 4xx to a client
// error so it doesn't trip retry paths and error budgets, and so the response
// doesn't leak that the scope failed to *create*.
func TestUnknownTenantManifest404MapsToClientError(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/mcp/manifest" && r.Header.Get("X-Tenant-ID") == "nosuchtenant" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			w.Write([]byte(`{"error":"tenant_not_found","error_code":"tenant_not_found","message":"tenant not found"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer upstream.Close()

	prevURL, hadURL := os.LookupEnv("DATA_SERVICE_URL")
	if err := os.Setenv("DATA_SERVICE_URL", upstream.URL); err != nil {
		t.Fatalf("set DATA_SERVICE_URL: %v", err)
	}
	t.Cleanup(func() {
		if hadURL {
			os.Setenv("DATA_SERVICE_URL", prevURL)
		} else {
			os.Unsetenv("DATA_SERVICE_URL")
		}
	})

	prevClient := globalClient
	globalClient = httpclient.New()
	t.Cleanup(func() { globalClient = prevClient })

	registry := newStreamableTenantRegistry()

	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"regression","version":"1"}}}`,
	))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", "nosuchtenant")

	rec := httptest.NewRecorder()
	registry.serveHTTP(rec, req)

	if rec.Code == http.StatusInternalServerError {
		t.Fatalf("unknown tenant must not yield 500 (upstream 404 is a client error); got %d body=%q",
			rec.Code, rec.Body.String())
	}
	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404 Not Found for unknown tenant, got %d body=%q",
			rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	for _, marker := range []string{"tenant_not_found", "/mcp/manifest", "nosuchtenant"} {
		if strings.Contains(body, marker) {
			t.Fatalf("response body leaks upstream/internal detail %q: %q", marker, body)
		}
	}
}

// Companion check: an upstream 5xx (data-service down) must remain a retryable
// server error, i.e. NOT be turned into the same 500 the unknown-tenant path
// currently produces — the two failure classes must be distinguishable.
func TestUpstreamUnavailableStillServerError(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/mcp/manifest" {
			http.Error(w, "internal", http.StatusInternalServerError)
			return
		}
		http.NotFound(w, r)
	}))
	defer upstream.Close()

	prevURL, hadURL := os.LookupEnv("DATA_SERVICE_URL")
	os.Setenv("DATA_SERVICE_URL", upstream.URL)
	t.Cleanup(func() {
		if hadURL {
			os.Setenv("DATA_SERVICE_URL", prevURL)
		} else {
			os.Unsetenv("DATA_SERVICE_URL")
		}
	})

	prevClient := globalClient
	globalClient = httpclient.New()
	t.Cleanup(func() { globalClient = prevClient })

	registry := newStreamableTenantRegistry()
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"regression","version":"1"}}}`,
	))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", "autoparts")

	rec := httptest.NewRecorder()
	registry.serveHTTP(rec, req)

	if rec.Code < http.StatusInternalServerError {
		t.Fatalf("upstream 5xx must stay a server error (retryable), got %d body=%q",
			rec.Code, rec.Body.String())
	}
}
