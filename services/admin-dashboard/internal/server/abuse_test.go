package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/go-chi/chi/v5"
)

// ════════════════════════════════════════════════════════════════
// AbuseConfig store tests
// ════════════════════════════════════════════════════════════════

func TestAbuseStore_Defaults(t *testing.T) {
	dir := t.TempDir()
	store := NewAbuseStore(dir)
	cfg := store.Get()

	if cfg.RPS != 1.0 {
		t.Errorf("default rps = %f, want 1.0", cfg.RPS)
	}
	if cfg.Burst != 5 {
		t.Errorf("default burst = %d, want 5", cfg.Burst)
	}
	if cfg.MaxMessageLength != 2000 {
		t.Errorf("default maxMessageLength = %d, want 2000", cfg.MaxMessageLength)
	}
	if cfg.MinIntervalMs != 1000 {
		t.Errorf("default minIntervalMs = %d, want 1000", cfg.MinIntervalMs)
	}
	if cfg.MaxMessagesPerSession != 50 {
		t.Errorf("default maxMessagesPerSession = %d, want 50", cfg.MaxMessagesPerSession)
	}
	if !cfg.BlockEmptyUserAgent {
		t.Error("default BlockEmptyUserAgent should be true")
	}
	if len(cfg.BlockedUserAgents) == 0 {
		t.Error("default BlockedUserAgents should not be empty")
	}
}

func TestAbuseStore_Persists(t *testing.T) {
	dir := t.TempDir()
	store := NewAbuseStore(dir)

	cfg := store.Get()
	cfg.RPS = 5.0
	cfg.Burst = 10
	cfg.MaxMessageLength = 500
	cfg.BlockEmptyUserAgent = false
	cfg.BlockedUserAgents = []string{"curl/*"}

	if err := store.Set(cfg); err != nil {
		t.Fatalf("Set: %v", err)
	}

	// Verify file exists
	filePath := filepath.Join(dir, "abuse_config.json")
	if _, err := os.Stat(filePath); os.IsNotExist(err) {
		t.Fatalf("abuse_config.json not created at %s", filePath)
	}

	// Create new store instance that reads from file
	store2 := NewAbuseStore(dir)
	cfg2 := store2.Get()

	if cfg2.RPS != 5.0 {
		t.Errorf("persisted rps = %f, want 5.0", cfg2.RPS)
	}
	if cfg2.Burst != 10 {
		t.Errorf("persisted burst = %d, want 10", cfg2.Burst)
	}
	if cfg2.MaxMessageLength != 500 {
		t.Errorf("persisted maxMessageLength = %d, want 500", cfg2.MaxMessageLength)
	}
	if cfg2.BlockEmptyUserAgent {
		t.Error("persisted BlockEmptyUserAgent should be false")
	}
	if len(cfg2.BlockedUserAgents) != 1 || cfg2.BlockedUserAgents[0] != "curl/*" {
		t.Errorf("persisted BlockedUserAgents = %v, want [curl/*]", cfg2.BlockedUserAgents)
	}
}

func TestAbuseStore_MissingFileDefaults(t *testing.T) {
	dir := t.TempDir()
	_ = os.Remove(filepath.Join(dir, "abuse_config.json"))

	store := NewAbuseStore(dir)
	cfg := store.Get()

	if cfg.RPS != 1.0 {
		t.Errorf("default rps = %f, want 1.0", cfg.RPS)
	}
}

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════

// newTestAbuseServer creates a test server with a temp data directory and a test token.
func newTestAbuseServer(t *testing.T) (chi.Router, func()) {
	t.Helper()
	dir := t.TempDir()
	s := New(Options{Addr: ":0", DataDir: dir, AdminToken: "test-token"})
	return s.Router(), func() { os.RemoveAll(dir) }
}

// ════════════════════════════════════════════════════════════════
// Abuse API endpoint tests
// ════════════════════════════════════════════════════════════════

func TestAbuseSettingsGet_ReturnsDefaults(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/abuse-settings", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /api/abuse-settings = %d, want 200\nbody: %s", w.Code, w.Body.String())
	}

	var cfg AbuseConfig
	if err := json.NewDecoder(w.Body).Decode(&cfg); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if cfg.RPS != 1.0 {
		t.Errorf("rps = %f, want 1.0", cfg.RPS)
	}
	if cfg.MaxMessageLength != 2000 {
		t.Errorf("maxMessageLength = %d, want 2000", cfg.MaxMessageLength)
	}
}

