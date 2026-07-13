// Package server provides admin-dashboard HTTP server (abuse config management).
//
// HTTP routes called (to upstream services):
//   proxyGetToApiService()  -> api-service:GET /api/agents/{name}    (get agent abuse config)
//   proxyPutToApiService()  -> api-service:PUT /api/agents/{name}    (update agent abuse config)
//   notifyApiServiceReload()-> api-service:POST /admin/abuse-config/reload (reload abuse)
package server

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/go-chi/chi/v5"
)

// ── AbuseConfig ──

// AbuseConfig defines global default anti-abuse / rate-limit settings.
// These are stored as a JSON file and can be overridden per-agent.
//
// Defaults match the api-service's env-var defaults.
type AbuseConfig struct {
	// Rate limiting
	RPS   float64 `json:"rps"`   // sustained requests per second (env: CHAT_RATE_LIMIT)
	Burst int     `json:"burst"` // burst size (env: CHAT_RATE_LIMIT_BURST)

	// Message restrictions
	MaxMessageLength      int `json:"max_message_length"`       // max chars per message
	MinIntervalMs         int `json:"min_interval_ms"`          // min ms between messages in a session
	MaxMessagesPerSession int `json:"max_messages_per_session"` // max messages per session

	// User-Agent filtering
	BlockEmptyUserAgent bool     `json:"block_empty_user_agent"`
	BlockedUserAgents   []string `json:"blocked_user_agents"` // patterns to block

	// Emergency / token saving
	EmergencyMode   bool   `json:"emergency_mode"`   // global emergency toggle
	TokenBudget     int    `json:"token_budget"`     // max tokens per session (0 = unlimited)
	EmergencyPreset string `json:"emergency_preset"` // "normal", "cautious", "lockdown"

	// Runtime settings (agent loop behaviour)
	HistoryTurns         int `json:"history_turns"`          // max conversation turns in history (env: DEMO_HISTORY_TURNS)
	HistoryContentChars  int `json:"history_content_chars"`  // max chars per history message (env: DEMO_HISTORY_CONTENT_CHARS)
	MaxIterations        int `json:"max_iterations"`         // max agent loop iterations (env: AGENT_MAX_ITERATIONS)
	MaxEmptyRounds       int `json:"max_empty_rounds"`       // max empty LLM rounds (env: AGENT_MAX_EMPTY_ROUNDS)
	MaxTurnTokens        int `json:"max_turn_tokens"`        // max tokens per turn (env: AGENT_MAX_TURN_TOKENS)
	SessionTTLHours      int `json:"session_ttl_hours"`      // session TTL in hours (0 = forever)
}

// DefaultAbuseConfig returns sensible defaults (matching api-service env defaults).
func DefaultAbuseConfig() AbuseConfig {
	return AbuseConfig{
		RPS:   1.0,
		Burst: 5,

		MaxMessageLength:      2000,
		MinIntervalMs:         1000,
		MaxMessagesPerSession: 50,

		BlockEmptyUserAgent: true,
		BlockedUserAgents: []string{
			"curl/*",
			"python-requests/*",
			"Go-http-client/*",
			"Wget/*",
		},

		// Emergency defaults
		EmergencyMode:   false,
		TokenBudget:     0,  // 0 = unlimited
		EmergencyPreset: "normal",

		// Runtime defaults (matching DemoSettings env defaults)
		HistoryTurns:        8,
		HistoryContentChars: 6000,
		MaxIterations:       5,
		MaxEmptyRounds:      3,
		MaxTurnTokens:       8000,
		SessionTTLHours:     0,
	}
}

// ── Per-Agent Abuse Override ──

