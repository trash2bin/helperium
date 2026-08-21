// Package server provides admin-dashboard HTTP server (abuse config management).
//
// HTTP routes called (to upstream services):
//
//	proxyGetToApiService()  -> api-service:GET /api/agents/{name}    (get agent abuse config)
//	proxyPutToApiService()  -> api-service:PUT /api/agents/{name}    (update agent abuse config)
//	notifyApiServiceReload()-> api-service:POST /admin/abuse-config/reload (reload abuse)
package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
)

// ── AbuseConfig ──

// AbuseConfig defines global default anti-abuse / rate-limit settings.
// These are stored as a JSON file and can be overridden per-agent.
//
// Defaults match the api-service's env-var defaults.
type AbuseConfig struct {
	// Rate limiting
	RPS   float64 `json:"rps"`   // sustained requests per second (env: ABUSE_RPS)
	Burst int     `json:"burst"` // burst size (env: ABUSE_BURST)

	// Message restrictions
	MaxMessageLength       int `json:"max_message_length"`         // max chars per message
	MinIntervalMs          int `json:"min_interval_ms"`            // min ms between messages in a session
	MaxUserTurnsPerSession int `json:"max_user_turns_per_session"` // accepted user turns per session

	// User-Agent filtering
	BlockEmptyUserAgent bool     `json:"block_empty_user_agent"`
	BlockedUserAgents   []string `json:"blocked_user_agents"` // patterns to block

	// Emergency controls
	EmergencyMode   bool   `json:"emergency_mode"`   // global emergency toggle
	EmergencyPreset string `json:"emergency_preset"` // "normal", "cautious", "lockdown"

	// Runtime settings (agent loop behaviour)
	HistoryTurns        int `json:"history_turns"`         // max conversation turns in history (env: DEMO_HISTORY_TURNS)
	HistoryContentChars int `json:"history_content_chars"` // max chars per history message (env: DEMO_HISTORY_CONTENT_CHARS)
	MaxIterations       int `json:"max_iterations"`        // max agent loop iterations (env: AGENT_MAX_ITERATIONS)
	MaxEmptyRounds      int `json:"max_empty_rounds"`      // max empty LLM rounds (env: AGENT_MAX_EMPTY_ROUNDS)
	MaxTurnTokens       int `json:"max_turn_tokens"`       // max tokens per turn (env: AGENT_MAX_TURN_TOKENS)
	SessionTTLHours     int `json:"session_ttl_hours"`     // session TTL in hours (0 = forever)
}

// DefaultAbuseConfig returns sensible defaults (matching api-service env defaults).
// DefaultAbuseConfig returns sensible defaults loaded from environment variables when set.
func DefaultAbuseConfig() AbuseConfig {
	return AbuseConfig{
		RPS:   getEnvFloat64("ABUSE_RPS", 1.0),
		Burst: getEnvInt("ABUSE_BURST", 5),

		MaxMessageLength:       getEnvInt("ABUSE_MAX_MSG_LENGTH", 2000),
		MinIntervalMs:          getEnvInt("ABUSE_MIN_INTERVAL_MS", 1000),
		MaxUserTurnsPerSession: getEnvInt("ABUSE_MAX_USER_TURNS", 50),

		BlockEmptyUserAgent: true,
		BlockedUserAgents: []string{
			"curl/*",
			"python-requests/*",
			"Go-http-client/*",
			"Wget/*",
		},

		// Emergency defaults
		EmergencyMode:   getEnvBool("ABUSE_EMERGENCY_MODE", false),
		EmergencyPreset: getEnvString("ABUSE_EMERGENCY_PRESET", "normal"),

		// Runtime defaults (matching DemoSettings env defaults)
		HistoryTurns:        getEnvInt("DEMO_HISTORY_TURNS", 8),
		HistoryContentChars: getEnvInt("DEMO_HISTORY_CONTENT_CHARS", 6000),
		MaxIterations:       getEnvInt("AGENT_MAX_ITERATIONS", 5),
		MaxEmptyRounds:      getEnvInt("AGENT_MAX_EMPTY_ROUNDS", 3),
		MaxTurnTokens:       getEnvInt("AGENT_MAX_TURN_TOKENS", 8000),
		SessionTTLHours:     getEnvInt("SESSION_TTL_HOURS", 0),
	}
}

// ── Per-Agent Abuse Override ──

