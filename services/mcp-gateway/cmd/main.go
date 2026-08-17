// Package main is the MCP Gateway server.
//
// HTTP routes served:
//
//	GET/POST/DELETE /mcp                  -> standard Streamable HTTP MCP
//
// HTTP routes called (to upstream services):
//
//	createServerForTenant() / createCompositeServer():
//	  FetchConfigWithTenant() -> data-service:GET /mcp/manifest (load MCP config)
//	makeHandler -> client.Call() -> data-service:GET /{endpoint} (data query)
//	ragClient.SearchDocuments() -> rag:POST /search
//	ragClient.ListDocuments()   -> rag:POST /documents/list
//	ragClient.GetRagContext()   -> rag:POST /context
//
// Config env: DATA_SERVICE_URL, RAG_SERVICE_URL
package main

import (
	"context"
	"encoding/json"
	"errors"
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
	"github.com/mark3labs/mcp-go/server"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/trash2bin/helperium/helperium-go/pkg/tracing"
	"github.com/trash2bin/helperium/mcp-gateway/internal/httpclient"
	gwserver "github.com/trash2bin/helperium/mcp-gateway/internal/server"
	"github.com/trash2bin/helperium/mcp-gateway/internal/tools"
)

var globalClient *httpclient.Client

// Session management constants
// Can be overridden with environment variables
var (
	// SessionIdleTimeout configures mcp-go transport-managed Streamable HTTP
	// session expiry. Can be overridden with MCP_SESSION_IDLE_TIMEOUT.
	SessionIdleTimeout = func() time.Duration {
		if v := os.Getenv("MCP_SESSION_IDLE_TIMEOUT"); v != "" {
			if d, err := time.ParseDuration(v); err == nil {
				return d
			}
		}
		return 5 * time.Minute // default
	}()

	// MaxStreamableTenantScopes bounds cached stateful Streamable HTTP
	// handlers, whose tool manifest is unique for each tenant set.
	// Can be overridden with MCP_MAX_STREAMABLE_TENANT_SCOPES.
	MaxStreamableTenantScopes = func() int {
		if v := os.Getenv("MCP_MAX_STREAMABLE_TENANT_SCOPES"); v != "" {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				return n
			}
		}
		return 256
	}()
)

// streamableTenantRegistry keeps a separate standard Streamable HTTP MCP
// transport for each already-resolved tenant set. Tool manifests are tenant
// specific, so one global MCPServer cannot safely serve all tenant scopes.
var errMaxStreamableTenantScopes = errors.New("maximum streamable tenant scopes reached")

type streamableTenantRegistry struct {
	mu       sync.Mutex
	handlers map[string]http.Handler
	max      int
}

func newStreamableTenantRegistry() *streamableTenantRegistry {
	return &streamableTenantRegistry{
		handlers: make(map[string]http.Handler),
		max:      MaxStreamableTenantScopes,
	}
}

func (registry *streamableTenantRegistry) handlerFor(tenantIDs []string) (http.Handler, error) {
	tenantKey := strings.Join(tenantIDs, ",")
	registry.mu.Lock()
	defer registry.mu.Unlock()

	if handler, ok := registry.handlers[tenantKey]; ok {
		return handler, nil
	}
	if len(registry.handlers) >= registry.max {
		return nil, errMaxStreamableTenantScopes
	}
	mcpServer, err := createCompositeServer(tenantIDs)
	if err != nil {
		return nil, err
	}

	primaryTenantID := tenantIDs[0]
	handler := server.NewStreamableHTTPServer(
		mcpServer,
		server.WithEndpointPath("/mcp"),
		server.WithStateful(true),
		server.WithSessionIdleTTL(SessionIdleTimeout),
		server.WithStreamableHTTPLogger(slog.Default()),
		server.WithHTTPContextFunc(func(ctx context.Context, _ *http.Request) context.Context {
			return context.WithValue(ctx, httpclient.TenantIDKey, primaryTenantID)
		}),
	)
	registry.handlers[tenantKey] = handler
	return handler, nil
}

func (registry *streamableTenantRegistry) serveHTTP(w http.ResponseWriter, r *http.Request) {
	tenantIDs := resolveTenantIDs(r)
	if len(tenantIDs) == 0 {
		http.Error(w, "X-Tenant-ID header is required", http.StatusBadRequest)
		return
	}
	handler, err := registry.handlerFor(tenantIDs)
	if err != nil {
		if errors.Is(err, errMaxStreamableTenantScopes) {
			http.Error(w, "too many active Streamable HTTP tenant scopes", http.StatusServiceUnavailable)
			return
		}
		slog.Error("Failed to create Streamable HTTP MCP server", "tenant_ids", tenantIDs, "error", err)
		http.Error(w, "Failed to create MCP server", http.StatusInternalServerError)
		return
	}
	handler.ServeHTTP(w, r)
}

func main() {
	devMode := os.Getenv("MCP_DEV") == "true"
	logLevel := slog.LevelInfo
	if devMode {
		logLevel = slog.LevelDebug
	}
	logHandler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel})
	slog.SetDefault(slog.New(logHandler))

	slog.Info("prometheus metrics initialized")

	tracing.Setup("mcp-gateway")
	defer tracing.Shutdown()

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