func TestAbuseSettingsPut_UpdatesAndReturns(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	payload := AbuseConfig{
		RPS:                   2.5,
		Burst:                 8,
		MaxMessageLength:      1000,
		MinIntervalMs:         500,
		MaxMessagesPerSession: 30,
		BlockEmptyUserAgent:   false,
		BlockedUserAgents:     []string{"bot/*"},
	}
	body, _ := json.Marshal(payload)

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/abuse-settings", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer test-token")
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("PUT /api/abuse-settings = %d, want 200\nbody: %s", w.Code, w.Body.String())
	}

	var resp AbuseConfig
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if resp.RPS != 2.5 {
		t.Errorf("rps = %f, want 2.5", resp.RPS)
	}
	if resp.Burst != 8 {
		t.Errorf("burst = %d, want 8", resp.Burst)
	}
	if resp.MaxMessageLength != 1000 {
		t.Errorf("maxMessageLength = %d, want 1000", resp.MaxMessageLength)
	}
}

func TestAbuseSettingsPut_AppliesDefaultsForZeroValues(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	payload := AbuseConfig{}
	body, _ := json.Marshal(payload)

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/abuse-settings", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer test-token")
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("PUT /api/abuse-settings = %d, want 200", w.Code)
	}

	var resp AbuseConfig
	_ = json.NewDecoder(w.Body).Decode(&resp)

	if resp.MaxMessageLength != 2000 {
		t.Errorf("default maxMessageLength = %d, want 2000", resp.MaxMessageLength)
	}
	if resp.RPS != 1.0 {
		t.Errorf("default rps = %f, want 1.0", resp.RPS)
	}
}

// ════════════════════════════════════════════════════════════════
// Abuse API — authenticated endpoint tests
// ════════════════════════════════════════════════════════════════

func TestAbuseSettingsGet_WithAuth(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/abuse-settings", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /api/abuse-settings with auth = %d, want 200\nbody: %s", w.Code, w.Body.String())
	}
}

func TestAbuseSettingsGet_WithoutAuth_ReturnsError(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/abuse-settings", nil)
	// No Authorization header
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("GET /api/abuse-settings without auth = %d, want 401", w.Code)
	}
}

// ════════════════════════════════════════════════════════════════
// Emergency preset tests
// ════════════════════════════════════════════════════════════════

func TestEmergencyPresets_ValidPresets(t *testing.T) {
	presets := EmergencyPresets()

	for name, cfg := range presets {
		t.Run(name, func(t *testing.T) {
			if cfg.EmergencyPreset != name {
				t.Errorf("preset %q has EmergencyPreset=%q", name, cfg.EmergencyPreset)
			}
			if cfg.RPS <= 0 {
				t.Errorf("preset %q: RPS=%f", name, cfg.RPS)
			}
			if cfg.Burst <= 0 {
				t.Errorf("preset %q: Burst=%d", name, cfg.Burst)
			}
		})
	}
}

func TestEmergencyPreset_NormalHasMaxSettings(t *testing.T) {
	presets := EmergencyPresets()
	normal := presets["normal"]
	lockdown := presets["lockdown"]

	// Normal should be more permissive than lockdown
	if normal.RPS <= lockdown.RPS {
		t.Errorf("normal RPS (%f) should be > lockdown RPS (%f)", normal.RPS, lockdown.RPS)
	}
	if normal.Burst <= lockdown.Burst {
		t.Errorf("normal burst (%d) should be > lockdown burst (%d)", normal.Burst, lockdown.Burst)
	}
	if normal.MaxMessagesPerSession <= lockdown.MaxMessagesPerSession {
		t.Errorf("normal maxMessages (%d) should be > lockdown maxMessages (%d)",
			normal.MaxMessagesPerSession, lockdown.MaxMessagesPerSession)
	}
}

func TestEmergencyPreset_CautiousHasTokenBudget(t *testing.T) {
	presets := EmergencyPresets()
	cautious := presets["cautious"]

	if cautious.TokenBudget == 0 {
		t.Error("cautious preset should have TokenBudget > 0")
	}
	// CheapModel removed — use provider_priority fallback via LiteLLM Router instead
	_ = cautious // placeholder
}

