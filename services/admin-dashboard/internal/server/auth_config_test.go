package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestValidateAuthTokens(t *testing.T) {
	cases := []struct {
		name, admin, viewer string
		wantErr             bool
	}{
		{"distinct", "admin-secret", "viewer-secret", false},
		{"admin only", "admin-secret", "", false},
		{"viewer only", "", "viewer-secret", false},
		{"unset", "", "", false},
		{"equal", "shared-secret", "shared-secret", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ValidateAuthTokens(tc.admin, tc.viewer); (got != nil) != tc.wantErr {
				t.Errorf("validation error = %v, wantErr %v", got, tc.wantErr)
			}
		})
	}
}

func TestAuthMiddlewareRejectsEqualTokens(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusTeapot) })
	handler := authMiddleware("shared-secret", "shared-secret")(next)
	req := httptest.NewRequest(http.MethodPost, "/api/agents", nil)
	req.Header.Set("Authorization", "Bearer shared-secret")
	res := httptest.NewRecorder()
	handler.ServeHTTP(res, req)
	if res.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want %d", res.Code, http.StatusInternalServerError)
	}
}
