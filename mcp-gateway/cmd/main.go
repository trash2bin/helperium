// Package mcp-gateway — MCP (Model Context Protocol) сервер на Go.
//
// Читает тот же config.json что и data-service, авто-генерирует
// MCP-инструменты из cfg.endpoints[] с опциональными оверрайдами
// из cfg.mcp_tools[] и делегирует вызовы через HTTP к data-service.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	"github.com/agent-tutor/mcp-gateway/internal/config"
	"github.com/agent-tutor/mcp-gateway/internal/tools"
)

// sseSession представляет активное SSE-подключение.
type sseSession struct {
	writer  http.ResponseWriter
	flusher http.Flusher
	done    chan struct{}
}

func main() {
	cfgPath := flag.String("config", "", "путь к config.json (по умолчанию $DS_CONFIG или поиск по locations)")
	flag.Parse()

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

	// ── Конфиг ──
	cfgFile := resolveConfigPath(*cfgPath)
	absPath, err := filepath.Abs(cfgFile)
	if err != nil {
		slog.Error("resolve config path", "error", err)
		os.Exit(1)
	}

	mcpCfg, err := config.Load(absPath)
	if err != nil {
		slog.Error("load config", "error", err)
		os.Exit(1)
	}

	// ── MCP-сервер ──
	mcpServer := server.NewMCPServer(
		"agent-tutor",
		"1.0.0",
	)

	registry := tools.NewRegistry(mcpCfg)
	registry.RegisterAll(mcpServer)

	toolDefs := registry.GetToolDefs()
	slog.Info("config loaded",
		"auto_tools", len(toolDefs),
		"explicit_mcp_tools", len(mcpCfg.MCPTools),
		"entities", len(mcpCfg.Entities),
		"endpoints", len(mcpCfg.Endpoints),
		"path", absPath,
	)

	// ── SSE session manager ──
	sessions := &sync.Map{}

	// ── HTTP-роутер (chi) ──
	r := chi.NewRouter()

	// Dev middleware: логируем каждый HTTP-запрос
	if devMode {
		r.Use(requestLogger)
	}

	// Health
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{
			"status":  "ok",
			"service": "mcp-gateway",
		})
	})

	// SSE endpoint — GET /mcp
	r.Get("/mcp", func(w http.ResponseWriter, r *http.Request) {
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
			writer:  w,
			flusher: flusher,
			done:    make(chan struct{}),
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
	})

	// JSON-RPC messages — POST /mcp/message (стандартный MCP streamable HTTP)
	mcpPost := mcpPostHandler(mcpServer, sessions)
	r.Post("/mcp/message", mcpPost)

	// Некоторые реализа��ии Python MCP SDK шлют POST на тот же URL что SSE (/mcp),
	// а не на полученный из event: endpoint URL — поэтому дублируем хендлер
	r.Post("/mcp", mcpPost)

	// Dev-режим endpoint'ы: доступны всегда, но в devMode — с дополнительным контекстом
	if devMode {
		r.Get("/debug/sessions", func(w http.ResponseWriter, r *http.Request) {
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
		})

		r.Get("/debug/config", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			toolDefs := registry.GetToolDefs()
			toolNames := make([]string, 0, len(toolDefs))
			for _, td := range toolDefs {
				toolNames = append(toolNames, td.Name)
			}
			json.NewEncoder(w).Encode(map[string]any{
				"config_path":   absPath,
				"entities":      len(mcpCfg.Entities),
				"endpoints":     len(mcpCfg.Endpoints),
				"tools":         toolNames,
				"tools_count":   len(toolDefs),
				"mcp_tools_cfg": len(mcpCfg.MCPTools),
				"auto_tools":    true,
				"dev_mode":      devMode,
			})
		})

		// MCP Playground — встроенный HTML-UI для тестирования инструментов
		r.Get("/debug", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(playgroundHTML))
		})

		// Редирект с / на /debug в dev-режиме
		r.Get("/", func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "/debug", http.StatusFound)
		})
	}

	// Debug endpoints (всегда, для тестов)
	r.Get("/tools/list", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		req := map[string]any{
			"jsonrpc": "2.0", "id": uuid.New().String(),
			"method": "tools/list", "params": map[string]any{},
		}
		rawReq, _ := json.Marshal(req)
		ctx := mcpServer.WithContext(r.Context(), server.NotificationContext{
			ClientID: "debug", SessionID: "debug",
		})
		response := mcpServer.HandleMessage(ctx, rawReq)
		if response != nil {
			json.NewEncoder(w).Encode(response)
		}
	})

	r.Post("/tools/call", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		var body struct {
			Name      string         `json:"name"`
			Arguments map[string]any `json:"arguments"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, fmt.Sprintf(`{"error":"invalid JSON: %s"}`, err), http.StatusBadRequest)
			return
		}
		req := map[string]any{
			"jsonrpc": "2.0", "id": uuid.New().String(),
			"method": "tools/call",
			"params": map[string]any{"name": body.Name, "arguments": body.Arguments},
		}
		rawReq, _ := json.Marshal(req)
		ctx := mcpServer.WithContext(r.Context(), server.NotificationContext{
			ClientID: "debug", SessionID: "debug",
		})
		response := mcpServer.HandleMessage(ctx, rawReq)
		if response != nil {
			json.NewEncoder(w).Encode(response)
		}
	})

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

	slog.Info("mcp-gateway listening", "port", port, "config", absPath)
	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
	slog.Info("mcp-gateway stopped")
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

		ctx := mcpServer.WithContext(r.Context(), server.NotificationContext{
			ClientID:  sessionID,
			SessionID: sessionID,
		})

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

// resolveConfigPath ищет config.json по приоритету: флаг → DS_CONFIG → кандидаты.
func resolveConfigPath(userPath string) string {
	if userPath != "" {
		return userPath
	}
	if env := os.Getenv("DS_CONFIG"); env != "" {
		return env
	}

	cwd, _ := os.Getwd()
	candidates := []string{
		filepath.Join(cwd, "..", "specs", "config.example.json"),
		filepath.Join(cwd, "specs", "config.example.json"),
	}
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(exeDir, "..", "..", "specs", "config.example.json"),
		)
	}

	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	return candidates[0]
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