// AgentAbuseOverride represents per-agent overrides for abuse settings.
// Empty/null fields mean "use global default".
type AgentAbuseOverride struct {
	RPS                    *float64 `json:"rps,omitempty"`
	Burst                  *int     `json:"burst,omitempty"`
	MaxMessageLength       *int     `json:"max_message_length,omitempty"`
	MinIntervalMs          *int     `json:"min_interval_ms,omitempty"`
	MaxUserTurnsPerSession *int     `json:"max_user_turns_per_session,omitempty"`
	BlockEmptyUserAgent    *bool    `json:"block_empty_user_agent,omitempty"`
	BlockedUserAgents      []string `json:"blocked_user_agents,omitempty"`
}

// ── File-based global store ──

// AbuseStore persists AbuseConfig as JSON on disk.
type AbuseStore struct {
	mu       sync.RWMutex
	filePath string
	config   AbuseConfig
}

// NewAbuseStore creates or loads an AbuseStore from the given directory.
func NewAbuseStore(dataDir string) *AbuseStore {
	s := &AbuseStore{
		filePath: filepath.Join(dataDir, "abuse_config.json"),
		config:   DefaultAbuseConfig(),
	}
	s.load()
	return s
}

func (s *AbuseStore) load() {
	data, err := os.ReadFile(s.filePath)
	if err != nil {
		// File doesn't exist yet — use defaults
		return
	}
	var cfg AbuseConfig
	if err := decodeStrictJSON(bytes.NewReader(data), &cfg); err != nil {
		panic(fmt.Sprintf("invalid persisted anti-abuse config %s: %v", s.filePath, err))
	}
	s.config = cfg
}

func (s *AbuseStore) save() error {
	data, err := json.MarshalIndent(s.config, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal abuse config: %w", err)
	}
	dir := filepath.Dir(s.filePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("mkdir: %w", err)
	}
	if err := os.WriteFile(s.filePath, data, 0644); err != nil {
		return fmt.Errorf("write abuse_config.json: %w", err)
	}
	return nil
}

// Get returns a copy of the current global config.
func (s *AbuseStore) Get() AbuseConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.config
}

// Set updates the global config and persists it to disk.
func (s *AbuseStore) Set(cfg AbuseConfig) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.config = cfg
	return s.save()
}

// ── Server integration ──

// AddAbuseStore adds the abuse store to the Server (called after New).
func (s *Server) AddAbuseStore(dataDir string) {
	s.abuseStore = NewAbuseStore(dataDir)
}

func (s *Server) abuseSettingsGetHandler(w http.ResponseWriter, r *http.Request) {
	if s.abuseStore == nil {
		respondJSON(w, http.StatusOK, DefaultAbuseConfig())
		return
	}
	respondJSON(w, http.StatusOK, s.abuseStore.Get())
}

func (s *Server) abuseSettingsPutHandler(w http.ResponseWriter, r *http.Request) {
	if s.abuseStore == nil {
		respondError(w, http.StatusInternalServerError, "store_unavailable", "abuse store not initialized")
		return
	}

	var cfg AbuseConfig
	if err := decodeStrictJSON(r.Body, &cfg); err != nil {
		respondError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}

	// Apply defaults for zero values
	def := DefaultAbuseConfig()
	if cfg.MaxMessageLength <= 0 {
		cfg.MaxMessageLength = def.MaxMessageLength
	}
	if cfg.MinIntervalMs <= 0 {
		cfg.MinIntervalMs = def.MinIntervalMs
	}
	if cfg.MaxUserTurnsPerSession <= 0 {
		cfg.MaxUserTurnsPerSession = def.MaxUserTurnsPerSession
	}
	if cfg.RPS <= 0 {
		cfg.RPS = def.RPS
	}
	if cfg.Burst <= 0 {
		cfg.Burst = def.Burst
	}

	previous := s.abuseStore.Get()
	if err := s.abuseStore.Set(cfg); err != nil {
		respondError(w, http.StatusInternalServerError, "save_error", err.Error())
		return
	}
	if err := s.applyAbuseConfig(); err != nil {
		if rollbackErr := s.abuseStore.Set(previous); rollbackErr != nil {
			slog.Error("failed to roll back unapplied abuse settings", "error", rollbackErr)
		}
		respondError(w, http.StatusBadGateway, "config_not_applied", err.Error())
		return
	}

	slog.Info("abuse settings applied", "rps", cfg.RPS, "burst", cfg.Burst)
	respondJSON(w, http.StatusOK, cfg)
}

// Per-agent abuse overrides — proxied through api-service's agent store.

