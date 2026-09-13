package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// Regression (authorized pentest 2026-09-13): corsMiddleware wrote the raw
// CORS_ALLOW_ORIGINS env value into Access-Control-Allow-Origin. With a
// multi-origin allow-list the header becomes a comma-joined list — invalid
// per the CORS spec (browsers reject the response), so even a legitimate
// allow-listed origin cannot work cross-origin. The middleware must reflect
// exactly the single allow-listed Origin of the request and emit no ACAO for
// unlisted origins.
func TestCORSReflectsSingleMatchingOrigin(t *testing.T) {
	defer withCORS("http://localhost:8080,http://127.0.0.1:8000")()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	req := httptest.NewRequest(http.MethodOptions, "/api/dashboard", nil)
	req.Header.Set("Origin", "http://localhost:8080")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	acao := w.Header().Get("Access-Control-Allow-Origin")
	if acao != "http://localhost:8080" {
		t.Fatalf("expected the single matching origin in Access-Control-Allow-Origin, got %q", acao)
	}
}

func TestCORSPreflightFromUnlistedOriginGetsNoACAO(t *testing.T) {
	defer withCORS("http://localhost:8080,http://127.0.0.1:8000")()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	req := httptest.NewRequest(http.MethodOptions, "/api/dashboard", nil)
	req.Header.Set("Origin", "https://evil.example")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if acao := w.Header().Get("Access-Control-Allow-Origin"); acao != "" {
		t.Fatalf("unlisted origin must not receive Access-Control-Allow-Origin, got %q", acao)
	}
}
