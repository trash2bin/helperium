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
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"regexp"
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

	// MaxTenantsPerScope limits one composite tenant header. Without this bound,
	// one request could synchronously fetch and register an unbounded number of
	// tenant manifests before the scope-cache limit is reached.
	// Can be overridden with MCP_MAX_TENANTS_PER_SCOPE.
	MaxTenantsPerScope = func() int {
		if v := os.Getenv("MCP_MAX_TENANTS_PER_SCOPE"); v != "" {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				return n
			}
		}
		return 8
	}()
)

// streamableTenantRegistry keeps a separate standard Streamable HTTP MCP
// transport for each already-resolved tenant set. Tool manifests are tenant
// specific, so one global MCPServer cannot safely serve all tenant scopes.
var (
	errMaxStreamableTenantScopes = errors.New("maximum streamable tenant scopes reached")
	errTooManyTenantsPerScope    = errors.New("maximum tenants per MCP scope reached")
	errDuplicateTenantInScope    = errors.New("duplicate tenant ID in MCP scope")
	errInvalidTenantIDInScope    = errors.New("invalid tenant ID in MCP scope")
	tenantIDPattern              = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)
)

type streamableTenantRegistry struct {
	mu         sync.Mutex
	handlers   map[string]http.Handler
	lastAccess map[string]time.Time
	max        int
}

func newStreamableTenantRegistry() *streamableTenantRegistry {
	return &streamableTenantRegistry{
		handlers:   make(map[string]http.Handler),
		lastAccess: make(map[string]time.Time),
		max:        MaxStreamableTenantScopes,
	}
}

// registryIdleEvictionTTL bounds how long a cached Streamable HTTP tenant
// scope may stay unused before it becomes evictable at capacity. It must stay
// comfortably above SessionIdleTimeout so live MCP sessions are never evicted;
// zero disables idle eviction.
var registryIdleEvictionTTL = 15 * time.Minute

// nowFunc is the clock used for registry idle-eviction timestamps. It is a
// package-level variable so tests can stub it deterministically.
var nowFunc = time.Now

// evictIdleScopes removes cached scopes whose last access is older than
// registryIdleEvictionTTL and returns whether at least one slot was freed.
// Active scopes (accessed within the TTL) are never evicted: their stateful
// MCP sessions would break. Callers must hold registry.mu.
func (registry *streamableTenantRegistry) evictIdleScopes(now time.Time) bool {
	if registryIdleEvictionTTL <= 0 {
		return false
	}
	evicted := false
	for key, last := range registry.lastAccess {
		if now.Sub(last) > registryIdleEvictionTTL {
			delete(registry.handlers, key)
			delete(registry.lastAccess, key)
			evicted = true
		}
	}
	return evicted
}

func (registry *streamableTenantRegistry) handlerFor(tenantIDs []string) (http.Handler, error) {
	tenantKey := strings.Join(tenantIDs, ",")

	// Do not hold the registry mutex while loading manifests from data-service.
	// A slow or unavailable tenant must not block already-cached scopes.
	registry.mu.Lock()
	if handler, ok := registry.handlers[tenantKey]; ok {
		registry.lastAccess[tenantKey] = nowFunc()
		registry.mu.Unlock()
		return handler, nil
	}
	if len(registry.handlers) >= registry.max {
		if registry.evictIdleScopes(nowFunc()) {
			// Fall through: a slot opened up.
		} else {
			registry.mu.Unlock()
			return nil, errMaxStreamableTenantScopes
		}
	}
	registry.lastAccess[tenantKey] = nowFunc()
	registry.mu.Unlock()

	mcpServer, err := createCompositeServer(tenantIDs)
	if err != nil {
		return nil, err
	}

	primaryTenantID := tenantIDs[0]
	candidate := server.NewStreamableHTTPServer(
		mcpServer,
		server.WithEndpointPath("/mcp"),
		server.WithStateful(true),
		server.WithSessionIdleTTL(SessionIdleTimeout),
		server.WithStreamableHTTPLogger(slog.Default()),
		server.WithHTTPContextFunc(func(ctx context.Context, _ *http.Request) context.Context {
			return context.WithValue(ctx, httpclient.TenantIDKey, primaryTenantID)
		}),
	)

	// Another request may have built the same scope while its manifest was
	// loading. Reuse that canonical handler and discard the duplicate candidate.
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if handler, ok := registry.handlers[tenantKey]; ok {
		return handler, nil
	}
	if len(registry.handlers) >= registry.max {
		return nil, errMaxStreamableTenantScopes
	}
	registry.handlers[tenantKey] = candidate
	return candidate, nil
}

