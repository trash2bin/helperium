// Package server provides the admin-dashboard HTTP server.
//
// HTTP routes called (proxy to upstream services):
//   proxyGetToDataService()  -> data-service:GET /admin/{path}  (tenant CRUD, config, tools)
//   proxyPostToDataService() -> data-service:POST /admin/{path} (create tenant, rewrite config)
//   proxyGetToApiService()   -> api-service:GET /api/agents/{name}  (get agent abuse config)
//   proxyPutToApiService()   -> api-service:PUT /api/agents/{name}  (update agent abuse config)
//   notifyApiServiceReload() -> api-service:POST /admin/abuse-config/reload (reload abuse)
//   RagClient.GetConfig()    -> rag:GET /admin/config  (get RAG config)
//   RagClient.UpdateConfig() -> rag:PUT /admin/config  (update RAG config)
//   RagClient.GetStats()     -> rag:GET /admin/stats   (get RAG stats)
package server

import (
	"embed"
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

	"github.com/trash2bin/helperium/helperium-go/pkg/metrics"
	"github.com/trash2bin/helperium/helperium-go/pkg/tracing"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Options для создания сервера.
type Options struct {
	Addr       string
	DataSvcURL string
	RagSvcURL  string
	ApiSvcURL  string
	AdminToken string
	DataDir    string
}

// responseWriter wraps http.ResponseWriter to capture the status code.
type responseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.statusCode = code
	rw.ResponseWriter.WriteHeader(code)
}

// Server — admin dashboard HTTP сервер.
type Server struct {
	opts       Options
	dataClient *DataServiceClient
	ragClient  *RagClient
	abuseStore *AbuseStore
	mu         sync.RWMutex
}

//go:embed static/*
var staticFS embed.FS

// New создаёт новый Server.
func New(opts Options) *Server {
	return &Server{
		opts:       opts,
		dataClient: NewDataServiceClient(opts.DataSvcURL, opts.AdminToken),
		ragClient:  NewRagClient(opts.RagSvcURL, opts.AdminToken),
		abuseStore: NewAbuseStore(opts.DataDir),
	}
}

