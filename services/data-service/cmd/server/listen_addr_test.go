package main

// Regression tests for listen-address handling (pentest C1):
// native dev binds data-service to loopback via PORT=127.0.0.1:8084 so the
// unauthenticated read surface (/q/*, /mcp/*) is not exposed on all
// interfaces; wildcard binds without an ADMIN_TOKEN must produce a loud
// startup warning (fail-closed documentation), not a silent launch.

import (
	"strings"
	"testing"
)

func TestAddrFromPort(t *testing.T) {
	cases := []struct {
		name string
		port string
		want string
	}{
		{"default", "", ":8084"},
		{"bare port", "8084", ":8084"},
		{"host:port", "127.0.0.1:8084", "127.0.0.1:8084"},
		{"ipv6 loopback", "[::1]:8084", "[::1]:8084"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := addrFromPort(tc.port); got != tc.want {
				t.Fatalf("addrFromPort(%q) = %q, want %q", tc.port, got, tc.want)
			}
		})
	}
}

func TestListenSafetyWarnings(t *testing.T) {
	cases := []struct {
		name       string
		addr       string
		adminToken string
		wantWarn   bool
	}{
		{"loopback no token — native dev boundary, ok", "127.0.0.1:8084", "", false},
		{"ipv6 loopback no token — ok", "[::1]:8084", "", false},
		{"localhost no token — ok", "localhost:8084", "", false},
		{"wildcard with token — ok", ":8084", "secret", false},
		{"non-loopback with token — ok", "0.0.0.0:8084", "secret", false},
		{"wildcard without token — warn", ":8084", "", true},
		{"non-loopback without token — warn", "0.0.0.0:8084", "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			warnings := listenSafetyWarnings(tc.addr, tc.adminToken)
			if got := len(warnings) > 0; got != tc.wantWarn {
				t.Fatalf("listenSafetyWarnings(%q, %q) = %v, want warn=%v", tc.addr, tc.adminToken, warnings, tc.wantWarn)
			}
		})
	}
}

func TestListenSafetyWarningMentionsAdminToken(t *testing.T) {
	warnings := listenSafetyWarnings(":8084", "")
	if len(warnings) == 0 {
		t.Fatal("expected a warning for wildcard bind without ADMIN_TOKEN")
	}
	if !strings.Contains(warnings[0], "ADMIN_TOKEN") {
		t.Fatalf("warning should mention ADMIN_TOKEN, got: %q", warnings[0])
	}
	if !strings.Contains(warnings[0], "/q") || !strings.Contains(warnings[0], "/mcp") {
		t.Fatalf("warning should name the exposed read surface, got: %q", warnings[0])
	}
}
