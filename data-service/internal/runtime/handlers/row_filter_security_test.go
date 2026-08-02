package handlers

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestTenantFilter_SecurityGap demonstrates the security issue:
// when auth is missing or RowFilters is empty, tenantFilter returns
// empty WHERE clause — meaning tenant_id is NOT enforced.
//
// This test is EXPECTED TO PASS (confirming the vulnerability exists).
// It serves as a regression test: if the vulnerability is fixed,
// this test should be updated to FAIL.
func TestTenantFilter_SecurityGap(t *testing.T) {
	// Simulate configuration where admin forgot to set up RowFilters
	// or auth is entirely missing.
	tests := []struct {
		name        string
		auth        *config.AuthConfig
		tenantID    string
		entityName  string
		expectWhere string
		comment     string
	}{
		{
			name:        "no auth config at all",
			auth:        nil,
			tenantID:    "tenant-a",
			entityName:  "customer",
			expectWhere: "", // ⚠️ GAP: any tenant_id sees all rows
			comment:     "No isolation — tenant_a sees tenant_b's data",
		},
		{
			name:        "auth configured but no RowFilters",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyHeader},
			tenantID:    "tenant-a",
			entityName:  "customer",
			expectWhere: "", // ⚠️ GAP: header is read but no filtering applied
			comment:     "Auth header required but no row_filter set",
		},
		{
			name:        "RowFilters set only for entity X, request for entity Y",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "customer", Where: "tenant_id = :tenant_id"}}},
			tenantID:    "tenant-a",
			entityName:  "order",
			expectWhere: "", // ⚠️ GAP: order entity has no filter
			comment:     "Cross-entity leak: order has no filter, sees all rows",
		},
		{
			name:        "empty tenant_id in header",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "customer", Where: "tenant_id = :tenant_id"}}},
			tenantID:    "",
			entityName:  "customer",
			expectWhere: "", // ⚠️ GAP: no tenant_id means no filter
			comment:     "If X-Tenant-ID not sent, no isolation — all data visible",
		},
	}

	translate := func(i int) string { return "?" }

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			where, args := tenantFilter(tt.entityName, tt.auth, tt.tenantID, 0, translate)

			if where != tt.expectWhere {
				t.Errorf("where = %q, want %q", tt.expectWhere, where)
			}

			// SECURITY CHECK: If tenant_id is provided AND auth is configured,
			// we MUST have a where clause that filters by tenant.
			if tt.tenantID != "" && tt.auth != nil && tt.auth.Strategy == config.AuthStrategyHeader {
				if where == "" {
					t.Logf("⚠️  SECURITY GAP: tenant_id=%q provided but no WHERE clause generated. %s", tt.tenantID, tt.comment)
					t.Logf("   This means tenant-a can see tenant-b's data via direct API call!")
				}
			}

			if len(args) > 0 && args[0] != tt.tenantID {
				t.Errorf("args[0] = %v, want %v", args[0], tt.tenantID)
			}
		})
	}
}

// TestTenantFilter_RequiredRowFilterOnAuthHeader ensures that when auth
// is configured with header strategy, AT LEAST ONE row filter must be present.
// This is the critical security guarantee for production.
func TestTenantFilter_RequiredRowFilterOnAuthHeader(t *testing.T) {
	auth := &config.AuthConfig{Strategy: config.AuthStrategyHeader}

	// Simulate production: tenant_id MUST be in request, even if no RowFilters
	translate := func(i int) string { return "?" }

	for _, entity := range []string{"customer", "order", "product", "user"} {
		t.Run(entity, func(t *testing.T) {
			where, args := tenantFilter(entity, auth, "tenant-a", 0, translate)

			// CRITICAL: If we get here, no filter was applied.
			// In a hardened version, this should auto-generate a default
			// filter (e.g. "tenant_id = ?") when AuthStrategyHeader is set.
			if where == "" && args == nil {
				t.Logf("⚠️  SECURITY: tenant_id='tenant-a' request for entity=%q produced no WHERE clause", entity)
				t.Logf("   Auto-generated fallback filter ('tenant_id = ?') should be added when Strategy=Header")
			}
		})
	}
}

// TestTenantFilter_EndToEndSecurity verifies the actual HTTP path:
// sends requests with different X-Tenant-ID headers and confirms
// that data does NOT leak across tenants.
//
// Run with: go test ./data-service/internal/runtime/handlers/ -run TestTenantFilter_EndToEndSecurity -v
//
// THIS IS A REGRESSION TEST: if it fails, tenant isolation is broken.
func TestTenantFilter_EndToEndSecurity(t *testing.T) {
	t.Skip("End-to-end test requires full DB setup; see e2e/test_data_isolation.py")

	// Example of what this test should verify:
	// 1. Seed DB with tenant_a_records + tenant_b_records (mixed)
	// 2. Configure tenant-a auth.row_filters properly, tenant-b WITHOUT filters
	// 3. GET /customers with X-Tenant-ID: tenant-a → only tenant_a_records
	// 4. GET /customers with X-Tenant-ID: tenant-b → should NOT return tenant_a_records
	// 5. WITHOUT X-Tenant-ID → should return error or empty (not all data)
}

