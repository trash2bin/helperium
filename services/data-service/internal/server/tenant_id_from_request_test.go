package server

import (
	"bytes"
	"context"
	"log/slog"
	"net/http/httptest"
	"strings"
	"testing"
)

// Repo-wide tenant-ID contract (AGENTS.md, "MCP scope"): tenant ID allows
// [A-Za-z0-9][A-Za-z0-9_-]{0,127}. The gateway (mcp-gateway tenantIDPattern)
// enforces this for header-based MCP scopes; the data-service query fallback
// must not accept values the contract rejects.
const testTenantIDPattern = `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`

// ── tenantIDFromRequest: query fallback hardening ──

func TestTenantIDFromRequest_QueryFallbackValidID(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=shop-1", nil)
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Errorf("valid query tenant: got %q, want %q", got, "shop-1")
	}
}

func TestTenantIDFromRequest_QueryFallbackInvalidIDsRejected(t *testing.T) {
	cases := []struct {
		name       string
		queryValue string
	}{
		{name: "path traversal", queryValue: "../evil"},
		{name: "path separator", queryValue: "a/b"},
		{name: "dotfile", queryValue: ".hidden"},
		{name: "too long", queryValue: strings.Repeat("a", 129)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest("GET", "/students?tenant="+tc.queryValue, nil)
			if got := tenantIDFromRequest(req); got != "" {
				t.Errorf("invalid query tenant %q: got %q, want empty", tc.queryValue, got)
			}
			_ = testTenantIDPattern // referenced to document the contract source
		})
	}
}

func TestTenantIDFromRequest_QueryFallbackCompositeTakesFirst(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=shop-1,default", nil)
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Errorf("composite query tenant: got %q, want %q", got, "shop-1")
	}
}

// The ?tenant= query fallback is browser-controlled input kept only for the
// Swagger UI spec fetch and curl-style workflows documented in doc/RUNBOOK.md.
// It must warn on every use so operators migrate to the X-Tenant-ID header.
func TestTenantIDFromRequest_QueryFallbackLogsDeprecation(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	defer slog.SetDefault(prev)

	req := httptest.NewRequest("GET", "/students?tenant=shop-1", nil)
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Fatalf("valid query tenant: got %q, want %q", got, "shop-1")
	}
	logs := buf.String()
	if !strings.Contains(logs, "deprecated") {
		t.Errorf("expected deprecation warning for ?tenant= use, got: %s", logs)
	}
	if !strings.Contains(logs, "X-Tenant-ID") {
		t.Errorf("deprecation warning must point to the X-Tenant-ID header, got: %s", logs)
	}
}

func TestTenantIDFromRequest_HeaderPathNoDeprecation(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	defer slog.SetDefault(prev)

	req := httptest.NewRequest("GET", "/students", nil)
	req.Header.Set("X-Tenant-ID", "shop-1")
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Fatalf("header tenant: got %q, want %q", got, "shop-1")
	}
	if strings.Contains(buf.String(), "deprecated") {
		t.Errorf("header path must not log a deprecation warning, got: %s", buf.String())
	}
}

func TestTenantIDFromRequest_HeaderPathUnaffected(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	req.Header.Set("X-Tenant-ID", "../header-value")
	// Header path keeps current behavior: no validation added in this lane.
	if got := tenantIDFromRequest(req); got != "../header-value" {
		t.Errorf("header path changed: got %q, want %q (header behavior must stay as-is)", got, "../header-value")
	}
}

func TestTenantIDFromRequest_ContextPathUnaffected(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	ctx := context.WithValue(req.Context(), tenantIDKey, "ctx-tenant")
	req = req.WithContext(ctx)
	if got := tenantIDFromRequest(req); got != "ctx-tenant" {
		t.Errorf("context path changed: got %q, want %q", got, "ctx-tenant")
	}
}

func TestTenantIDFromRequest_NoTenant(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	if got := tenantIDFromRequest(req); got != "" {
		t.Errorf("no tenant: got %q, want empty", got)
	}
}