// Router собирает chi роутер.
func (s *Server) Router() chi.Router {
	r := chi.NewRouter()

	// OpenTelemetry tracing middleware
	r.Use(tracing.Middleware)

	// Middleware
	// Structured logging + Prometheus metrics
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			wr := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}
			next.ServeHTTP(wr, r)
			duration := time.Since(start).Milliseconds()
			traceID := tracing.TraceIDFromContext(r.Context())
			slog.Info("request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", wr.statusCode,
				"duration_ms", duration,
				"trace_id", traceID,
			)
			metrics.AdminRequestsTotal.WithLabelValues(r.URL.Path, strconv.Itoa(wr.statusCode)).Inc()
		})
	})
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(corsMiddleware)
	r.Use(authMiddleware(s.opts.AdminToken))

	// Prometheus metrics (no auth needed)
	r.Handle("/metrics", promhttp.Handler())

	// Static frontend
	r.Handle("/*", s.staticHandler())

	// Health check (no auth)
	r.Get("/health", s.healthHandler)

	// i18n translations (no auth — loaded before login)
	r.Get("/i18n.json", s.i18nHandler)

	// API
	r.Route("/api", func(r chi.Router) {
		r.Get("/health", s.healthHandler)

		// Dashboard summary
		r.Get("/dashboard", s.dashboardHandler)

		// DB connection
		r.Post("/db/test", s.dbTestHandler)

		// Tenant CRUD
		r.Get("/tenants", s.tenantsListHandler)
		r.Post("/tenants", s.tenantCreateHandler)
		r.Get("/tenants/{id}", s.tenantGetHandler)
		r.Delete("/tenants/{id}", s.tenantDeleteHandler)
		r.Post("/tenants/upload-sqlite", s.tenantUploadSQLiteHandler)

		// Tenant config
		r.Get("/tenants/{id}/config", s.tenantConfigGetHandler)
		r.Put("/tenants/{id}/config", s.tenantConfigPutHandler)
		r.Post("/tenants/{id}/introspect", s.tenantIntrospectHandler)

		// Write-tool approval (per-tenant — uses X-Tenant-ID header set by caller)
		r.Get("/tenants/{id}/tools/pending", s.toolsPendingHandler)
		r.Post("/tenants/{id}/tools/{toolName}/approve", s.toolsApproveHandler)

		// MCP manifest (all tools for a tenant)
		r.Get("/tenants/{id}/manifest", s.tenantManifestHandler)

		// RAG
		r.Get("/rag/health", s.ragHealthHandler)
		r.Get("/rag/config", s.ragConfigGetHandler)
		r.Put("/rag/config", s.ragConfigPutHandler)
		r.Get("/rag/stats", s.ragStatsHandler)
		r.Post("/rag/documents/list", s.ragDocListHandler)
		r.Post("/rag/documents/import", s.ragDocImportHandler)
		r.Post("/rag/documents/upload", s.ragDocUploadHandler)
		r.Post("/rag/documents/delete", s.ragDocDeleteHandler)

		// Agent CRUD (proxy to api-service)
		r.Get("/agents", s.agentListHandler)
		r.Post("/agents", s.agentCreateHandler)
		r.Get("/agents/{name}", s.agentGetHandler)
		r.Put("/agents/{name}", s.agentUpdateHandler)
		r.Delete("/agents/{name}", s.agentDeleteHandler)

		// LLM Provider Fallback (proxy to api-service)
		r.Get("/llm-config", s.llmConfigGetHandler)
		r.Get("/llm-providers", s.llmProvidersListHandler)
		r.Post("/llm-providers", s.llmProvidersAddHandler)
		r.Get("/llm-providers/{name}", s.llmProvidersGetHandler)
		r.Put("/llm-providers/{name}", s.llmProvidersUpdateHandler)
		r.Delete("/llm-providers/{name}", s.llmProvidersDeleteHandler)
		r.Post("/llm-providers/{name}/toggle", s.llmProvidersToggleHandler)
		r.Get("/llm-provider-list", s.llmProviderListHandler)

		// Anti-abuse / rate limit settings
		r.Get("/abuse-settings", s.abuseSettingsGetHandler)
		r.Put("/abuse-settings", s.abuseSettingsPutHandler)
		r.Get("/agents/{name}/abuse", s.agentAbuseGetHandler)
		r.Put("/agents/{name}/abuse", s.agentAbusePutHandler)
		r.Post("/abuse-preset/{preset}", s.abusePresetHandler)
		r.Get("/emergency-status", s.emergencyStatusHandler)
	})

	return r
}

// ── Middleware ──