func (registry *streamableTenantRegistry) serveHTTP(w http.ResponseWriter, r *http.Request) {
	tenantIDs := resolveTenantIDs(r)
	if len(tenantIDs) == 0 {
		http.Error(w, "X-Tenant-ID header is required", http.StatusBadRequest)
		return
	}
	if err := validateTenantScope(tenantIDs); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
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
	if err := validateStartupConfiguration(); err != nil {
		slog.Error("invalid MCP gateway configuration", "error", err)
		os.Exit(1)
	}

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

// newMCPServer creates an MCP server whose lifecycle hooks maintain the
// Streamable HTTP active-session gauge for one resolved tenant scope.
func newMCPServer(tenantScope string) *server.MCPServer {
	hooks := &server.Hooks{}
	hooks.AddOnRegisterSession(func(context.Context, server.ClientSession) {
		mcpSessionsActive.WithLabelValues(tenantScope).Inc()
	})
	hooks.AddOnUnregisterSession(func(context.Context, server.ClientSession) {
		mcpSessionsActive.WithLabelValues(tenantScope).Dec()
	})
	return server.NewMCPServer("helperium", "1.0.0", server.WithHooks(hooks))
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
	mcpServer := newMCPServer(tenantID)
	slog.Info("Creating registry", "tenantID", tenantID)
	registry := tools.NewTenantRegistry(cfg, tenantID)
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
	composite := newMCPServer(strings.Join(tenantIDs, ","))

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

// validateStartupConfiguration protects every non-development deployment from
// accidentally exposing the gateway with auth disabled. Local development must
// opt out explicitly through MCP_DEV=true; MCP_REQUIRE_AUTH=true always requires
// a non-empty key, including in development.
func validateStartupConfiguration() error {
	authRequired := os.Getenv("MCP_REQUIRE_AUTH") == "true"
	if !authRequired && os.Getenv("MCP_DEV") != "true" {
		return errors.New("MCP_REQUIRE_AUTH=true is required unless MCP_DEV=true")
	}
	if authRequired && strings.TrimSpace(os.Getenv("MCP_API_KEY")) == "" {
		return errors.New("MCP_REQUIRE_AUTH=true requires a non-empty MCP_API_KEY")
	}
	return nil
}

// originMiddleware implements the Streamable HTTP DNS-rebinding defence. Native
// service clients normally omit Origin; browser-originated requests must match
// the configured comma-separated MCP_ALLOWED_ORIGINS allow-list exactly.
func originMiddleware(next http.Handler) http.Handler {
	allowed := make(map[string]struct{})
	for _, value := range strings.Split(os.Getenv("MCP_ALLOWED_ORIGINS"), ",") {
		if origin := strings.TrimSpace(value); origin != "" {
			allowed[origin] = struct{}{}
		}
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" {
			if _, ok := allowed[origin]; !ok {
				http.Error(w, "Origin is not allowed", http.StatusForbidden)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// validateTenantScope constrains composite setup work before any upstream
// manifest requests. Ordering is preserved because it determines the exposed
// tool names, while duplicate IDs have no valid composite meaning.
func validateTenantScope(tenantIDs []string) error {
	if len(tenantIDs) > MaxTenantsPerScope {
		return errTooManyTenantsPerScope
	}
	seen := make(map[string]struct{}, len(tenantIDs))
	for _, tenantID := range tenantIDs {
		// Tenant IDs are header-controlled lookup keys and cache keys. Keep the
		// accepted contract intentionally narrow so malformed values cannot reach
		// manifest routing or produce an internal error response.
		if !tenantIDPattern.MatchString(tenantID) {
			return errInvalidTenantIDInScope
		}
		if _, duplicate := seen[tenantID]; duplicate {
			return errDuplicateTenantInScope
		}
		seen[tenantID] = struct{}{}
	}
	return nil
}

// requiredSingleTenant resolves metadata routes that are meaningful only for one
// manifest. Unlike the old fallback, an absent X-Tenant-ID cannot silently read
// a default scope and a composite scope must use the MCP tool manifest instead.
func requiredSingleTenant(w http.ResponseWriter, r *http.Request) (string, bool) {
	tenantIDs := resolveTenantIDs(r)
	if len(tenantIDs) == 0 {
		http.Error(w, "X-Tenant-ID header is required", http.StatusBadRequest)
		return "", false
	}
	if err := validateTenantScope(tenantIDs); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return "", false
	}
	if len(tenantIDs) != 1 {
		http.Error(w, "metadata endpoints require exactly one X-Tenant-ID", http.StatusBadRequest)
		return "", false
	}
	return tenantIDs[0], true
}

// authMiddleware проверяет Authorization: Bearer <token> на всех маршрутах,
// кроме /health. Пустой MCP_API_KEY может пройти сюда только после явного
// MCP_DEV=true opt-out: validateStartupConfiguration запрещает такой запуск вне
// local development и запрещает пустой ключ при MCP_REQUIRE_AUTH=true.
func authMiddleware(next http.Handler) http.Handler {
	apiKey := os.Getenv("MCP_API_KEY")
	if apiKey == "" {
		// Only explicit MCP_DEV=true can reach this branch. Any non-development
		// launch fails before router construction unless auth has a non-empty key.
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
		// Constant-time compare: token equality must not be observable through
		// response timing. Length mismatch returns immediately inside
		// ConstantTimeCompare, which is the standard accepted behavior.
		if subtle.ConstantTimeCompare([]byte(token), []byte(apiKey)) != 1 {
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

	// Reject browser-originated requests unless their Origin is explicitly
	// allow-listed. Service-to-service clients do not send Origin.
	r.Use(originMiddleware)

	// Auth middleware — check Authorization: Bearer <token> on all routes
	// except /health. Startup validation makes auth mandatory outside explicit dev.
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
	tenantID, ok := requiredSingleTenant(w, r)
	if !ok {
		return
	}
	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch manifest from upstream",
			"handler", "manifestProxyHandler", "tenantID", tenantID, "error", err)
		writeUpstreamUnavailable(w)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(cfg)
}

func mappingHandler(w http.ResponseWriter, r *http.Request) {
	tenantID, ok := requiredSingleTenant(w, r)
	if !ok {
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")

	cfg, err := globalClient.FetchConfigWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch config for mapping",
			"handler", "mappingHandler", "tenantID", tenantID, "error", err)
		writeUpstreamUnavailable(w)
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
	tenantID, ok := requiredSingleTenant(w, r)
	if !ok {
		return
	}

	data, err := globalClient.FetchSchemaWithTenant(tenantID)
	if err != nil {
		slog.Error("Failed to fetch schema from upstream",
			"handler", "schemaProxyHandler", "tenantID", tenantID, "error", err)
		writeUpstreamUnavailable(w)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

// writeUpstreamUnavailable answers metadata routes with a generic retryable
// error. It never forwards err.Error() from the upstream client: those errors
// embed the internal DATA_SERVICE_URL, transport detail and upstream response
// bodies, which must not reach callers.
func writeUpstreamUnavailable(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusInternalServerError)
	json.NewEncoder(w).Encode(map[string]string{
		"error":   "upstream_unavailable",
		"message": "Upstream metadata is temporarily unavailable, please retry.",
	})
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
