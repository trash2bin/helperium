package seedgen

import "testing"

func TestSQLitePlaceholder(t *testing.T) {
	for i := 1; i <= 5; i++ {
		got := SQLitePlaceholder(i)
		if got != "?" {
			t.Errorf("SQLitePlaceholder(%d) = %q, want '?'", i, got)
		}
	}
}

func TestPostgresPlaceholder(t *testing.T) {
	tests := []struct{ idx int; want string }{
		{1, "$1"}, {2, "$2"}, {3, "$3"}, {42, "$42"},
	}
	for _, tc := range tests {
		got := PostgresPlaceholder(tc.idx)
		if got != tc.want {
			t.Errorf("PostgresPlaceholder(%d) = %q, want %q", tc.idx, got, tc.want)
		}
	}
}