// corsMiddleware разрешает CORS для dev-режима.
// Origin читается из CORS_ALLOW_ORIGINS env var (по умолчанию "http://localhost:8080").
// Для разрешения любых origin'ов (embed/production) установи CORS_ALLOW_ORIGINS=*.
func corsMiddleware(next http.Handler) http.Handler {
	origin := os.Getenv("CORS_ALLOW_ORIGINS")
	if origin == "" {
		origin = "http://localhost:8080"
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Tenant-ID, X-Correlation-ID")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// authMiddleware проверяет Authorization: Bearer <token>.
func authMiddleware(token string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			path := r.URL.Path

			// Static files — без auth (все пути под .css, .js, i18n.json, i18n.js, index.html)
			if path == "/" || path == "/index.html" || path == "/styles.css" || path == "/app.js" || path == "/i18n.js" || path == "/i18n.json" || path == "/metrics" {
				next.ServeHTTP(w, r)
				return
			}
			if strings.HasPrefix(path, "/static/") {
				next.ServeHTTP(w, r)
				return
			}

			// Health — без auth
			if path == "/api/health" || path == "/health" {
				next.ServeHTTP(w, r)
				return
			}
			if token == "" {
				http.Error(w, `{"error":"ADMIN_TOKEN not configured"}`, http.StatusInternalServerError)
				return
			}
			auth := r.Header.Get("Authorization")
			expected := "Bearer " + token
			if auth != expected {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// ── Static handler ──

func (s *Server) staticHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Security headers for all static responses
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy",
			"default-src 'self'; "+
				"script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "+
				"style-src 'self' 'unsafe-inline'; "+
				"img-src 'self' data:; "+
				"connect-src 'self'; "+
				"frame-ancestors 'none';",
		)

		// Root → index.html
		if r.URL.Path == "/" {
			data, err := staticFS.ReadFile("static/index.html")
			if err != nil {
				http.Error(w, "not found", http.StatusNotFound)
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Write(data)
			return
		}

		// Проверяем filepath
		path := strings.TrimPrefix(r.URL.Path, "/")
		data, err := staticFS.ReadFile("static/" + path)
		if err != nil {
			// SPA fallback — отдаём index.html для любых неизвестных путей
			data, err := staticFS.ReadFile("static/index.html")
			if err != nil {
				http.Error(w, "not found", http.StatusNotFound)
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Write(data)
			return
		}

		// Content-Type по расширению
		if strings.HasSuffix(path, ".js") {
			w.Header().Set("Content-Type", "application/javascript")
		} else if strings.HasSuffix(path, ".css") {
			w.Header().Set("Content-Type", "text/css")
		} else {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
		}
		w.Write(data)
	}
}

// ── Helpers ──

func respondJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

func respondError(w http.ResponseWriter, status int, code, message string) {
	respondJSON(w, status, map[string]string{"error": code, "message": message})
}

type APIError struct {
	Status  int
	Message string
}

func (e *APIError) Error() string { return e.Message }

// proxyToDataService проксирует запрос к data-service с тем же методом, телом и X-Tenant-ID.
func (s *Server) proxyToDataService(w http.ResponseWriter, r *http.Request, path string) {
	dataURL := s.opts.DataSvcURL + path

	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), r.Method, dataURL, strings.NewReader(string(body)))
	if err != nil {
		respondError(w, http.StatusInternalServerError, "proxy_error", err.Error())
		return
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)
	req.Header.Set("Content-Type", "application/json")

	// Forward X-Tenant-ID from the original request (set by handlers for tenant-aware endpoints)
	if tid := r.Header.Get("X-Tenant-ID"); tid != "" {
		req.Header.Set("X-Tenant-ID", tid)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.Header().Del("Access-Control-Allow-Origin") // наш CORS уже установлен
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

// proxyGetToDataService отправляет GET-запрос к data-service с X-Tenant-ID если задан.
func (s *Server) proxyGetToDataService(path string, tenantID ...string) ([]byte, int, error) {
	dataURL := s.opts.DataSvcURL + path
	req, err := http.NewRequest(http.MethodGet, dataURL, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)

	if len(tenantID) > 0 && tenantID[0] != "" {
		req.Header.Set("X-Tenant-ID", tenantID[0])
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	return body, resp.StatusCode, nil
}

// proxyPostToDataService отправляет POST-запрос к data-service с JSON-телом.
// Если tenantID не пустой, добавляет X-Tenant-ID заголовок.
func (s *Server) proxyPostToDataService(path string, payload any, tenantID ...string) ([]byte, int, error) {
	var bodyReader io.Reader
	if payload != nil {
		data, _ := json.Marshal(payload)
		bodyReader = strings.NewReader(string(data))
	}

	dataURL := s.opts.DataSvcURL + path
	req, err := http.NewRequest(http.MethodPost, dataURL, bodyReader)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.opts.AdminToken)
	req.Header.Set("Content-Type", "application/json")

	if len(tenantID) > 0 && tenantID[0] != "" {
		req.Header.Set("X-Tenant-ID", tenantID[0])
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	return body, resp.StatusCode, nil
}

// ── Handlers ──

func (s *Server) healthHandler(w http.ResponseWriter, r *http.Request) {
	respondJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) i18nHandler(w http.ResponseWriter, r *http.Request) {
	data, err := staticFS.ReadFile("static/i18n.json")
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Write(data)
}

func (s *Server) dashboardHandler(w http.ResponseWriter, r *http.Request) {
	// Получаем список тенантов из data-service (возвращает {"tenants": [...]})
	body, status, err := s.proxyGetToDataService("/admin/tenants")
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}
	if status != http.StatusOK {
		w.WriteHeader(status)
		w.Write(body)
		return
	}

	var respData map[string]json.RawMessage
	if err := json.Unmarshal(body, &respData); err != nil {
		respondJSON(w, http.StatusOK, map[string]any{
			"tenants":      json.RawMessage(body),
			"tenant_count": 0,
			"data_service": s.opts.DataSvcURL,
		})
		return
	}

	tenantsRaw, hasTenants := respData["tenants"]
	tCount := 0
	if hasTenants {
		var tList []any
		if err := json.Unmarshal(tenantsRaw, &tList); err == nil {
			tCount = len(tList)
		}
	}

	respondJSON(w, http.StatusOK, map[string]any{
		"tenants":      respData["tenants"],
		"tenant_count": tCount,
		"data_service": s.opts.DataSvcURL,
	})
}

// ── DB Test ──

type dbTestRequest struct {
	Driver string `json:"driver"`
	DSN    string `json:"dsn"`
}

func (s *Server) dbTestHandler(w http.ResponseWriter, r *http.Request) {
	var req dbTestRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		respondError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	if req.Driver == "" || req.DSN == "" {
		respondError(w, http.StatusBadRequest, "missing_fields", "driver and dsn are required")
		return
	}

	// Пробуем открыть соединение через data-service,
	// передавая DSN в POST /admin/config/rewrite → data-service сам проверит.
	// Для простоты проверяем через прямой запрос к data-service health
	// с новым DSN.
	slog.Info("testing DB connection", "driver", req.Driver, "dsn", req.DSN)

	// Пока просто проверяем, что data-service жив
	healthBody, healthStatus, err := s.proxyGetToDataService("/admin/health")
	if err != nil {
		respondError(w, http.StatusBadGateway, "data_service_unreachable", err.Error())
		return
	}
	if healthStatus != http.StatusOK {
		respondError(w, http.StatusBadGateway, "data_service_error", string(healthBody))
		return
	}

	// Отвечаем успехом — data-service сам проверит DSN при создании тенанта
	respondJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"message": "Data service is reachable",
		"driver":  req.Driver,
		"dsn":     req.DSN,
	})
}

// ── Tenant CRUD ──

func (s *Server) tenantsListHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToDataService(w, r, "/admin/tenants")
}