// agentAbuseGetHandler returns the abuse overrides for a specific agent.
// It fetches the agent config from api-service and extracts abuse_config.
func (s *Server) agentAbuseGetHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	if name == "" {
		respondError(w, http.StatusBadRequest, "missing_name", "agent name is required")
		return
	}

	// Proxy to api-service to get the agent
	body, status, err := s.proxyGetToApiService("/api/agents/" + name)
	if err != nil {
		respondError(w, http.StatusBadGateway, "api_unreachable", err.Error())
		return
	}
	if status != http.StatusOK {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write(body)
		return
	}

	// Try to extract abuse_config from the agent object
	var agentData map[string]any
	if err := json.Unmarshal(body, &agentData); err != nil {
		respondError(w, http.StatusInternalServerError, "parse_error", err.Error())
		return
	}

	abuseCfg := AgentAbuseOverride{}
	if raw, ok := agentData["abuse_config"]; ok && raw != nil {
		// Marshal back to bytes then unmarshal into struct
		rawBytes, _ := json.Marshal(raw)
		_ = json.Unmarshal(rawBytes, &abuseCfg)
	}

	respondJSON(w, http.StatusOK, map[string]any{
		"agent":        agentData,
		"abuse_config": abuseCfg,
	})
}

// agentAbusePutHandler updates the abuse overrides for a specific agent.
// It fetches the current agent from api-service, merges abuse_config, and PUTs back.
func (s *Server) agentAbusePutHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	if name == "" {
		respondError(w, http.StatusBadRequest, "missing_name", "agent name is required")
		return
	}

	var override AgentAbuseOverride
	if err := decodeStrictJSON(r.Body, &override); err != nil {
		respondError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}

	// Merge: replace abuse_config on the existing agent
	body, status, err := s.proxyGetToApiService("/api/agents/" + name)
	if err != nil {
		respondError(w, http.StatusBadGateway, "api_unreachable", err.Error())
		return
	}
	if status != http.StatusOK {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write(body)
		return
	}

	var agentData map[string]any
	if err := json.Unmarshal(body, &agentData); err != nil {
		respondError(w, http.StatusInternalServerError, "parse_error", err.Error())
		return
	}

	agentData["abuse_config"] = override

	// PUT back to api-service
	updateBody, updateStatus, err := s.proxyPutToApiService("/api/agents/"+name, agentData)
	if err != nil {
		respondError(w, http.StatusBadGateway, "api_unreachable", err.Error())
		return
	}

	slog.Info("agent abuse settings updated", "agent", name)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(updateStatus)
	w.Write(updateBody)
}

// abuseReloadHandler — UI-facing endpoint that triggers a reload of
// anti-abuse config on the api-service (POST /api/admin/abuse-config/reload).
func (s *Server) abuseReloadHandler(w http.ResponseWriter, r *http.Request) {
	if err := s.applyAbuseConfig(); err != nil {
		respondError(w, http.StatusBadGateway, "config_not_applied", err.Error())
		return
	}
	respondJSON(w, http.StatusOK, map[string]any{
		"status":  "applied",
		"message": "API service applied the current abuse config",
	})
}

