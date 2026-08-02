package search

import "testing"

func TestParseOffset_Capped(t *testing.T) {
	tests := []struct {
		name  string
		query map[string][]string
		want  int
	}{
		{
			name:  "no offset returns 0",
			query: map[string][]string{},
			want:  0,
		},
		{
			name:  "regular offset",
			query: map[string][]string{"offset": {"50"}},
			want:  50,
		},
		{
			name:  "huge offset capped at 100000",
			query: map[string][]string{"offset": {"99999999"}},
			want:  100000,
		},
		{
			name:  "exactly at cap",
			query: map[string][]string{"offset": {"100000"}},
			want:  100000,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseOffset(tt.query)
			if got != tt.want {
				t.Errorf("parseOffset(%v) = %d, want %d", tt.query, got, tt.want)
			}
		})
	}
}