// buildHTTPServer configures server-level timeouts. WriteTimeout remains
// unset because Streamable HTTP may keep a response stream open while the
// transport manages MCP session/event delivery. ReadHeaderTimeout protects
// against slow/stalled request headers (slowloris-style attacks).
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

// createServerForTenant creates a per-tenant MCP server with unprefixed tools.
func createServerForTenant(tenantID string) (*server.MCPServer, error) {
	slog.Info("Fetching config for tenant", "tenantID", tenantID)
	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch config", "tenantID", tenantID, "error", err)
		return nil, err
	}
	slog.Info("Config fetched, creating server", "tenantID", tenantID)
	mcpServer := server.NewMCPServer("helperium", "1.0.0")
	slog.Info("Creating registry", "tenantID", tenantID)
	registry := tools.NewRegistry(cfg)
	slog.Info("Registering tools", "tenantID", tenantID)
	registry.RegisterAll(mcpServer)
	slog.Info("MCP server ready", "tenantID", tenantID)
	return mcpServer, nil
}

// createCompositeServer creates a composite MCP server for multiple tenants.
// Single tenant → standard mode (no prefix).
// Multiple tenants → all tools registered with "{tenantID}__" prefix.
func createCompositeServer(tenantIDs []string) (*server.MCPServer, error) {
	// Single tenant: unprefixed tools.
	if len(tenantIDs) == 1 {
		return createServerForTenant(tenantIDs[0])
	}

	slog.Info("Creating composite MCP server", "tenants", tenantIDs)
	composite := server.NewMCPServer("helperium", "1.0.0")

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
		if r.URL.Path == "/health" || r.URL.Path == "/metrics" {
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
	streamableHandlers := newStreamableTenantRegistry()
	r := chi.NewRouter()

	// Recover from panics in any handler (e.g. a misbehaving tool) so one
	// bad request can't take down the process, and so we get a proper
	// stack trace in the logs instead of a silently dropped connection.
	r.Use(chimiddleware.Recoverer)

	// OpenTelemetry tracing middleware
	r.Use(tracing.Middleware)

	// Auth middleware — check Authorization: Bearer <token> on all routes
	// except /health. If MCP_API_KEY env is empty, auth is skipped.
	r.Use(authMiddleware)

	// Global request logger to debug routing issues
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			traceID := tracing.TraceIDFromContext(r.Context())
			slog.Info("INCOMING REQUEST", "method", r.Method, "path", r.URL.Path, "tenant", r.Header.Get("X-Tenant-ID"), "trace_id", traceID)
			next.ServeHTTP(w, r)
		})
	})

	r.Get("/health", healthHandler())
	r.Handle("/metrics", promhttp.Handler())
	r.Get("/docs", gwserver.SwaggerHandler())
	r.Get("/openapi.json", gwserver.OpenAPIHandler())
	r.Get("/config", debugConfigHandler())
	r.Get("/debug/config", debugConfigHandler())
	// One standard MCP endpoint for all Streamable HTTP methods. Rate limit
	// applies to GET, POST and DELETE so transport sessions cannot bypass it.
	r.Group(func(r chi.Router) {
		r.Use(mcpRateLimitMiddleware())
		r.HandleFunc("/mcp", streamableHandlers.serveHTTP)
	})

	r.Get("/mcp/manifest", manifestProxyHandler)
	r.Get("/mcp/tools/mapping", mappingHandler)
	r.Get("/mcp/schema", schemaProxyHandler)
	return r
}

func healthHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
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

func mappingHandler(w http.ResponseWriter, r *http.Request) {
	tenantIDs := resolveTenantIDs(r)
	tenantID := ""
	if len(tenantIDs) > 0 {
		tenantID = tenantIDs[0]
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")

	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch config for mapping", "tenantID", tenantID, "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	mapping := make(map[string]string, len(cfg.MCPTools))
	for _, mt := range cfg.MCPTools {
		display := mt.DisplayName
		if display == "" {
			display = mt.Name
		}
		mapping[mt.Name] = display
	}

	json.NewEncoder(w).Encode(mapping)
}

// schemaProxyHandler прокидывает запрос /mcp/schema в data-service.
func schemaProxyHandler(w http.ResponseWriter, r *http.Request) {
	tenantIDs := resolveTenantIDs(r)
	tenantID := ""
	if len(tenantIDs) > 0 {
		tenantID = tenantIDs[0]
	}

	data, err := globalClient.FetchSchemaWithTenant(tenantID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

// ── Debug Handlers ──

func debugConfigHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Reuse the same logic as manifestProxyHandler
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		manifestProxyHandler(w, r)
	}
}

// resolveTenantIDs parses the server-to-server X-Tenant-ID header as a
// comma-separated list. Query parameters are deliberately not accepted because
// tenant scope must not be selected through an alternate public input surface.
func resolveTenantIDs(r *http.Request) []string {
	tenantID := r.Header.Get("X-Tenant-ID")

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