// AgentAbuseOverride represents per-agent overrides for abuse settings.
// Empty/null fields mean "use global default".
type AgentAbuseOverride struct {
	RPS                   *float64 `json:"rps,omitempty"`
	Burst                 *int     `json:"burst,omitempty"`
	MaxMessageLength      *int     `json:"max_message_length,omitempty"`
	MinIntervalMs         *int     `json:"min_interval_ms,omitempty"`
	MaxMessagesPerSession *int     `json:"max_messages_per_session,omitempty"`
	BlockEmptyUserAgent   *bool    `json:"block_empty_user_agent,omitempty"`
	BlockedUserAgents     []string `json:"blocked_user_agents,omitempty"`
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
	if err := json.Unmarshal(data, &cfg); err != nil {
		slog.Warn("failed to parse abuse_config.json, using defaults", "error", err)
		return
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
	if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
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
	if cfg.MaxMessagesPerSession <= 0 {
		cfg.MaxMessagesPerSession = def.MaxMessagesPerSession
	}
	if cfg.RPS <= 0 {
		cfg.RPS = def.RPS
	}
	if cfg.Burst <= 0 {
		cfg.Burst = def.Burst
	}

	if err := s.abuseStore.Set(cfg); err != nil {
		respondError(w, http.StatusInternalServerError, "save_error", err.Error())
		return
	}

	slog.Info("abuse settings updated", "rps", cfg.RPS, "burst", cfg.Burst)

	// Notify api-service to reload its config
	if s.opts.ApiSvcURL != "" {
		s.notifyApiServiceReload()
	}

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
	if err := json.NewDecoder(r.Body).Decode(&override); err != nil {
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
// notifyApiServiceReload sends a POST request to api-service to reload abuse config.
func (s *Server) notifyApiServiceReload() {
	apiURL := s.opts.ApiSvcURL + "/admin/abuse-config/reload"
	go func() {
		req, err := http.NewRequest(http.MethodPost, apiURL, nil)
		if err != nil {
			slog.Warn("failed to create reload request", "error", err)
			return
		}
		if s.opts.AdminToken != "" {
			req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			slog.Warn("failed to notify api-service", "error", err)
			return
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			slog.Info("api-service abuse config reloaded")
		} else {
			slog.Warn("api-service reload returned non-ok status", "status", resp.StatusCode)
		}
	}()
}

// ── API helpers for api-service ──

func (s *Server) proxyGetToApiService(path string) ([]byte, int, error) {
	apiURL := s.opts.ApiSvcURL + path
	req, err := http.NewRequest(http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}
	if s.opts.AdminToken != "" {
		req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)
	}
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
	if s.opts.AdminToken != "" {
		req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)
	}
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
	cautious.MaxMessagesPerSession = 30
	cautious.BlockedUserAgents = append(cautious.BlockedUserAgents, "Mozilla/4.*", "MSIE.*")
	cautious.TokenBudget = 10000
	cautious.EmergencyPreset = "cautious"

	lockdown := DefaultAbuseConfig()
	lockdown.RPS = 0.2
	lockdown.Burst = 1
	lockdown.MaxMessageLength = 500
	lockdown.MinIntervalMs = 5000
	lockdown.MaxMessagesPerSession = 10
	lockdown.BlockEmptyUserAgent = true
	lockdown.BlockedUserAgents = []string{
		"curl/*", "python-requests/*", "Go-http-client/*", "Wget/*",
		"Mozilla/4.*", "MSIE.*", "Java/*", "libwww/*", "scrapy/*",
		"axios/*", "PostmanRuntime/*",
	}
	lockdown.TokenBudget = 2000
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

	if s.abuseStore != nil {
		if err := s.abuseStore.Set(cfg); err != nil {
			respondError(w, http.StatusInternalServerError, "save_error", err.Error())
			return
		}
	}

	// Notify api-service to reload its config
	if s.opts.ApiSvcURL != "" {
		s.notifyApiServiceReload()
	}

	slog.Warn("emergency preset applied",
		"preset", preset,
		"emergency_mode", cfg.EmergencyMode,
		"rps", cfg.RPS,
		"burst", cfg.Burst,
		"token_budget", cfg.TokenBudget,
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
		"token_budget":     cfg.TokenBudget,
		"max_messages":     cfg.MaxMessagesPerSession,
		"min_interval_ms":  cfg.MinIntervalMs,
		"active":           cfg.EmergencyMode && cfg.EmergencyPreset == "lockdown",
	})
}
