package seedgen

import "testing"

func TestIsMemoryDSN(t *testing.T) {
	tests := []struct {
		dsn  string
		want bool
	}{
		{":memory:", true},
		{":memory:?cache=shared", true},
		{"/tmp/test.db", false},
		{"postgres://host/db", false},
		{"", false},
	}
	for _, tc := range tests {
		got := isMemoryDSN(tc.dsn)
		if got != tc.want {
			t.Errorf("isMemoryDSN(%q) = %v, want %v", tc.dsn, got, tc.want)
		}
	}
}

func TestIsAbsolutePath(t *testing.T) {
	tests := []struct {
		dsn  string
		want bool
	}{
		{"/tmp/test.db", true},
		{"/var/data/db.sqlite", true},
		{"postgres://user:pass@host/db", true},
		{"postgresql://user@host/db", true},
		{":memory:", false},
		{"relative/path.db", false},
		{"", false},
	}
	for _, tc := range tests {
		got := isAbsolutePath(tc.dsn)
		if got != tc.want {
			t.Errorf("isAbsolutePath(%q) = %v, want %v", tc.dsn, got, tc.want)
		}
	}
}
