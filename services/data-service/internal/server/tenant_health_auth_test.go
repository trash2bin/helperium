package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// Pentest M2: GET /health без аутентификации не должен перечислять tenant'ов
// (id/driver/entities — инвентаризация целей для атакующего). Без токена —
// только агрегированный статус и счётчик; полный список — с Bearer ADMIN_TOKEN.

func newMultiTenantStore(t *testing.T) *TenantStore {
	t.Helper()
	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)
	cfg := newInMemoryConfig(t)
	if _, err := ts.AddTenant(context.Background(), "tenant-b", cfg, ""); err != nil {
		t.Fatalf("add second tenant: %v", err)
	}
	if _, err := ts.AddTenant(context.Background(), "internal-e2e", cfg, ""); err != nil {
		t.Fatalf("add third tenant: %v", err)
	}
	return ts
}

func doHealthGET(ts *TenantStore, token string) (*httptest.ResponseRecorder, map[string]any) {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	ts.multiTenantHealthHandler(w, req)

	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		return w, nil
	}
	return w, body
}

func TestHealth_NoToken_AggregatedOnly(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "admin-secret")
	defer os.Unsetenv("ADMIN_TOKEN")

	ts := newMultiTenantStore(t)

	w, body := doHealthGET(ts, "")
	if w.Code != http.StatusOK {
		t.Fatalf("GET /health without token = %d, want 200 (public)", w.Code)
	}
	if body == nil {
		t.Fatalf("unparsable body: %s", w.Body.String())
	}
	if _, hasTenants := body["tenants"]; hasTenants {
		t.Fatalf("body leaks tenant inventory without token: %v", body)
	}
	count, ok := body["tenants_count"].(float64)
	if !ok || int(count) != 3 {
		t.Fatalf("tenants_count = %v, want 3", body["tenants_count"])
	}
	raw := w.Body.String()
	if strings.Contains(raw, "tenant-b") || strings.Contains(raw, "internal-e2e") {
		t.Fatalf("body contains tenant IDs without token: %s", raw)
	}
}

func TestHealth_NoToken_FailClosed_NoAdminTokenConfigured(t *testing.T) {
	if tok, ok := os.LookupEnv("ADMIN_TOKEN"); ok {
		defer os.Setenv("ADMIN_TOKEN", tok)
	}
	os.Unsetenv("ADMIN_TOKEN")

	ts := newMultiTenantStore(t)

	w, body := doHealthGET(ts, "Bearer anyone")
	if w.Code != http.StatusOK {
		t.Fatalf("GET /health = %d, want 200", w.Code)
	}
	if _, hasTenants := body["tenants"]; hasTenants {
		t.Fatalf("body leaks tenant inventory when ADMIN_TOKEN unset: %v", body)
	}
}

func TestHealth_WrongToken_AggregatedOnly(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "admin-secret")
	defer os.Unsetenv("ADMIN_TOKEN")

	ts := newMultiTenantStore(t)

	w, body := doHealthGET(ts, "wrong-token")
	if w.Code != http.StatusOK {
		t.Fatalf("GET /health with wrong token = %d, want 200", w.Code)
	}
	if _, hasTenants := body["tenants"]; hasTenants {
		t.Fatalf("body leaks tenant inventory with wrong token: %v", body)
	}
}

func TestHealth_AdminToken_FullInventory(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "admin-secret")
	defer os.Unsetenv("ADMIN_TOKEN")

	// Single tenant legacy shape
	single := newTestTenantStore(t)
	addDefaultTenant(t, single)
	_, body := doHealthGET(single, "admin-secret")
	if body["status"] != "ok" {
		t.Fatalf("single healthy tenant status = %v, want ok", body["status"])
	}

	// Multi-tenant: full inventory present
	_, body = doHealthGET(newMultiTenantStore(t), "admin-secret")
	tenants, ok := body["tenants"].([]any)
	if !ok || len(tenants) != 3 {
		t.Fatalf("admin token should see full tenant list, got %v", body["tenants"])
	}
	raw, _ := json.Marshal(body)
	if !strings.Contains(string(raw), "tenant-b") {
		t.Fatalf("admin response missing tenant ID: %s", raw)
	}
}

func TestHealth_NoToken_SingleTenant_NoInventory(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "admin-secret")
	defer os.Unsetenv("ADMIN_TOKEN")

	ts := newTestTenantStore(t)
	addDefaultTenant(t, ts)

	w, body := doHealthGET(ts, "")
	if w.Code != http.StatusOK {
		t.Fatalf("GET /health (single tenant) = %d, want 200", w.Code)
	}
	if body["status"] != "ok" {
		t.Fatalf("single tenant status = %v, want ok", body["status"])
	}
	if _, hasTenants := body["tenants"]; hasTenants {
		t.Fatalf("single-tenant body leaks inventory: %v", body)
	}
}
