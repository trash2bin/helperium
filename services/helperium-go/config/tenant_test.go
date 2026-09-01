package config

import (
	"strings"
	"testing"
)

// TestValidTenantID pins the repo-wide tenant-ID contract
// (AGENTS.md "MCP scope"): [A-Za-z0-9][A-Za-z0-9_-]{0,127}.
func TestValidTenantID(t *testing.T) {
	long128 := strings.Repeat("a", 128)
	long129 := strings.Repeat("a", 129)

	tests := []struct {
		name string
		id   string
		want bool
	}{
		{name: "simple alphanumeric", id: "tenant1", want: true},
		{name: "dash underscore digits", id: "tenant-1_a2", want: true},
		{name: "single char", id: "a", want: true},
		{name: "max length 128", id: long128, want: true},
		{name: "empty", id: "", want: false},
		{name: "path traversal", id: "../evil", want: false},
		{name: "slash", id: "a/b", want: false},
		{name: "backslash", id: `a\b`, want: false},
		{name: "leading dot", id: ".hidden", want: false},
		{name: "dot inside", id: "ten.ant", want: false},
		{name: "spaces", id: "tenant 1", want: false},
		{name: "leading dash", id: "-tenant", want: false},
		{name: "leading underscore", id: "_tenant", want: false},
		{name: "newline injection", id: "tenant\n1", want: false},
		{name: "too long 129", id: long129, want: false},
		{name: "unicode", id: "tenant-é", want: false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ValidTenantID(tt.id); got != tt.want {
				t.Errorf("ValidTenantID(%q) = %v, want %v", tt.id, got, tt.want)
			}
		})
	}
}
