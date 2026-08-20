package server

import "testing"

func TestDefaultAbuseConfigUsesEnvironment(t *testing.T) {
	t.Setenv("ABUSE_RPS", "2.5")
	t.Setenv("ABUSE_BURST", "8")
	t.Setenv("ABUSE_MAX_MSG_LENGTH", "1234")
	t.Setenv("ABUSE_MIN_INTERVAL_MS", "333")
	t.Setenv("ABUSE_MAX_USER_TURNS", "22")
	t.Setenv("ABUSE_EMERGENCY_MODE", "true")
	t.Setenv("ABUSE_EMERGENCY_PRESET", "cautious")
	t.Setenv("DEMO_HISTORY_TURNS", "9")
	t.Setenv("DEMO_HISTORY_CONTENT_CHARS", "7000")
	t.Setenv("AGENT_MAX_ITERATIONS", "6")
	t.Setenv("AGENT_MAX_EMPTY_ROUNDS", "4")
	t.Setenv("AGENT_MAX_TURN_TOKENS", "9000")
	t.Setenv("SESSION_TTL_HOURS", "24")

	cfg := DefaultAbuseConfig()
	if cfg.RPS != 2.5 || cfg.Burst != 8 || cfg.MaxMessageLength != 1234 || cfg.MinIntervalMs != 333 || cfg.MaxUserTurnsPerSession != 22 || !cfg.EmergencyMode || cfg.EmergencyPreset != "cautious" || cfg.HistoryTurns != 9 || cfg.HistoryContentChars != 7000 || cfg.MaxIterations != 6 || cfg.MaxEmptyRounds != 4 || cfg.MaxTurnTokens != 9000 || cfg.SessionTTLHours != 24 {
		t.Fatalf("DefaultAbuseConfig() = %+v, values from environment were not preserved", cfg)
	}
}

func TestDefaultAbuseConfigRejectsInvalidEnvironment(t *testing.T) {
	t.Setenv("ABUSE_RPS", "invalid")
	t.Setenv("ABUSE_BURST", "2.5")
	t.Setenv("ABUSE_EMERGENCY_MODE", "invalid")

	cfg := DefaultAbuseConfig()
	if cfg.RPS != 1.0 || cfg.Burst != 5 || cfg.EmergencyMode {
		t.Fatalf("DefaultAbuseConfig() = %+v, invalid values must use defaults", cfg)
	}
}
