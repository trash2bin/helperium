// Package mcp-gateway — MCP (Model Context Protocol) сервер на Go.
//
// Конфигурация MCP-инструментов получается по HTTP от data-service
// (эндпоинт /mcp/manifest), а не прямым парсингом config.json.
// data-service остаётся единственным source of truth для конфигурации.
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
	"github.com/google/uuid"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/mcp-gateway/internal/httpclient"
	"github.com/agent-tutor/mcp-gateway/internal/tools"
)

// sseSession представляет активное SSE-подключение.
type sseSession struct {
	writer    http.ResponseWriter
	flusher   http.Flusher
	done      chan struct{}
	tenantID  string
}

func main() {
	devMode := os.Getenv("MCP_DEV") == "true"
	logLevel := slog.LevelInfo
	if devMode {
		logLevel = slog.LevelDebug
	}
	logHandler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel})
	slog.SetDefault(slog.New(logHandler))

	if devMode {
		slog.Info("🧪 MCP_DEV mode enabled — debug endpoints, request logging, message dumps")
	}
	slog.Info("mcp-gateway starting")

	// ── Конфиг: локальный файл (если задан MCP_LOCAL_CONFIG_PATH) или HTTP от data-service ──
	var mcpCfg *config.Config
	var configSource string
	var err error

	if localPath := os.Getenv("MCP_LOCAL_CONFIG_PATH"); localPath != "" {
		slog.Info("loading config from local file", "path", localPath)
		mcpCfg, err = config.Load(localPath)
		if err != nil {
			slog.Error("load local config", "path", localPath, "error", err)
			os.Exit(1)
		}
		configSource = fmt.Sprintf("local:%s", localPath)
	} else {
		client := httpclient.New()
		mcpCfg, err = client.FetchConfig()
		if err != nil {
			slog.Error("fetch config from data-service", "error", err)
			os.Exit(1)
		}
		configSource = fmt.Sprintf("data-service:%s", client.BaseURL())
	}

	// ── MCP-сервер + регистрация тулов ──
	mcpServer, registry, err := buildMCPServer(mcpCfg)
	if err != nil {
		slog.Error("build MCP server", "error", err)
		os.Exit(1)
	}

	slog.Info("config loaded",
		"source", configSource,
		"auto_tools", len(registry.GetToolDefs()),
		"explicit_mcp_tools", len(mcpCfg.MCPTools),
		"entities", len(mcpCfg.Entities),
		"endpoints", len(mcpCfg.Endpoints),
	)

	ragInfo := "disabled"
	if registry.RagEnabled() {
		ragInfo = "enabled"
	} else {
		ragInfo = "disabled (" + registry.RagDisabledReason() + ")"
	}
	slog.Info("RAG tools", "status", ragInfo)

	// ── HTTP-роутер ──
	r := buildRouter(mcpServer, registry, mcpCfg, devMode)

	port := os.Getenv("MCP_PORT")
	if port == "" {
		port = "8083"
	}

	addr := fmt.Sprintf(":%s", port)
	httpServer := &http.Server{
		Addr:         addr,
		Handler:      r,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	// Graceful shutdown
	go func() {
		quit := make(chan os.Signal, 1)
		signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
		sig := <-quit
		slog.Info("shutting down", "signal", sig.String())

		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		if err := httpServer.Shutdown(ctx); err != nil {
			slog.Error("forced shutdown", "error", err)
		}
	}()

	slog.Info("mcp-gateway listening", "port", port)
	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
	slog.Info("mcp-gateway stopped")
}

// buildMCPServer создаёт MCP-сервер и регистрирует инструменты по конфигу.
func buildMCPServer(cfg *config.Config) (*server.MCPServer, *tools.Registry, error) {
	mcpServer := server.NewMCPServer("agent-tutor", "1.0.0")
	registry := tools.NewRegistry(cfg)
	registry.RegisterAll(mcpServer)
	return mcpServer, registry, nil
}

