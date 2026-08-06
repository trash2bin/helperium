package cors

import (
	"os"
	"testing"
)

// clearCORS unsets CORS_ALLOW_ORIGINS and returns a restore func.
func clearCORS() func() {
	prev, ok := os.LookupEnv("CORS_ALLOW_ORIGINS")
	os.Unsetenv("CORS_ALLOW_ORIGINS")
	if ok {
		return func() { os.Setenv("CORS_ALLOW_ORIGINS", prev) }
	}
	return func() {}
}

// withCORS sets CORS_ALLOW_ORIGINS and returns a restore func.
func withCORS(val string) func() {
	prev, ok := os.LookupEnv("CORS_ALLOW_ORIGINS")
	os.Setenv("CORS_ALLOW_ORIGINS", val)
	if ok {
		return func() { os.Setenv("CORS_ALLOW_ORIGINS", prev) }
	}
	return func() { os.Unsetenv("CORS_ALLOW_ORIGINS") }
}

func TestAllowOrigin_Default(t *testing.T) {
	defer clearCORS()()
	got := AllowOrigin()
	if got != "*" {
		t.Errorf("AllowOrigin() = %q, want %q", got, "*")
	}
}

func TestAllowOrigin_Custom(t *testing.T) {
	defer withCORS("example.com")()
	got := AllowOrigin()
	if got != "example.com" {
		t.Errorf("AllowOrigin() = %q, want %q", got, "example.com")
	}
}

func TestAllowOrigin_Empty(t *testing.T) {
	defer withCORS("")()
	got := AllowOrigin()
	if got != "*" {
		t.Errorf("AllowOrigin() = %q, want %q", got, "*")
	}
}