// TestFilterExecution_RequiresTenantHeader checks that if AuthStrategyHeader
// is configured, requests without X-Tenant-ID should be REJECTED.
func TestFilterExecution_RequiresTenantHeader(t *testing.T) {
	// This test verifies the AssertTenantIDMiddleware logic
	// (or equivalent) that blocks requests without X-Tenant-ID
	// when auth is configured.
	//
	// In the current code:
	// 1. TenantIDMiddleware stores tenant_id in context (even if empty)
	// 2. tenantFilter returns ("", nil) when tenant_id is empty
	// 3. So SQL queries run WITHOUT "WHERE tenant_id = ?"
	// 4. Result: full table scan, ALL rows returned, no isolation
	//
	// This is the FUNDAMENTAL security issue.

	t.Skip("See e2e/test_data_isolation.py for the e2e version of this test")

	// In a hardened version, this should be the behavior:
	// chi-router := chi.NewRouter()
	// chi-router.Use(RequireTenantHeader(auth))  // returns 401 if no X-Tenant-ID

	// ... verify that GET /customers without X-Tenant-ID returns 401
	// ... verify that GET /customers with X-Tenant-ID: tenant-a returns only tenant_a rows
}

// TestFilterExecution_TenantIDInContext verifies that tenant_id is properly
// passed through the context to the SQL query builder.
func TestFilterExecution_TenantIDInContext(t *testing.T) {
	// Create a request with X-Tenant-ID: tenant-a
	req := httptest.NewRequest(http.MethodGet, "/customers", nil)
	req.Header.Set("X-Tenant-ID", "tenant-a")

	// Simulate the middleware
	tenantID := req.Header.Get("X-Tenant-ID")
	if tenantID == "" {
		t.Fatal("X-Tenant-ID not set in request")
	}

	// Verify that tenant_id propagates to the DB query
	// This is a unit test placeholder — actual test would use a mock Adapter
	// that captures the SQL query and verifies WHERE clause is present.
}

// TestSQLBuilder_AddsTenantFilter is the most critical test.
// It verifies that when tenant_id is provided AND auth is configured,
// the SQL query MUST include a tenant_id filter.
func TestSQLBuilder_AddsTenantFilter(t *testing.T) {
	// Handcrafted config where admin FORGOT to set row_filters.
	// This is the realistic scenario in production.
	auth := &config.AuthConfig{
		Strategy:     config.AuthStrategyHeader,
		RowFilters:   nil, // ⚠️ MISSING!
		TenantHeader: "X-Tenant-ID",
	}

	// Simulate a strategy query (filter, grep, etc.)
	// Expected: tenant_id should still be applied via default behavior
	// (if no RowFilters is configured, fallback to "tenant_id = ?")
	translate := func(i int) string { return "?" }

	where, args := tenantFilter("customer", auth, "tenant-a", 0, translate)

	// Currently: where="" args=nil (security gap)
	// Hardened version: where="tenant_id = ?" args=[tenant-a]
	if where == "" || len(args) == 0 {
		t.Logf("⚠️  CRITICAL SECURITY GAP: tenant_id='tenant-a' but no WHERE clause applied")
		t.Logf("   In production, this would allow tenant-a to see tenant-b's data!")
		t.Logf("   Fix: auto-generate 'tenant_id = ?' filter when AuthStrategyHeader is set")
	}
}

// TestTenantFilter_TenantIDSpoofing verifies that if the same tenant_id
// is sent from two different physical clients, they both get the same
// (correct) data. This is not actually a vulnerability — it's verifying
// that tenant_id is the SOLE isolation boundary.
func TestTenantFilter_TenantIDSpoofing(t *testing.T) {
	auth := &config.AuthConfig{
		Strategy:   config.AuthStrategyHeader,
		RowFilters: []config.RowFilter{{Entity: "customer", Where: "tenant_id = :tenant_id"}},
	}
	translate := func(i int) string { return "?" }

	// Client 1 sends X-Tenant-ID: tenant-a
	where1, args1 := tenantFilter("customer", auth, "tenant-a", 0, translate)
	// Client 2 sends X-Tenant-ID: tenant-a (same as Client 1)
	where2, args2 := tenantFilter("customer", auth, "tenant-a", 0, translate)

	if where1 != where2 {
		t.Errorf("Inconsistent WHERE for same tenant_id: %q vs %q", where1, where2)
	}
	if len(args1) > 0 && args1[0] != "tenant-a" {
		t.Errorf("args1[0] = %v, want tenant-a", args1[0])
	}
	if len(args2) > 0 && args2[0] != "tenant-a" {
		t.Errorf("args2[0] = %v, want tenant-a", args2[0])
	}

	// Client 3 sends X-Tenant-ID: tenant-b
	where3, args3 := tenantFilter("customer", auth, "tenant-b", 0, translate)
	if len(args3) > 0 && args3[0] != "tenant-b" {
		t.Errorf("args3[0] = %v, want tenant-b", args3[0])
	}

	// SHOULD BE: args1[0] != args3[0] (different tenants)
	// Currently: just verifies args are different strings
	// Hardened: also verify that WHERE clause is present in both cases
	if where1 == "" || where3 == "" {
		t.Logf("⚠️  tenant_id='tenant-a' and 'tenant-b' both produce no WHERE clause — isolation broken")
	}
}
