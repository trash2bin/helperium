package server

import (
	"context"
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
