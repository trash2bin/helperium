// reports_test.go — widget problem report proxy handlers.
//
// The dashboard only proxies: GET /api/reports → api-service /admin/reports,
// POST /api/reports/{id}/status → api-service /admin/reports/{id}/status.
// These tests pin the upstream path (contract), the bearer forwarding and the
// fail-closed behaviour when the API control-plane bearer is unconfigured.
package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestReportsListProxiesToAdminReports(t *testing.T) {
	var upstreamPath, upstreamQuery, upstreamAuthorization, upstreamMethod string
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamPath = r.URL.Path
		upstreamQuery = r.URL.RawQuery
		upstreamMethod = r.Method
		upstreamAuthorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"reports": [], "total": 0}`))
	}))
	defer api.Close()

	s := New(Options{
		Addr:           ":0",
		ApiSvcURL:      api.URL,
		ApiBearerToken: "api-control-secret",
		AdminToken:     "dashboard-admin-secret",
	})
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/reports?limit=25&status=new", nil)
	req.Header.Set("Authorization", "Bearer dashboard-admin-secret")
	s.Router().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("proxy status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if upstreamMethod != http.MethodGet || upstreamPath != "/admin/reports" {
		t.Fatalf("upstream = %s %s, want GET /admin/reports", upstreamMethod, upstreamPath)
	}
	if upstreamQuery != "limit=25&status=new" {
		t.Fatalf("upstream query = %q, want limit=25&status=new", upstreamQuery)
	}
	if upstreamAuthorization != "Bearer api-control-secret" {
		t.Fatalf("upstream Authorization = %q, want configured API bearer", upstreamAuthorization)
	}
	body, _ := io.ReadAll(w.Body)
	if !strings.Contains(string(body), `"total": 0`) {
		t.Fatalf("upstream body not forwarded: %s", string(body))
	}
}

func TestReportStatusPostProxiesBody(t *testing.T) {
	var upstreamPath, upstreamBody string
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamPath = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		upstreamBody = string(raw)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id": "r-1", "status": "reviewed"}`))
	}))
	defer api.Close()

	s := New(Options{
		Addr:           ":0",
		ApiSvcURL:      api.URL,
		ApiBearerToken: "api-control-secret",
		AdminToken:     "dashboard-admin-secret",
	})
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/reports/r-1/status", strings.NewReader(`{"status":"reviewed"}`))
	req.Header.Set("Authorization", "Bearer dashboard-admin-secret")
	req.Header.Set("Content-Type", "application/json")
	s.Router().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("proxy status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if upstreamPath != "/admin/reports/r-1/status" {
		t.Fatalf("upstream path = %q, want /admin/reports/r-1/status", upstreamPath)
	}
	if upstreamBody != `{"status":"reviewed"}` {
		t.Fatalf("upstream body = %q, want forwarded JSON", upstreamBody)
	}
}

func TestReportsProxyFailsClosedWithoutAPIControlPlaneBearer(t *testing.T) {
	s := New(Options{
		Addr:       ":0",
		ApiSvcURL:  "http://127.0.0.1:1",
		AdminToken: "dashboard-admin-secret",
	})

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/reports", nil)
	req.Header.Set("Authorization", "Bearer dashboard-admin-secret")
	s.Router().ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("proxy status = %d, want 503; body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "api_auth_unconfigured") {
		t.Fatalf("missing api_auth_unconfigured error: %s", w.Body.String())
	}
}

func TestReportStatusForbiddenForViewer(t *testing.T) {
	s := New(Options{
		Addr:        ":0",
		ApiSvcURL:   "http://127.0.0.1:1",
		AdminToken:  "dashboard-admin-secret",
		ViewerToken: "dashboard-viewer-secret",
	})

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/reports/r-1/status", strings.NewReader(`{"status":"reviewed"}`))
	req.Header.Set("Authorization", "Bearer dashboard-viewer-secret")
	s.Router().ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("viewer POST status = %d, want 403; body=%s", w.Code, w.Body.String())
	}
}