type createTenantRequest struct {
	TenantID string `json:"tenant_id"`
	Driver   string `json:"driver"`
	DSN      string `json:"dsn"`
}

func (s *Server) tenantCreateHandler(w http.ResponseWriter, r *http.Request) {
	var req createTenantRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		respondError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	if req.TenantID == "" {
		respondError(w, http.StatusBadRequest, "missing_field", "tenant_id is required")
		return
	}

	// Data-service ожидает формат: {id, config: {version, data_source: {driver, dsn}}}
	// Normalize driver string
	driver := req.Driver
	if driver == "sqlite" || driver == "sqlite3" {
		driver = "sqlite"
	} else if driver == "" {
		driver = "sqlite"
	}

	readOnly := true
	configObj := map[string]any{
		"version": 1,
		"data_source": map[string]any{
			"driver":    driver,
			"dsn":       req.DSN,
			"read_only": &readOnly,
		},
	}
	payload := map[string]any{
		"id":     req.TenantID,
		"config": configObj,
	}

	body, status, err := s.proxyPostToDataService("/admin/tenants", payload)
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}

	// Если успешно — сразу запускаем introspection
	if status == http.StatusCreated || status == http.StatusOK {
		slog.Info("tenant registered, starting introspection", "tenant", req.TenantID)
		introBody, introStatus, err := s.proxyPostToDataService("/admin/config/rewrite", nil, req.TenantID)
		_ = err
		if introStatus == http.StatusOK {
			slog.Info("introspection successful", "tenant", req.TenantID)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			w.Write(body)
			return
		}
		slog.Warn("introspection failed after tenant creation", "tenant", req.TenantID, "status", introStatus, "body", string(introBody))
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

func (s *Server) tenantGetHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	s.proxyToDataService(w, r, "/admin/tenants/"+id)
}

func (s *Server) tenantDeleteHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	body, status, err := s.proxyPostToDataService("/admin/tenants/"+id+"/delete", nil)
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