// buildRouter собирает chi-роутер со всеми endpoint'ами, включая MCP- и debug.
func buildRouter(mcpServer *server.MCPServer, registry *tools.Registry, cfg *config.Config, devMode bool) *chi.Mux {
	sessions := &sync.Map{}
	r := chi.NewRouter()

	// Create a shared HTTP client for handlers
	client := httpclient.New()

	if devMode {
		r.Use(requestLogger)
	}

	// Health
	r.Get("/health", healthHandler())

	// SSE endpoint — GET /mcp
	r.Get("/mcp", sseHandler(sessions))

	// JSON-RPC messages — POST /mcp/message
	mcpPost := mcpPostHandler(mcpServer, sessions)
	r.Post("/mcp/message", mcpPost)

	// Fallback POST /mcp (Python SDK compat)
	r.Post("/mcp", mcpPost)

	// Dev-режим endpoints
	if devMode {
		r.Get("/debug/sessions", debugSessionsHandler(sessions))
		r.Get("/debug/config", debugConfigHandler(registry, cfg, devMode))
		r.Get("/debug", debugPlaygroundHandler())
		r.Get("/", func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "/debug", http.StatusFound)
		})
	}

	// tools/list (always available)
	r.Get("/tools/list", toolsListHandler(mcpServer, registry, client))

	// tools/call (always available)
	r.Post("/tools/call", toolsCallHandler(mcpServer, client))

	return r
}

// ── Handler constructors ──

func healthHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{
			"status":  "ok",
			"service": "mcp-gateway",
		})
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
			writer:    w,
			flusher:   flusher,
			done:      make(chan struct{}),
			tenantID:  r.Header.Get("X-Tenant-ID"),
		}
		sessions.Store(sessionID, session)
		defer sessions.Delete(sessionID)

		scheme := "http"
		if r.TLS != nil {
			scheme = "https"
		}
		messageURL := fmt.Sprintf("%s://%s/mcp/message?sessionId=%s", scheme, r.Host, sessionID)
		fmt.Fprintf(w, "event: endpoint\ndata: %s\r\n\r\n", messageURL)
		flusher.Flush()

		<-r.Context().Done()
		close(session.done)
	}
}

func debugSessionsHandler(sessions *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		var sseIDs []string
		sessions.Range(func(key, value any) bool {
			sseIDs = append(sseIDs, fmt.Sprint(key))
			return true
		})
		json.NewEncoder(w).Encode(map[string]any{
			"total_sessions": len(sseIDs),
			"session_ids":    sseIDs,
			"note":           "SSE-сессии; POST/message без sessionId создаёт новый UUID",
		})
	}
}

func debugConfigHandler(registry *tools.Registry, cfg *config.Config, devMode bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		ragStatus := "disabled"
		ragReason := ""
		if registry.RagEnabled() {
			ragStatus = "enabled"
		} else {
			ragReason = registry.RagDisabledReason()
		}
		source := "data-service /mcp/manifest"
		if localPath := os.Getenv("MCP_LOCAL_CONFIG_PATH"); localPath != "" {
			source = fmt.Sprintf("local file: %s", localPath)
		}
		json.NewEncoder(w).Encode(map[string]any{
			"source":        source,
			"entities":      len(cfg.Entities),
			"endpoints":     len(cfg.Endpoints),
			"all_tools":     registry.GetToolNames(),
			"auto_tools":    len(registry.GetToolDefs()),
			"mcp_tools_cfg": len(cfg.MCPTools),
			"rag_status":    ragStatus,
			"rag_reason":    ragReason,
			"dev_mode":      devMode,
		})
	}
}

func debugPlaygroundHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(playgroundHTML))
	}
}

