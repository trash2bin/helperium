package server

import "testing"

// ── computeOverallStatus ──

func TestComputeOverallStatus(t *testing.T) {
	tests := []struct {
		name   string
		health []TenantHealth
		want   string
	}{
		{"empty", []TenantHealth{}, "unhealthy"},
		{"all healthy", []TenantHealth{{Status: "healthy"}, {Status: "healthy"}}, "healthy"},
		{"all unhealthy", []TenantHealth{{Status: "unhealthy", Error: "err"}}, "unhealthy"},
		{"mixed", []TenantHealth{{Status: "healthy"}, {Status: "unhealthy", Error: "err"}}, "degraded"},
		{"single healthy", []TenantHealth{{Status: "healthy"}}, "healthy"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := computeOverallStatus(tc.health)
			if got != tc.want {
				t.Errorf("computeOverallStatus(%+v) = %q, want %q", tc.health, got, tc.want)
			}
		})
	}
}
