package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/mark3labs/mcp-go/server"

	"github.com/agent-tutor/agent-tutor-go/pkg/cors"
	"github.com/agent-tutor/mcp-gateway/internal/httpclient"
	gwserver "github.com/agent-tutor/mcp-gateway/internal/server"
	"github.com/agent-tutor/mcp-gateway/internal/tools"
)

var globalClient *httpclient.Client

// postHandlerTimeout bounds a single JSON-RPC request/response cycle
// (HandleMessage + write to the SSE stream). It is intentionally short
// and scoped to the POST handler only — it must NOT be confused with the
// old http.Server.WriteTimeout, which used to apply to the *entire*
// lifetime of the associated GET /mcp SSE connection and silently killed
// every session after 30s of being open. See buildHTTPServer for the fix.
// Can be overridden with MCP_POST_HANDLER_TIMEOUT environment variable (seconds).
var postHandlerTimeout = func() time.Duration {
	if v := os.Getenv("MCP_POST_HANDLER_TIMEOUT"); v != "" {
		if sec, err := strconv.Atoi(v); err == nil && sec > 0 {
			return time.Duration(sec) * time.Second
		}
	}
	return 25 * time.Second
}()

// Session management constants
// Can be overridden with environment variables
var (
	// MaxSessions limits concurrent SSE sessions per process to prevent OOM
	// Can be overridden with MCP_MAX_SESSIONS environment variable
	MaxSessions = func() int {
		if v := os.Getenv("MCP_MAX_SESSIONS"); v != "" {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				return n
			}
		}
		return 1000 // default
	}()

	// SessionIdleTimeout closes idle SSE connections after this duration
	// Can be overridden with MCP_SESSION_IDLE_TIMEOUT environment variable (e.g., "5m", "30s")
	SessionIdleTimeout = func() time.Duration {
		if v := os.Getenv("MCP_SESSION_IDLE_TIMEOUT"); v != "" {
			if d, err := time.ParseDuration(v); err == nil {
				return d
			}
		}
		return 5 * time.Minute // default
	}()

	// SessionMaxLifetime forces session recreation after this duration
	// Can be overridden with MCP_SESSION_MAX_LIFETIME environment variable (e.g., "30m", "1h")
	SessionMaxLifetime = func() time.Duration {
		if v := os.Getenv("MCP_SESSION_MAX_LIFETIME"); v != "" {
			if d, err := time.ParseDuration(v); err == nil {
				return d
			}
		}
		return 30 * time.Minute // default
	}()
)

// sseSession represents one long-lived SSE connection (opened via GET) and
// the (possibly composite) MCP server state associated with it.
//
// mu guards mcpServer, tenantIDs, and all writes to writer/flusher.
type sseSession struct {
	mu           sync.Mutex
	writer       http.ResponseWriter
	flusher      http.Flusher
	done         chan struct{}
	tenantIDs    []string
	mcpServer    *server.MCPServer
	createdAt    time.Time
	lastActivity time.Time
}

func (s *sseSession) getTenantIDs() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.tenantIDs
}

func (s *sseSession) isExpired() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	return now.Sub(s.lastActivity) > SessionIdleTimeout || now.Sub(s.createdAt) > SessionMaxLifetime
}

// writeMessage safely writes a JSON-RPC "message" SSE event to this
// session's underlying connection.
func (s *sseSession) writeMessage(eventData []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fmt.Fprintf(s.writer, "event: message\ndata: %s\n\n", eventData)
	s.flusher.Flush()
	s.lastActivity = time.Now()
}

// ensureCompositeServer lazily creates (or re-creates, on tenant list change)
// the (possibly composite) MCP server for this session.
// Guarded by mu so concurrent POSTs on the same session can't race.
func (s *sseSession) ensureCompositeServer(tenantIDs []string) (*server.MCPServer, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Fast path: same tenants → reuse
	if s.mcpServer != nil && sliceEqual(s.tenantIDs, tenantIDs) {
		s.lastActivity = time.Now()
		return s.mcpServer, nil
	}

	slog.Info("Initializing MCP server for session", "tenants", tenantIDs)
	mcpServer, err := createCompositeServer(tenantIDs)
	if err != nil {
		return nil, err
	}
	s.mcpServer = mcpServer
	s.tenantIDs = tenantIDs
	s.lastActivity = time.Now()
	return mcpServer, nil
}