func toolsListHandler(mcpServer *server.MCPServer, registry *tools.Registry, client *httpclient.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		tenantID := r.Header.Get("X-Tenant-ID")
		
		cfg, err := client.FetchConfigWithTenant(tenantID)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":"failed to fetch tenant config: %s"}`, err), http.StatusInternalServerError)
			return
		}

		// Create a temporary registry for this tenant's config to reuse deriveParams logic
		tenantRegistry := tools.NewRegistry(cfg)
		toolDefs := tenantRegistry.GetToolDefs()

		tools := make([]map[string]any, 0)
		for _, td := range toolDefs {
			props := make(map[string]any)
			for _, p := range td.Params {
				props[p.Name] = map[string]any{
					"type": p.Type,
					"description": p.Description,
				}
			}
			tools = append(tools, map[string]any{
				"name": td.Name,
				"description": td.Description,
				"inputSchema": map[string]any{
					"type": "object",
					"properties": props,
				},
			})
		}
		
		// Add static RAG tools
		tools = append(tools, map[string]any{
			"name": "search_documents",
			"description": "Search documents",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"query": map[string]any{"type": "string", "description": "Search query"},
					"discipline_id": map[string]any{"type": "string", "description": "Discipline ID"},
					"limit": map[string]any{"type": "integer", "description": "Limit"},
				},
			},
		})
		tools = append(tools, map[string]any{
			"name": "list_documents",
			"description": "List documents",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"discipline_id": map[string]any{"type": "string", "description": "Discipline ID"},
				},
			},
		})
		tools = append(tools, map[string]any{
			"name": "get_rag_context",
			"description": "Get RAG context",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"query": map[string]any{"type": "string", "description": "Search query"},
					"discipline_id": map[string]any{"type": "string", "description": "Discipline ID"},
					"limit": map[string]any{"type": "integer", "description": "Limit"},
				},
			},
		})

		response := map[string]any{
			"jsonrpc": "2.0",
			"id": uuid.New().String(),
			"result": map[string]any{
				"tools": tools,
			},
		}
		json.NewEncoder(w).Encode(response)
	}
}

func toolsCallHandler(mcpServer *server.MCPServer, client *httpclient.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		tenantID := r.Header.Get("X-Tenant-ID")

		var body struct {
			Name      string         `json:"name"`
			Arguments map[string]any `json:"arguments"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, fmt.Sprintf(`{"error":"invalid JSON: %s"}`, err), http.StatusBadRequest)
			return
		}

		cfg, err := client.FetchConfigWithTenant(tenantID)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":"failed to fetch tenant config: %s"}`, err), http.StatusInternalServerError)
			return
		}
		
		slog.Debug("tool call resolve", "name", body.Name, "tenant", tenantID)
		var endpoint string
		for _, ep := range cfg.Endpoints {
			name := ""
			switch ep.Op {
			case config.OpGetByID: name = "get_" + ep.Entity
			case config.OpFind: name = "find_" + ep.Entity
			case config.OpList: name = "list_" + ep.Entity
			case config.OpCustomQuery: 
				if ep.QueryID != "" { name = ep.QueryID }
			}
			if name == body.Name {
				slog.Debug("tool matched", "name", name, "endpoint", ep.Path)
				endpoint = ep.Path
				break
			}
		}
		
		if endpoint == "" {
			for _, mt := range cfg.MCPTools {
				if mt.Name == body.Name { endpoint = mt.Endpoint; break }
			}
		}
		
		if endpoint == "" {
			if body.Name == "search_documents" {
				endpoint = "/rag/search"
			}
			if body.Name == "list_documents" {
				endpoint = "/rag/list"
			}
			if body.Name == "get_rag_context" {
				endpoint = "/rag/context"
			}
			if endpoint == "" {
				http.Error(w, fmt.Sprintf(`{"error":"tool %s not found for tenant %s"}`, body.Name, tenantID), http.StatusNotFound)
				return
			}
		}

		ctx := r.Context()
		ctx = context.WithValue(ctx, httpclient.TenantIDKey, tenantID)
		
		result, err := client.Call(ctx, endpoint, body.Arguments)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":"call failed: %s"}`, err), http.StatusInternalServerError)
			return
		}

		response := map[string]any{
			"jsonrpc": "2.0",
			"id": uuid.New().String(),
			"result": map[string]any{
				"content": result,
			},
		}
		json.NewEncoder(w).Encode(response)
	}
}

