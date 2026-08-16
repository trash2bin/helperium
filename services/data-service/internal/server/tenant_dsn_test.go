package server

import "testing"

func TestResolveDataSourceDSN(t *testing.T) {
	configPath := "/srv/tenants/demo.json"
	cases := map[string]string{
		"data.db":                    "/srv/tenants/data.db",
		"file:data.db?mode=ro":       "file:/srv/tenants/data.db?mode=ro",
		"file:/data/demo.db?mode=ro": "file:/data/demo.db?mode=ro",
		"postgres://db/demo":         "postgres://db/demo",
	}
	for input, want := range cases {
		if got := resolveDataSourceDSN(input, configPath); got != want {
			t.Errorf("resolveDataSourceDSN(%q) = %q, want %q", input, got, want)
		}
	}
}