// sliceEqual checks if two string slices have the same elements in the same order.
func sliceEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func main() {
	devMode := os.Getenv("MCP_DEV") == "true"
	logLevel := slog.LevelInfo
	if devMode {
		logLevel = slog.LevelDebug
	}
	logHandler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel})
	slog.SetDefault(slog.New(logHandler))

	globalClient = httpclient.New()
	r := buildRouter()
	port := os.Getenv("MCP_PORT")
	if port == "" {
		port = "8083"
	}

	httpServer := buildHTTPServer(r, port)

	go func() {
		quit := make(chan os.Signal, 1)
		signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
		<-quit
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		httpServer.Shutdown(ctx)
	}()

	slog.Info("mcp-gateway listening", "port", port)
	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
}

// buildHTTPServer configures server-level timeouts.
//
// WriteTimeout is intentionally NOT set: it applies to the entire
// lifetime of a connection's response, and our GET /mcp handler holds
// that response open indefinitely to stream SSE events. A non-zero
// WriteTimeout here silently kills every SSE session ~N seconds after it
// opens, regardless of activity. Slow-client/slow-write protection is
// instead applied per-request inside mcpPostHandler via context, and
// ReadHeaderTimeout below still protects against slow/stalled request
// headers (slowloris-style attacks) without touching long-lived SSE
// writes.
//
// Can be overridden with env vars:
//
//	MCP_READ_HEADER_TIMEOUT (seconds, default 10)
//	MCP_IDLE_TIMEOUT (seconds, default 120)
func buildHTTPServer(r http.Handler, port string) *http.Server {
	readHeaderTimeout := 10 * time.Second
	if v := os.Getenv("MCP_READ_HEADER_TIMEOUT"); v != "" {
		if sec, err := strconv.Atoi(v); err == nil && sec > 0 {
			readHeaderTimeout = time.Duration(sec) * time.Second
		}
	}
	idleTimeout := 120 * time.Second
	if v := os.Getenv("MCP_IDLE_TIMEOUT"); v != "" {
		if sec, err := strconv.Atoi(v); err == nil && sec > 0 {
			idleTimeout = time.Duration(sec) * time.Second
		}
	}
	return &http.Server{
		Addr:              ":" + port,
		Handler:           r,
		ReadHeaderTimeout: readHeaderTimeout,
		IdleTimeout:       idleTimeout,
		// WriteTimeout intentionally omitted — see doc comment above.
	}
}

// createServerForTenant creates a per-tenant MCP server (single-tenant, no prefix).
// Kept for backward compatibility and internal use.
func createServerForTenant(tenantID string) (*server.MCPServer, error) {
	slog.Info("Fetching config for tenant", "tenantID", tenantID)
	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch config", "tenantID", tenantID, "error", err)
		return nil, err
	}
	slog.Info("Config fetched, creating server", "tenantID", tenantID)
	mcpServer := server.NewMCPServer("agent-tutor", "1.0.0")
	slog.Info("Creating registry", "tenantID", tenantID)
	registry := tools.NewRegistry(cfg)
	slog.Info("Registering tools", "tenantID", tenantID)
	registry.RegisterAll(mcpServer)
	slog.Info("MCP server ready", "tenantID", tenantID)
	return mcpServer, nil
}

// createCompositeServer creates a composite MCP server for multiple tenants.
// Single tenant → standard mode (no prefix, backward compat).
// Multiple tenants → all tools registered with "{tenantID}__" prefix.
func createCompositeServer(tenantIDs []string) (*server.MCPServer, error) {
	// Single tenant: backward-compatible path (no prefix)
	if len(tenantIDs) == 1 {
		return createServerForTenant(tenantIDs[0])
	}

	slog.Info("Creating composite MCP server", "tenants", tenantIDs)
	composite := server.NewMCPServer("agent-tutor", "1.0.0")

	for _, tenantID := range tenantIDs {
		slog.Info("Fetching config for tenant", "tenantID", tenantID)
		cfg, err := globalClient.FetchConfigWithTenant(tenantID)
		if err != nil {
			slog.Error("Failed to fetch config", "tenantID", tenantID, "error", err)
			return nil, err
		}

		slog.Info("Registering tools for tenant", "tenantID", tenantID)
		registry := tools.NewPrefixedRegistry(cfg, tenantID)
		registry.RegisterAll(composite)
	}

	slog.Info("Composite MCP server ready", "tenants", tenantIDs, "count", len(tenantIDs))
	return composite, nil
}

