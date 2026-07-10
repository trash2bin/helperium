package server

import (
	"os"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
)

func TestInitLogger(t *testing.T) {
	t.Setenv("DS_LOG_LEVEL", "debug")
	InitLogger()
	// If no panic, it works. Read-only check.
}

func TestInitLogger_Default(t *testing.T) {
	os.Unsetenv("DS_LOG_LEVEL")
	InitLogger()
}

func TestResolveRequestTimeout_Env(t *testing.T) {
	t.Setenv("DS_REQUEST_TIMEOUT", "99")
	cfg := &config.Config{}
	got := ResolveRequestTimeout(cfg)
	if got != 99 {
		t.Errorf("ResolveRequestTimeout = %d, want 99", got)
	}
}

func TestResolveRequestTimeout_FromConfig(t *testing.T) {
	os.Unsetenv("DS_REQUEST_TIMEOUT")
	timeout := 45
	cfg := &config.Config{Server: &config.ServerConfig{RequestTimeoutSeconds: &timeout}}
	got := ResolveRequestTimeout(cfg)
	if got != 45 {
		t.Errorf("ResolveRequestTimeout = %d, want 45", got)
	}
}

func TestResolveRequestTimeout_Default(t *testing.T) {
	os.Unsetenv("DS_REQUEST_TIMEOUT")
	cfg := &config.Config{}
	got := ResolveRequestTimeout(cfg)
	if got != 30 {
		t.Errorf("ResolveRequestTimeout = %d, want 30 (default)", got)
	}
}

func TestResolveBodyLimit_Env(t *testing.T) {
	t.Setenv("DS_BODY_LIMIT_MB", "5")
	cfg := &config.Config{}
	got := ResolveBodyLimit(cfg)
	if got != 5<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 5<<20)
	}
}

func TestResolveBodyLimit_FromConfig(t *testing.T) {
	os.Unsetenv("DS_BODY_LIMIT_MB")
	mb := 20
	cfg := &config.Config{Server: &config.ServerConfig{BodyLimitMB: &mb}}
	got := ResolveBodyLimit(cfg)
	if got != 20<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 20<<20)
	}
}

func TestResolveBodyLimit_Default(t *testing.T) {
	os.Unsetenv("DS_BODY_LIMIT_MB")
	cfg := &config.Config{}
	got := ResolveBodyLimit(cfg)
	if got != 10<<20 {
		t.Errorf("ResolveBodyLimit = %d, want %d", got, 10<<20)
	}
}

func TestResolveMaxConcurrent_Env(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "250")
	cfg := &config.Config{}
	got := ResolveMaxConcurrent(cfg)
	if got != 250 {
		t.Errorf("ResolveMaxConcurrent = %d, want 250", got)
	}
}

func TestResolveMaxConcurrent_FromConfig(t *testing.T) {
	os.Unsetenv("DS_MAX_CONCURRENT")
	mc := 50
	cfg := &config.Config{Server: &config.ServerConfig{MaxConcurrent: &mc}}
	got := ResolveMaxConcurrent(cfg)
	if got != 50 {
		t.Errorf("ResolveMaxConcurrent = %d, want 50", got)
	}
}

func TestResolveMaxConcurrent_Default(t *testing.T) {
	os.Unsetenv("DS_MAX_CONCURRENT")
	cfg := &config.Config{}
	got := ResolveMaxConcurrent(cfg)
	if got != 100 {
		t.Errorf("ResolveMaxConcurrent = %d, want 100 (default)", got)
	}
}

func TestResolveIntEnv_Invalid(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "notanumber")
	got := resolveIntEnv("DS_MAX_CONCURRENT", 0, 50)
	if got != 50 {
		t.Errorf("resolveIntEnv with invalid value = %d, want 50 (default)", got)
	}
}

func TestResolveIntEnv_Negative(t *testing.T) {
	t.Setenv("DS_MAX_CONCURRENT", "-5")
	got := resolveIntEnv("DS_MAX_CONCURRENT", 10, 50)
	if got != 10 {
		t.Errorf("resolveIntEnv with negative = %d, want 10 (fallback)", got)
	}
}

func TestConfigValue_Nil(t *testing.T) {
	got := configValue(nil, func(c *config.Config) *int { return nil })
	if got != 0 {
		t.Errorf("configValue(nil) = %d, want 0", got)
	}
}

func TestConfigValue_NilServer(t *testing.T) {
	cfg := &config.Config{Server: nil}
	got := configValue(cfg, func(c *config.Config) *int {
		if c.Server != nil {
			return c.Server.MaxConcurrent
		}
		return nil
	})
	if got != 0 {
		t.Errorf("configValue(nil server) = %d, want 0", got)
	}
}