func (s *Server) tenantUploadSQLiteHandler(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(200 << 20); err != nil {
		respondError(w, http.StatusBadRequest, "parse_error", err.Error())
		return
	}

	tenantID := r.FormValue("tenant_id")
	if tenantID == "" {
		respondError(w, http.StatusBadRequest, "missing_field", "tenant_id is required")
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		respondError(w, http.StatusBadRequest, "file_required", err.Error())
		return
	}
	defer file.Close()

	// Save DB file to a data directory
	dataDir := s.opts.DataDir
	if dataDir == "" {
		dataDir = ".data/uploads"
	}
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		respondError(w, http.StatusInternalServerError, "mkdir_error", err.Error())
		return
	}

	// Use .sqlite extension from original or default .db
	ext := filepath.Ext(header.Filename)
	if ext == "" {
		ext = ".db"
	}
	savePath := filepath.Join(dataDir, tenantID+ext)

	dst, err := os.Create(savePath)
	if err != nil {
		respondError(w, http.StatusInternalServerError, "file_create_error", err.Error())
		return
	}
	defer dst.Close()

	if _, err := io.Copy(dst, file); err != nil {
		respondError(w, http.StatusInternalServerError, "file_write_error", err.Error())
		return
	}

	// Register tenant with data-service — DSN is just the file path, no sqlite:// prefix
	dsn := savePath
	readOnly := true
	configObj := map[string]any{
		"version": 1,
		"data_source": map[string]any{
			"driver":    "sqlite",
			"dsn":       dsn,
			"read_only": &readOnly,
		},
	}
	payload := map[string]any{
		"id":     tenantID,
		"config": configObj,
	}

	body, status, err := s.proxyPostToDataService("/admin/tenants", payload)
	if err != nil {
		os.Remove(savePath)
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}

	// Registration failed — clean up the uploaded file
	if status != http.StatusCreated && status != http.StatusOK {
		os.Remove(savePath)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write(body)
		return
	}

	// Success — run introspection
	slog.Info("tenant created from uploaded SQLite, starting introspection", "tenant", tenantID)
	introBody, introStatus, err := s.proxyPostToDataService("/admin/config/rewrite", nil, tenantID)
	_ = err
	if introStatus == http.StatusOK {
		slog.Info("introspection successful", "tenant", tenantID)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write(body)
		return
	}
	slog.Warn("introspection failed after sqlite upload", "tenant", tenantID, "status", introStatus, "body", string(introBody))

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

// ── Tenant Config ──

func (s *Server) tenantConfigGetHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	// Set X-Tenant-ID so data-service resolves the right tenant
	r.Header.Set("X-Tenant-ID", id)
	s.proxyToDataService(w, r, "/admin/config")
}

func (s *Server) tenantConfigPutHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	// Set X-Tenant-ID so data-service resolves the right tenant
	r.Header.Set("X-Tenant-ID", id)
	s.proxyToDataService(w, r, "/admin/config")
}

func (s *Server) tenantManifestHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.Header.Set("X-Tenant-ID", id)
	s.proxyToDataService(w, r, "/mcp/manifest")
}

func (s *Server) tenantIntrospectHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	body, status, err := s.proxyPostToDataService("/admin/config/rewrite", nil, id)
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}
	if status != http.StatusOK {
		// Fallback с query-параметром
		body2, status2, err2 := s.proxyPostToDataService("/admin/config/rewrite?tenant="+id, nil)
		if err2 == nil && status2 == http.StatusOK {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status2)
			w.Write(body2)
			return
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

// ── Tool approval ──

func (s *Server) toolsPendingHandler(w http.ResponseWriter, r *http.Request) {
	// Extract tenant ID from URL param (matches /tenants/{id}/tools/pending)
	id := chi.URLParam(r, "id")
	if id == "" {
		respondError(w, http.StatusBadRequest, "missing_tenant", "tenant id is required")
		return
	}
	r.Header.Set("X-Tenant-ID", id)
	s.proxyToDataService(w, r, "/admin/tenants/"+id+"/tools/pending")
}

func (s *Server) toolsApproveHandler(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	toolName := chi.URLParam(r, "toolName")
	if id == "" || toolName == "" {
		respondError(w, http.StatusBadRequest, "missing_params", "tenant id and tool name are required")
		return
	}
	r.Header.Set("X-Tenant-ID", id)
	body, status, err := s.proxyPostToDataService("/admin/tenants/"+id+"/tools/"+toolName+"/approve", nil)
	if err != nil {
		respondError(w, http.StatusBadGateway, "upstream_error", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

// ── RAG proxy ──

func (s *Server) ragHealthHandler(w http.ResponseWriter, r *http.Request) {
	body, status, err := s.ragClient.Do(r.Method, "/health", nil)
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

func (s *Server) ragDocListHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}
	respBody, status, err := s.ragClient.Do("POST", "/documents/list", json.RawMessage(body))
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(respBody)
}

func (s *Server) ragDocImportHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}
	respBody, status, err := s.ragClient.Do("POST", "/documents/import", json.RawMessage(body))
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(respBody)
}

func (s *Server) ragDocDeleteHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}
	respBody, status, err := s.ragClient.Do("POST", "/documents/delete", json.RawMessage(body))
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(respBody)
}