// authMiddleware проверяет Authorization: Bearer <token> на всех маршрутах,
// кроме /health. Если переменная окружения MCP_API_KEY не установлена,
// middleware пропускает все запросы (backward compat).
func authMiddleware(next http.Handler) http.Handler {
	apiKey := os.Getenv("MCP_API_KEY")
	if apiKey == "" {
		// No auth configured — skip entirely
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Health endpoint is excluded from auth
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		auth := r.Header.Get("Authorization")
		if auth == "" || !strings.HasPrefix(auth, "Bearer ") {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized", "message": "Missing or invalid Authorization header"})
			return
		}
		token := strings.TrimPrefix(auth, "Bearer ")
		if token != apiKey {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized", "message": "Invalid API key"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func buildRouter() *chi.Mux {
	sessions := &sync.Map{}
	r := chi.NewRouter()

	// Recover from panics in any handler (e.g. a misbehaving tool) so one
	// bad request can't take down the process, and so we get a proper
	// stack trace in the logs instead of a silently dropped connection.
	r.Use(chimiddleware.Recoverer)

	// Auth middleware — check Authorization: Bearer <token> on all routes
	// except /health. If MCP_API_KEY env is empty, auth is skipped.
	r.Use(authMiddleware)

	// Global request logger to debug routing issues
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			slog.Info("INCOMING REQUEST", "method", r.Method, "path", r.URL.Path, "tenant", r.Header.Get("X-Tenant-ID"))
			next.ServeHTTP(w, r)
		})
	})

	r.Get("/health", healthHandler())
	r.Get("/docs", gwserver.SwaggerHandler())
	r.Get("/openapi.json", gwserver.OpenAPIHandler())
	r.Get("/debug", debugPlaygroundHandler())
	r.Get("/config", debugConfigHandler())
	r.Get("/debug/sessions", debugSessionsHandler(sessions))
	r.Get("/debug/config", debugConfigHandler())
	r.Get("/mcp", sseHandler(sessions))
	r.Get("/sse", sseHandler(sessions))
	r.Get("/", sseHandler(sessions))
	mcpPost := mcpPostHandler(sessions)

	// POST (MCP JSON-RPC message) endpoints have rate limiting applied
	r.Group(func(r chi.Router) {
		r.Use(mcpRateLimitMiddleware())
		r.Post("/mcp/message", mcpPost)
		r.Post("/mcp", mcpPost)
		r.Post("/message", mcpPost)
		r.Post("/", mcpPost)
	})

	r.Get("/mcp/manifest", manifestProxyHandler)
	return r
}

func healthHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}
}

func sseHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("Access-Control-Allow-Origin", cors.AllowOrigin())

		// Enforce session limit to prevent OOM
		count := 0
		sessions.Range(func(_, _ any) bool {
			count++
			return true
		})
		if count >= MaxSessions {
			http.Error(w, "Too many SSE sessions", http.StatusServiceUnavailable)
			return
		}

		sessionID := uuid.New().String()
		now := time.Now()
		session := &sseSession{
			writer:       w,
			flusher:      flusher,
			done:         make(chan struct{}),
			tenantIDs:    resolveTenantIDs(r),
			createdAt:    now,
			lastActivity: now,
		}
		sessions.Store(sessionID, session)
		defer func() {
			sessions.Delete(sessionID)
			slog.Info("MCP session closed", "sessionID", sessionID)
		}()

		messageURL := fmt.Sprintf("http://%s/mcp/message?sessionId=%s", r.Host, sessionID)
		fmt.Fprintf(w, "event: endpoint\ndata: %s\r\n\r\n", messageURL)
		flusher.Flush()

		// Start idle timeout monitor for this session
		idleTimer := time.NewTimer(SessionIdleTimeout)
		defer idleTimer.Stop()

		for {
			select {
			case <-r.Context().Done():
				return
			case <-idleTimer.C:
				if session.isExpired() {
					slog.Info("Closing idle SSE session", "sessionID", sessionID)
					sessions.Delete(sessionID)
					return
				}
				idleTimer.Reset(SessionIdleTimeout)
			}
		}
	}
}

// jsonRPCMessage is a minimal parse target for logging the method name.
type jsonRPCMessage struct {
	Method string `json:"method"`
}

func mcpPostHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()

		var rawMessage json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&rawMessage); err != nil {
			writeJSONRPCError(w, nil, 400, "Parse error")
			return
		}

		// Extract method name for audit logging (best-effort, ignore parse errors)
		var msgMeta jsonRPCMessage
		_ = json.Unmarshal(rawMessage, &msgMeta)
		rpcMethod := msgMeta.Method
		if rpcMethod == "" {
			rpcMethod = "unknown"
		}

		sessionID := r.URL.Query().Get("sessionId")
		tenantIDs := resolveTenantIDs(r)
		var session *sseSession

		if sessionID != "" {
			si, ok := sessions.Load(sessionID)
			if !ok {
				http.Error(w, "session not found", http.StatusNotFound)
				return
			}
			session = si.(*sseSession)

			if len(tenantIDs) == 0 {
				tenantIDs = session.getTenantIDs()
			}
		}

		if len(tenantIDs) == 0 {
			http.Error(w, "X-Tenant-ID header is required", http.StatusBadRequest)
			return
		}

		var mcpServer *server.MCPServer
		var err error
		if session != nil {
			mcpServer, err = session.ensureCompositeServer(tenantIDs)
		} else {
			mcpServer, err = createCompositeServer(tenantIDs)
		}
		if err != nil {
			http.Error(w, "Failed to create MCP server", http.StatusInternalServerError)
			return
		}

		// Bound this single request/response cycle instead of relying on
		// http.Server.WriteTimeout (which would also cap the unrelated,
		// long-lived GET /mcp SSE connection this response is written
		// through). If HandleMessage hangs (e.g. a slow downstream tool),
		// this context expires and callers get a clean timeout instead of
		// a connection that hangs forever.
		ctx, cancel := context.WithTimeout(r.Context(), postHandlerTimeout)
		defer cancel()

		// Inject primary tenantID into context for backward compat with
		// single-tenant tool handlers (composite handlers use their own closure).
		ctx = context.WithValue(ctx, httpclient.TenantIDKey, tenantIDs[0])

		if session != nil {
			ctx = mcpServer.WithContext(ctx, server.NotificationContext{
				ClientID:  sessionID,
				SessionID: sessionID,
			})
		}

		response := mcpServer.HandleMessage(ctx, rawMessage)
		if response != nil {
			if session != nil {
				eventData, _ := json.Marshal(response)
				session.writeMessage(eventData)
				w.WriteHeader(http.StatusAccepted)
				slog.LogAttrs(ctx, slog.LevelInfo, "jsonrpc_call",
					slog.String("method", rpcMethod),
					slog.String("session_id", sessionID),
					slog.Any("tenant_ids", tenantIDs),
					slog.Int64("duration_ms", time.Since(start).Milliseconds()),
				)
				return
			}

			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(response)
			slog.LogAttrs(ctx, slog.LevelInfo, "jsonrpc_call",
				slog.String("method", rpcMethod),
				slog.Any("tenant_ids", tenantIDs),
				slog.Int64("duration_ms", time.Since(start).Milliseconds()),
			)
			return
		}

		w.WriteHeader(http.StatusAccepted)
		slog.LogAttrs(ctx, slog.LevelInfo, "jsonrpc_call",
			slog.String("method", rpcMethod),
			slog.String("session_id", sessionID),
			slog.Any("tenant_ids", tenantIDs),
			slog.Int64("duration_ms", time.Since(start).Milliseconds()),
		)
	}
}

func manifestProxyHandler(w http.ResponseWriter, r *http.Request) {
	tenantIDs := resolveTenantIDs(r)
	// Use the first tenant for manifest (backward compat)
	tenantID := ""
	if len(tenantIDs) > 0 {
		tenantID = tenantIDs[0]
	}
	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(cfg)
}

func writeJSONRPCError(w http.ResponseWriter, id any, code int, message string) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"jsonrpc": "2.0", "error": map[string]any{"code": code, "message": message}, "id": id,
	})
}

// ── Debug Handlers ──

func debugPlaygroundHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		w.Write([]byte(playgroundHTML))
	}
}

type sessionInfo struct {
	SessionID string   `json:"session_id"`
	TenantIDs []string `json:"tenant_ids"`
}

func debugSessionsHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tenantIDs := resolveTenantIDs(r)
		var result []sessionInfo
		sessions.Range(func(key, value any) bool {
			s := value.(*sseSession)
			sTenantIDs := s.getTenantIDs()
			// If no tenant filter, show all sessions; otherwise filter by tenant
			if len(tenantIDs) == 0 || sliceContainsAny(sTenantIDs, tenantIDs) {
				result = append(result, sessionInfo{
					SessionID: key.(string),
					TenantIDs: sTenantIDs,
				})
			}
			return true
		})
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		json.NewEncoder(w).Encode(map[string]any{"sessions": result})
	}
}

// sliceContainsAny returns true if a contains any element from b.
func sliceContainsAny(a, b []string) bool {
	for _, va := range a {
		for _, vb := range b {
			if va == vb {
				return true
			}
		}
	}
	return false
}

func debugConfigHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Reuse the same logic as manifestProxyHandler
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		manifestProxyHandler(w, r)
	}
}

// resolveTenantIDs parses X-Tenant-ID header as a comma-separated list.
// Returns a slice (never nil). Supports backward compat with single tenant.
func resolveTenantIDs(r *http.Request) []string {
	tenantID := r.Header.Get("X-Tenant-ID")
	if tenantID == "" {
		tenantID = r.URL.Query().Get("tenant")
	}
	if tenantID == "" {
		tenantID = r.URL.Query().Get("tenat")
	}

	parts := strings.Split(tenantID, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			result = append(result, p)
		}
	}
	return result
}