// applyAbuseConfig reloads api-service synchronously and returns only after it
// acknowledges the same persisted policy. A dashboard success response therefore
// means the effective runtime policy changed, not merely that a local file wrote.
func (s *Server) applyAbuseConfig() error {
	if s.opts.ApiSvcURL == "" {
		return fmt.Errorf("api-service URL not configured")
	}
	if s.opts.ApiBearerToken == "" {
		return fmt.Errorf("API control-plane bearer is not configured")
	}
	apiURL := s.opts.ApiSvcURL + "/admin/abuse-config/reload"
	req, err := http.NewRequest(http.MethodPost, apiURL, nil)
	if err != nil {
		return fmt.Errorf("create reload request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.ApiBearerToken)
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("api-service reload request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("api-service reload returned status %d", resp.StatusCode)
	}
	slog.Info("api-service abuse config applied")
	return nil
}

// ── API helpers for api-service ──

func (s *Server) proxyGetToApiService(path string) ([]byte, int, error) {
	apiURL := s.opts.ApiSvcURL + path
	req, err := http.NewRequest(http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.ApiBearerToken)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	return body, resp.StatusCode, nil
}

func (s *Server) proxyPutToApiService(path string, payload any) ([]byte, int, error) {
	data, _ := json.Marshal(payload)
	apiURL := s.opts.ApiSvcURL + path
	req, err := http.NewRequest(http.MethodPut, apiURL, strings.NewReader(string(data)))
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.ApiBearerToken)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	return body, resp.StatusCode, nil
}

// ── Emergency presets ──

// EmergencyPresets returns preset configurations for quick switching.
func EmergencyPresets() map[string]AbuseConfig {
	normal := DefaultAbuseConfig()

	cautious := DefaultAbuseConfig()
	cautious.RPS = 0.5
	cautious.Burst = 3
	cautious.MaxMessageLength = 1000
	cautious.MinIntervalMs = 2000
	cautious.MaxUserTurnsPerSession = 30
	cautious.BlockedUserAgents = append(cautious.BlockedUserAgents, "Mozilla/4.*", "MSIE.*")
	cautious.EmergencyPreset = "cautious"

	lockdown := DefaultAbuseConfig()
	lockdown.RPS = 0.2
	lockdown.Burst = 1
	lockdown.MaxMessageLength = 500
	lockdown.MinIntervalMs = 5000
	lockdown.MaxUserTurnsPerSession = 10
	lockdown.BlockEmptyUserAgent = true
	lockdown.BlockedUserAgents = []string{
		"curl/*", "python-requests/*", "Go-http-client/*", "Wget/*",
		"Mozilla/4.*", "MSIE.*", "Java/*", "libwww/*", "scrapy/*",
		"axios/*", "PostmanRuntime/*",
	}
	lockdown.EmergencyMode = true
	lockdown.EmergencyPreset = "lockdown"

	return map[string]AbuseConfig{
		"normal":   normal,
		"cautious": cautious,
		"lockdown": lockdown,
	}
}

// abusePresetHandler applies a preset and returns the resulting config.
func (s *Server) abusePresetHandler(w http.ResponseWriter, r *http.Request) {
	preset := chi.URLParam(r, "preset")

	presets := EmergencyPresets()
	cfg, ok := presets[preset]
	if !ok {
		respondError(w, http.StatusBadRequest, "invalid_preset",
			fmt.Sprintf("unknown preset %q, valid: normal, cautious, lockdown", preset))
		return
	}

	if s.abuseStore == nil {
		respondError(w, http.StatusInternalServerError, "store_unavailable", "abuse store not initialized")
		return
	}
	previous := s.abuseStore.Get()
	if err := s.abuseStore.Set(cfg); err != nil {
		respondError(w, http.StatusInternalServerError, "save_error", err.Error())
		return
	}
	if err := s.applyAbuseConfig(); err != nil {
		if rollbackErr := s.abuseStore.Set(previous); rollbackErr != nil {
			slog.Error("failed to roll back unapplied emergency preset", "error", rollbackErr)
		}
		respondError(w, http.StatusBadGateway, "config_not_applied", err.Error())
		return
	}

	slog.Warn("emergency preset applied",
		"preset", preset,
		"emergency_mode", cfg.EmergencyMode,
		"rps", cfg.RPS,
		"burst", cfg.Burst,
	)

	respondJSON(w, http.StatusOK, cfg)
}

// emergencyStatusHandler returns current emergency state: mode, preset, and key metrics.
func (s *Server) emergencyStatusHandler(w http.ResponseWriter, r *http.Request) {
	var cfg AbuseConfig
	if s.abuseStore != nil {
		cfg = s.abuseStore.Get()
	} else {
		cfg = DefaultAbuseConfig()
	}

	respondJSON(w, http.StatusOK, map[string]any{
		"emergency_mode":   cfg.EmergencyMode,
		"emergency_preset": cfg.EmergencyPreset,
		"rps":              cfg.RPS,
		"burst":            cfg.Burst,
		"max_user_turns":   cfg.MaxUserTurnsPerSession,
		"min_interval_ms":  cfg.MinIntervalMs,
		"active":           cfg.EmergencyMode && cfg.EmergencyPreset == "lockdown",
	})
}

// decodeStrictJSON rejects unknown fields so renamed policy controls cannot be ignored.
func decodeStrictJSON(body io.Reader, target any) error {
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

// getEnvFloat64 reads an environment variable as float64, falling back to defaultVal.
func getEnvFloat64(key string, defaultVal float64) float64 {
	if value, err := strconv.ParseFloat(os.Getenv(key), 64); err == nil {
		return value
	}
	return defaultVal
}

// getEnvInt reads an environment variable as int, falling back to defaultVal.
func getEnvInt(key string, defaultVal int) int {
	if value, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return value
	}
	return defaultVal
}

// getEnvString reads an environment variable, falling back to defaultVal when empty.
func getEnvString(key, defaultVal string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultVal
}

// getEnvBool reads an environment variable as bool, falling back to defaultVal.
func getEnvBool(key string, defaultVal bool) bool {
	if value, err := strconv.ParseBool(os.Getenv(key)); err == nil {
		return value
	}
	return defaultVal
}