// mcpPostHandler возвращает HTTP-хендлер для JSON-RPC сообщений MCP.
func mcpPostHandler(mcpServer *server.MCPServer, sessions *sync.Map) http.HandlerFunc {
	devMode := os.Getenv("MCP_DEV") == "true"
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := r.URL.Query().Get("sessionId")
		if sessionID == "" {
			sessionID = uuid.New().String()
		}

		var rawMessage json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&rawMessage); err != nil {
			writeJSONRPCError(w, nil, mcp.PARSE_ERROR, "Parse error")
			return
		}

		// Dev: лог входящего MCP-сообщения
		if devMode {
			var msg map[string]any
			if err := json.Unmarshal(rawMessage, &msg); err == nil {
				slog.Debug("→ MCP",
					"method", msg["method"],
					"id", msg["id"],
					"session", sessionID,
					"params", truncateJSON(msg["params"], 500),
				)
			}
		}

		// Extract tenant ID: priority Header > Session
		tenantID := r.Header.Get("X-Tenant-ID")
		if tenantID == "" {
			if si, ok := sessions.Load(sessionID); ok {
				s := si.(*sseSession)
				tenantID = s.tenantID
			}
		}

		ctx := mcpServer.WithContext(r.Context(), server.NotificationContext{
			ClientID:  sessionID,
			SessionID: sessionID,
		})

		// Inject tenant ID into context for tools
		if tenantID != "" {
			ctx = context.WithValue(ctx, httpclient.TenantIDKey, tenantID)
		}

		response := mcpServer.HandleMessage(ctx, rawMessage)
		if response != nil {
			// Dev: лог исходящего ответа
			if devMode {
				respBytes, _ := json.Marshal(response)
				var respDump map[string]any
				if json.Unmarshal(respBytes, &respDump) == nil {
					slog.Debug("← MCP",
						"session", sessionID,
						"result", truncateJSON(respDump["result"], 500),
					)
				}
			}

			if si, ok := sessions.Load(sessionID); ok {
				s := si.(*sseSession)
				eventData, _ := json.Marshal(response)
				fmt.Fprintf(s.writer, "event: message\ndata: %s\n\n", eventData)
				s.flusher.Flush()
				w.WriteHeader(http.StatusAccepted)
			} else {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(response)
			}
		} else {
			w.WriteHeader(http.StatusAccepted)
		}
	}
}



// requestLogger — middleware: пишет method, path, status, duration
func requestLogger(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		srw := &statusResponseWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(srw, r)
		slog.Debug("[HTTP]",
			"method", r.Method,
			"path", r.URL.Path,
			"status", srw.status,
			"duration", time.Since(start).String(),
		)
	})
}

type statusResponseWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusResponseWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

// truncateJSON обрезает значение до N байт для читаемых логов.
func truncateJSON(v any, maxLen int) any {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("<marshal error: %s>", err)
	}
	if len(b) <= maxLen {
		return json.RawMessage(b)
	}
	s := string(b)
	// Для строк и компактных структур — режем с многоточием
	truncated := s[:maxLen] + "..."
	return truncated
}

// writeJSONRPCError пишет JSON-RPC ошибку.
func writeJSONRPCError(w http.ResponseWriter, id any, code int, message string) {
	resp := map[string]any{
		"jsonrpc": "2.0",
		"error":   map[string]any{"code": code, "message": message},
		"id":      id,
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusBadRequest)
	json.NewEncoder(w).Encode(resp)
}