func (s *Server) ragDocUploadHandler(w http.ResponseWriter, r *http.Request) {
	// Parse multipart form — max 50 MB
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		respondError(w, http.StatusBadRequest, "parse_error", err.Error())
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		respondError(w, http.StatusBadRequest, "file_required", err.Error())
		return
	}
	defer file.Close()

	fileContent, err := io.ReadAll(file)
	if err != nil {
		respondError(w, http.StatusBadRequest, "file_read_error", err.Error())
		return
	}

	formFields := map[string]string{}
	if title := r.FormValue("title"); title != "" {
		formFields["title"] = title
	}
	if disciplineID := r.FormValue("discipline_id"); disciplineID != "" {
		formFields["discipline_id"] = disciplineID
	}

	respBody, status, err := s.ragClient.Upload("/documents/upload", header.Filename, "file", fileContent, formFields)
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(respBody)
}

// ── RAG Admin API ──

func (s *Server) ragConfigGetHandler(w http.ResponseWriter, r *http.Request) {
	body, status, err := s.ragClient.Do(http.MethodGet, "/admin/config", nil)
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

func (s *Server) ragConfigPutHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}
	respBody, status, err := s.ragClient.Do(http.MethodPut, "/admin/config", json.RawMessage(body))
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(respBody)
}

func (s *Server) ragStatsHandler(w http.ResponseWriter, r *http.Request) {
	body, status, err := s.ragClient.Do(http.MethodGet, "/admin/stats", nil)
	if err != nil {
		respondError(w, http.StatusBadGateway, "rag_unreachable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(body)
}

// ── Proxy to API Service (agent CRUD) ──

func (s *Server) proxyToApiService(w http.ResponseWriter, r *http.Request, path string) {
	apiURL := s.opts.ApiSvcURL + path

	body, err := io.ReadAll(r.Body)
	if err != nil {
		respondError(w, http.StatusBadRequest, "read_error", err.Error())
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), r.Method, apiURL, strings.NewReader(string(body)))
	if err != nil {
		respondError(w, http.StatusInternalServerError, "proxy_error", err.Error())
		return
	}

	if token := r.Header.Get("Authorization"); token != "" {
		req.Header.Set("Authorization", token)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		respondError(w, http.StatusBadGateway, "api_unreachable", err.Error())
		return
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.Header().Del("Access-Control-Allow-Origin")
	// 204 No Content — не пишем тело и сбрасываем Content-Type
	if resp.StatusCode == http.StatusNoContent {
		w.Header().Del("Content-Type")
		w.WriteHeader(resp.StatusCode)
		return
	}
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

func (s *Server) agentListHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/api/agents")
}

func (s *Server) agentCreateHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/api/agents")
}

func (s *Server) agentGetHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/api/agents/"+name)
}

func (s *Server) agentUpdateHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/api/agents/"+name)
}

func (s *Server) agentDeleteHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/api/agents/"+name)
}

func (s *Server) llmConfigGetHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/admin/llm-config")
}

func (s *Server) llmProvidersListHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/admin/llm-providers")
}

func (s *Server) llmProvidersAddHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/admin/llm-providers")
}

func (s *Server) llmProvidersGetHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/admin/llm-providers/"+name)
}

func (s *Server) llmProvidersUpdateHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/admin/llm-providers/"+name)
}

func (s *Server) llmProvidersDeleteHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/admin/llm-providers/"+name)
}

func (s *Server) llmProvidersToggleHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	s.proxyToApiService(w, r, "/admin/llm-providers/"+name+"/toggle")
}

func (s *Server) llmProviderListHandler(w http.ResponseWriter, r *http.Request) {
	s.proxyToApiService(w, r, "/admin/llm-provider-list")
}
