package server

import (
	"context"
	"net/http/httptest"
	"testing"
)

// tenantIDFromRequest: query parameter is NOT tenant authority.
// Per AGENTS.md tenant authority contract, only X-Tenant-ID (context or
// header) selects the tenant. The ?tenant= query parameter is ignored
// entirely — even a valid value does not select a tenant. This mirrors
// mcp-gateway's resolveTenantIDs (header-only).

func TestTenantIDFromRequest_QueryDoesNotSelectTenant(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=shop-1", nil)
	if got := tenantIDFromRequest(req); got != "" {
		t.Errorf("?tenant= must not select tenant: got %q, want empty", got)
	}
}

func TestTenantIDFromRequest_QueryDoesNotSelectTenantComposite(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=shop-1,default", nil)
	if got := tenantIDFromRequest(req); got != "" {
		t.Errorf("composite ?tenant= must not select tenant: got %q, want empty", got)
	}
}

func TestTenantIDFromRequest_QueryDoesNotSelectTenantInvalid(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=../evil", nil)
	if got := tenantIDFromRequest(req); got != "" {
		t.Errorf("invalid ?tenant= must not select tenant: got %q, want empty", got)
	}
}

func TestTenantIDFromRequest_HeaderOverridesQuery(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=other", nil)
	req.Header.Set("X-Tenant-ID", "shop-1")
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Errorf("header must override query: got %q, want shop-1", got)
	}
}

func TestTenantIDFromRequest_ContextOverridesQuery(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=other", nil)
	ctx := context.WithValue(req.Context(), tenantIDKey, "ctx-tenant")
	req = req.WithContext(ctx)
	if got := tenantIDFromRequest(req); got != "ctx-tenant" {
		t.Errorf("context must override query: got %q, want ctx-tenant", got)
	}
}

func TestTenantIDFromRequest_HeaderPath(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	req.Header.Set("X-Tenant-ID", "shop-1")
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Errorf("header tenant: got %q, want shop-1", got)
	}
}

func TestTenantIDFromRequest_HeaderPathUnaffected(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	req.Header.Set("X-Tenant-ID", "../header-value")
	// Header path keeps current behavior: no validation added in this lane.
	if got := tenantIDFromRequest(req); got != "../header-value" {
		t.Errorf("header path changed: got %q, want %q", got, "../header-value")
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

func TestTenantIDFromRequest_CompositeHeader(t *testing.T) {
	req := httptest.NewRequest("GET", "/students", nil)
	req.Header.Set("X-Tenant-ID", "shop-1,default")
	if got := tenantIDFromRequest(req); got != "shop-1" {
		t.Errorf("composite header: got %q, want %q", got, "shop-1")
	}
}

func TestTenantIDFromRequest_NoTenant(t *testing.T) {
	req := httptest.NewRequest("GET", "/students?tenant=ignored", nil)
	if got := tenantIDFromRequest(req); got != "" {
		t.Errorf("no tenant: got %q, want empty", got)
	}
}