func TestAbusePreset_ApplyLockdown(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/abuse-preset/lockdown", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("POST /api/abuse-preset/lockdown = %d, want 200\nbody: %s", w.Code, w.Body.String())
	}

	var cfg AbuseConfig
	if err := json.NewDecoder(w.Body).Decode(&cfg); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if !cfg.EmergencyMode {
		t.Error("lockdown preset should set EmergencyMode=true")
	}
	if cfg.EmergencyPreset != "lockdown" {
		t.Errorf("EmergencyPreset=%q, want lockdown", cfg.EmergencyPreset)
	}
	if cfg.Burst > 2 {
		t.Errorf("lockdown burst=%d, want <=2", cfg.Burst)
	}
	if cfg.RPS > 0.5 {
		t.Errorf("lockdown rps=%f, want <=0.5", cfg.RPS)
	}
	if cfg.TokenBudget == 0 {
		t.Error("lockdown should have TokenBudget > 0")
	}
}

func TestAbusePreset_InvalidPreset_Returns400(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/abuse-preset/nonexistent", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("invalid preset = %d, want 400", w.Code)
	}
}

func TestEmergencyStatus_ReturnsCurrentState(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/emergency-status", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /api/emergency-status = %d, want 200", w.Code)
	}

	var resp map[string]any
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if resp["emergency_preset"] != "normal" {
		t.Errorf("emergency_preset=%v, want normal", resp["emergency_preset"])
	}
	if resp["emergency_mode"] != false {
		t.Errorf("emergency_mode=%v, want false", resp["emergency_mode"])
	}
}

func TestEmergencyPreset_ApplyNormal_ReturnsNonEmergency(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	// First apply lockdown
	w1 := httptest.NewRecorder()
	req1 := httptest.NewRequest(http.MethodPost, "/api/abuse-preset/lockdown", nil)
	req1.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w1, req1)

	if w1.Code != http.StatusOK {
		t.Fatalf("step 1 lockdown: %d", w1.Code)
	}

	// Then apply normal
	w2 := httptest.NewRecorder()
	req2 := httptest.NewRequest(http.MethodPost, "/api/abuse-preset/normal", nil)
	req2.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w2, req2)

	if w2.Code != http.StatusOK {
		t.Fatalf("step 2 normal: %d", w2.Code)
	}

	var cfg AbuseConfig
	if err := json.NewDecoder(w2.Body).Decode(&cfg); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if cfg.EmergencyMode {
		t.Error("normal preset should set EmergencyMode=false")
	}
	if cfg.EmergencyPreset != "normal" {
		t.Errorf("EmergencyPreset=%q, want normal", cfg.EmergencyPreset)
	}
	if cfg.RPS != 1.0 {
		t.Errorf("normal rps=%f, want 1.0", cfg.RPS)
	}
}

func TestEmergencyPreset_CautiousReturnsCautiousSettings(t *testing.T) {
	router, cleanup := newTestAbuseServer(t)
	defer cleanup()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/abuse-preset/cautious", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("POST /api/abuse-preset/cautious = %d, want 200", w.Code)
	}

	var cfg AbuseConfig
	if err := json.NewDecoder(w.Body).Decode(&cfg); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if cfg.RPS > 0.6 || cfg.RPS < 0.4 {
		t.Errorf("cautious rps=%f, want ~0.5", cfg.RPS)
	}
	if cfg.TokenBudget != 10000 {
		t.Errorf("cautious token_budget=%d, want 10000", cfg.TokenBudget)
	}
	// CheapModel field removed — use provider_priority fallback via LiteLLM Router
	if cfg.MinIntervalMs < 1500 {
		t.Errorf("cautious min_interval_ms=%d, want >=2000", cfg.MinIntervalMs)
	}
	if cfg.MaxMessageLength != 1000 {
		t.Errorf("cautious max_message_length=%d, want 1000", cfg.MaxMessageLength)
	}
	if cfg.EmergencyPreset != "cautious" {
		t.Errorf("EmergencyPreset=%q, want cautious", cfg.EmergencyPreset)
	}
}
