package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// C1: GET /admin/config must not carry the plaintext readonly_dsn. The DSN is
// a secret; the empty-means-preserve merge in adminConfigUpdateHandler keeps
// the admin GET-then-PUT round-trip working without echoing it back. Only
// has_readonly_dsn is exposed.
func TestAdminConfigGet_RedactsReadonlyDSN(t *testing.T) {
	ts := newTenantAdminTestStore(t)

	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	const secretDSN = "postgres://readonly:s3cret@db.internal:5432/shop"
	inst.Config.DataSource.ReadonlyDSN = secretDSN

	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	req.Header.Set("X-Tenant-ID", "test-tenant")
	rec := httptest.NewRecorder()
	ts.adminConfigHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET: expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal GET: %v", err)
	}
	if strings.Contains(rec.Body.String(), secretDSN) {
		t.Errorf("GET /admin/config leaked the plaintext readonly_dsn")
	}
	ds, _ := resp["data_source"].(map[string]any)
	if ds == nil {
		t.Fatal("data_source missing in GET response")
	}
	if _, present := ds["readonly_dsn"]; present {
		t.Errorf("data_source.readonly_dsn must not be serialized in GET responses, got %v", ds["readonly_dsn"])
	}
	if ds["has_readonly_dsn"] != true {
		t.Errorf("has_readonly_dsn = %v, want true", ds["has_readonly_dsn"])
	}
}

// The GET-then-PUT round-trip must keep the stored DSN even though GET no
// longer returns it: PUT with an empty readonly_dsn preserves the secret.
func TestAdminConfigRoundTrip_PreservesDSNWithoutEcho(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	inst.Config.DataSource.ReadonlyDSN = "file:roundtrip-readonly.db"

	// GET (secret not echoed)
	getReq := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	getReq.Header.Set("X-Tenant-ID", "test-tenant")
	getRec := httptest.NewRecorder()
	ts.adminConfigHandler(getRec, getReq)
	if getRec.Code != http.StatusOK {
		t.Fatalf("GET: expected 200, got %d", getRec.Code)
	}

	// PUT the same body shape a dashboard would send after a GET
	var cfg map[string]any
	if err := json.Unmarshal(getRec.Body.Bytes(), &cfg); err != nil {
		t.Fatalf("unmarshal GET body: %v", err)
	}
	ds := cfg["data_source"].(map[string]any)
	ds["readonly_dsn"] = "" // dashboards send back whatever GET gave them
	raw, _ := json.Marshal(cfg)
	putReq := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader(string(raw)))
	putReq.Header.Set("X-Tenant-ID", "test-tenant")
	putRec := httptest.NewRecorder()
	ts.adminConfigUpdateHandler(putRec, putReq)
	if putRec.Code != http.StatusOK {
		t.Fatalf("PUT: expected 200, got %d: %s", putRec.Code, putRec.Body.String())
	}

	after, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant gone after PUT")
	}
	if after.Config.DataSource.ReadonlyDSN != "file:roundtrip-readonly.db" {
		t.Errorf("readonly_dsn lost on round-trip PUT: got %q", after.Config.DataSource.ReadonlyDSN)
	}
}
