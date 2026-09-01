package server

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// tenantUploadSQLiteHandler must enforce the repo-wide tenant-ID contract
// (AGENTS.md "MCP scope") and a strict upload extension allow-list before any
// filepath math: a crafted tenant_id (../ traversal) or filename extension
// must never escape the upload data directory.

func newUploadTestServer(t *testing.T) *Server {
	t.Helper()
	return New(Options{
		Addr:        ":0",
		DataSvcURL:  "http://127.0.0.1:1", // invalid upstream: valid uploads never reach it in these tests
		AdminToken:  "admin-secret",
		ViewerToken: "viewer-secret",
		DataDir:     t.TempDir(),
	})
}

func uploadSQLiteRequest(t *testing.T, s *Server, tenantID, filename string) *httptest.ResponseRecorder {
	t.Helper()

	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	if err := writer.WriteField("tenant_id", tenantID); err != nil {
		t.Fatalf("write tenant_id field: %v", err)
	}
	// Minimal SQLite header so an accepted upload would at least look like a DB.
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		t.Fatalf("create form file: %v", err)
	}
	if _, err := part.Write([]byte("SQLite format 3\x00")); err != nil {
		t.Fatalf("write file part: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close multipart writer: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/tenants/upload-sqlite", &buf)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("Authorization", "Bearer admin-secret")

	w := httptest.NewRecorder()
	s.Router().ServeHTTP(w, req)
	return w
}

func TestTenantUploadSQLiteRejectsInvalidTenantIDs(t *testing.T) {
	s := newUploadTestServer(t)

	invalid := []struct {
		name     string
		tenantID string
	}{
		{name: "path traversal", tenantID: "../evil"},
		{name: "nested path", tenantID: "a/b"},
		{name: "backslash", tenantID: `a\b`},
		{name: "hidden file style", tenantID: ".hidden"},
		{name: "dot inside", tenantID: "ten.ant"},
		{name: "too long 129", tenantID: strings.Repeat("a", 129)},
		{name: "empty-ish space", tenantID: " "},
	}

	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			w := uploadSQLiteRequest(t, s, tc.tenantID, "shop.db")
			if w.Code != http.StatusBadRequest {
				t.Fatalf("tenant_id %q: status = %d, want 400; body=%s", tc.tenantID, w.Code, w.Body.String())
			}
			body := w.Body.String()
			if !strings.Contains(body, "invalid_tenant_id") {
				t.Fatalf("tenant_id %q: want error code invalid_tenant_id, body=%s", tc.tenantID, body)
			}
		})
	}
}

func TestTenantUploadSQLiteAcceptsValidTenantID(t *testing.T) {
	s := newUploadTestServer(t)

	w := uploadSQLiteRequest(t, s, "tenant-1", "shop.db")
	// A valid tenant ID must pass handler-side validation. The upstream
	// data-service is unreachable by design, so the expected terminal state is
	// a 502 upstream_error — NOT a 400 validation failure.
	if w.Code == http.StatusBadRequest {
		t.Fatalf("valid tenant id rejected: status=%d body=%s", w.Code, w.Body.String())
	}
	if w.Code != http.StatusBadGateway {
		t.Fatalf("valid tenant id: status = %d, want 502 upstream_error; body=%s", w.Code, w.Body.String())
	}
	if body := w.Body.String(); !strings.Contains(body, "upstream_error") {
		t.Fatalf("valid tenant id: want upstream_error, body=%s", body)
	}
}

func TestTenantUploadSQLiteRejectsBadExtensions(t *testing.T) {
	s := newUploadTestServer(t)

	invalid := []struct {
		name     string
		filename string
	}{
		{name: "no extension", filename: "shop"},
		{name: "not sqlite", filename: "notes.txt"},
		{name: "tarball", filename: "dump.tar.gz"},
		{name: "db-like double ext", filename: "shop.db.bak"},
	}

	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			w := uploadSQLiteRequest(t, s, "tenant-1", tc.filename)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("filename %q: status = %d, want 400; body=%s", tc.filename, w.Code, w.Body.String())
			}
			body := w.Body.String()
			if !strings.Contains(body, "invalid_extension") {
				t.Fatalf("filename %q: want error code invalid_extension, body=%s", tc.filename, body)
			}
		})
	}
}

// Traversal sequences in the uploaded filename must never reach savePath.
// filepath.Base sanitizes them, and only the sanitized extension survives the
// allow-list, so the upload is accepted (and stored under the validated
// tenant ID) — asserted here as 502 upstream_error, never a filesystem write
// outside the data directory.
func TestTenantUploadSQLiteSanitizesFilenameTraversal(t *testing.T) {
	s := newUploadTestServer(t)

	for _, filename := range []string{"shop.db/../../evil.db", "../../evil.db", "..\\..\\evil.db"} {
		t.Run(filename, func(t *testing.T) {
			w := uploadSQLiteRequest(t, s, "tenant-1", filename)
			if w.Code != http.StatusBadGateway {
				t.Fatalf("filename %q: status = %d, want 502 (sanitized, validation passed); body=%s", filename, w.Code, w.Body.String())
			}
		})
	}
}

func TestTenantUploadSQLiteAcceptsAllowedExtensions(t *testing.T) {
	s := newUploadTestServer(t)

	for _, filename := range []string{"shop.db", "shop.sqlite", "shop.sqlite3", "weird name.sqlite"} {
		t.Run(filename, func(t *testing.T) {
			w := uploadSQLiteRequest(t, s, "tenant-1", filename)
			// Validation must pass; unreachable upstream turns this into 502.
			if w.Code != http.StatusBadGateway {
				t.Fatalf("filename %q: status = %d, want 502 (validation passed); body=%s", filename, w.Code, w.Body.String())
			}
		})
	}
}
