package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/mark3labs/mcp-go/server"

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
const postHandlerTimeout = 25 * time.Second

// sseSession represents one long-lived SSE connection (opened via GET) and
// the tenant-scoped MCP server state associated with it.
//
// mu guards mcpServer, tenantID, and all writes to writer/flusher.
// Both are mutated from mcpPostHandler (on every POST) and read from the
// same handler, and in principle a client could fire concurrent tool
// calls for the same session — the Go http.ResponseWriter is not safe for
// concurrent writes, so every write MUST go through this lock.
type sseSession struct {
	mu        sync.Mutex
	writer    http.ResponseWriter
	flusher   http.Flusher
	done      chan struct{}
	tenantID  string
	mcpServer *server.MCPServer
}

// writeMessage safely writes a JSON-RPC "message" SSE event to this
// session's underlying connection.
func (s *sseSession) writeMessage(eventData []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fmt.Fprintf(s.writer, "event: message\ndata: %s\n\n", eventData)
	s.flusher.Flush()
}

// ensureServerForTenant lazily creates (or re-creates, on tenant switch)
// the per-tenant MCP server for this session. Guarded by mu so concurrent
// POSTs on the same session can't race on server creation.
func (s *sseSession) ensureServerForTenant(tenantID string) (*server.MCPServer, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.mcpServer != nil && s.tenantID == tenantID {
		return s.mcpServer, nil
	}

	slog.Info("Initializing MCP server for tenant in session", "tenantID", tenantID)
	mcpServer, err := createServerForTenant(tenantID)
	if err != nil {
		return nil, err
	}
	s.mcpServer = mcpServer
	s.tenantID = tenantID
	return mcpServer, nil
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
func buildHTTPServer(r http.Handler, port string) *http.Server {
	return &http.Server{
		Addr:              ":" + port,
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
		// WriteTimeout intentionally omitted — see doc comment above.
	}
}

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

func buildRouter() *chi.Mux {
	sessions := &sync.Map{}
	r := chi.NewRouter()

	// Recover from panics in any handler (e.g. a misbehaving tool) so one
	// bad request can't take down the process, and so we get a proper
	// stack trace in the logs instead of a silently dropped connection.
	r.Use(chimiddleware.Recoverer)

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
	r.Get("/debug/sessions", debugSessionsHandler(sessions))
	r.Get("/debug/config", debugConfigHandler())
	r.Get("/mcp", sseHandler(sessions))
	r.Get("/sse", sseHandler(sessions))
	r.Get("/", sseHandler(sessions))
	mcpPost := mcpPostHandler(sessions)
	r.Post("/mcp/message", mcpPost)
	r.Post("/mcp", mcpPost)
	r.Post("/message", mcpPost)
	r.Post("/", mcpPost)
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
		w.Header().Set("Access-Control-Allow-Origin", "*")

		sessionID := uuid.New().String()
		session := &sseSession{
			writer:   w,
			flusher:  flusher,
			done:     make(chan struct{}),
			tenantID: r.Header.Get("X-Tenant-ID"),
		}
		sessions.Store(sessionID, session)
		defer func() {
			sessions.Delete(sessionID)
			slog.Info("MCP session closed", "sessionID", sessionID)
		}()

		messageURL := fmt.Sprintf("http://%s/mcp/message?sessionId=%s", r.Host, sessionID)
		fmt.Fprintf(w, "event: endpoint\ndata: %s\r\n\r\n", messageURL)
		flusher.Flush()
		<-r.Context().Done()
	}
}

func mcpPostHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := r.URL.Query().Get("sessionId")
		if sessionID == "" {
			http.Error(w, "sessionId is required", http.StatusBadRequest)
			return
		}

		si, ok := sessions.Load(sessionID)
		if !ok {
			http.Error(w, "session not found", http.StatusNotFound)
			return
		}
		session := si.(*sseSession)

		tenantID := r.Header.Get("X-Tenant-ID")
		if tenantID == "" {
			session.mu.Lock()
			tenantID = session.tenantID
			session.mu.Unlock()
		}
		if tenantID == "" {
			http.Error(w, "X-Tenant-ID header is required", http.StatusBadRequest)
			return
		}

		mcpServer, err := session.ensureServerForTenant(tenantID)
		if err != nil {
			http.Error(w, "Failed to create MCP server", http.StatusInternalServerError)
			return
		}

		var rawMessage json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&rawMessage); err != nil {
			writeJSONRPCError(w, nil, 400, "Parse error")
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

		// Inject tenantID into context so tool handlers in tools.go
		// can read it via ctx.Value(httpclient.TenantIDKey) and pass
		// it to data-service for multi-tenant isolation.
		ctx = context.WithValue(ctx, httpclient.TenantIDKey, tenantID)

		ctx = mcpServer.WithContext(ctx, server.NotificationContext{
			ClientID:  sessionID,
			SessionID: sessionID,
		})

		response := mcpServer.HandleMessage(ctx, rawMessage)
		if response != nil {
			eventData, _ := json.Marshal(response)
			session.writeMessage(eventData)
		}
		w.WriteHeader(http.StatusAccepted)
	}
}

func manifestProxyHandler(w http.ResponseWriter, r *http.Request) {
	tenantID := r.Header.Get("X-Tenant-ID")
	if tenantID == "" {
		tenantID = r.URL.Query().Get("tenant")
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
		w.Write([]byte(playgroundHTML))
	}
}

type sessionInfo struct {
	SessionID string `json:"session_id"`
	TenantID  string `json:"tenant_id"`
}

func debugSessionsHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var result []sessionInfo
		sessions.Range(func(key, value any) bool {
			s := value.(*sseSession)
			s.mu.Lock()
			tenantID := s.tenantID
			s.mu.Unlock()
			result = append(result, sessionInfo{
				SessionID: key.(string),
				TenantID:  tenantID,
			})
			return true
		})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"sessions": result})
	}
}

func debugConfigHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Reuse the same logic as manifestProxyHandler
		manifestProxyHandler(w, r)
	}
}
