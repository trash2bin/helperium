package main

import (
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/agent-tutor/admin-dashboard/internal/server"
	"github.com/agent-tutor/agent-tutor-go/pkg/metrics"
)

func main() {
	// Configure structured JSON logging with LOG_LEVEL env
	var logLevel slog.Level
	switch strings.ToLower(os.Getenv("LOG_LEVEL")) {
	case "debug":
		logLevel = slog.LevelDebug
	case "warn":
		logLevel = slog.LevelWarn
	case "error":
		logLevel = slog.LevelError
	default:
		logLevel = slog.LevelInfo
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel})))
	metrics.RegisterMetrics()

	addr := flag.String("addr", envOrDefault("LISTEN_ADDR", ":8085"), "Listen address")
	dataSvcURL := flag.String("data-service", envOrDefault("DATA_SERVICE_URL", "http://127.0.0.1:8084"), "Data service base URL")
	ragSvcURL := flag.String("rag-service", envOrDefault("RAG_SERVICE_URL", "http://127.0.0.1:8082"), "RAG service base URL")
	apiSvcURL := flag.String("api-service", envOrDefault("API_SERVICE_URL", "http://127.0.0.1:8081"), "API service base URL")
	adminToken := flag.String("admin-token", os.Getenv("ADMIN_TOKEN"), "Admin auth token")
	flag.Parse()

	if *adminToken == "" {
		slog.Warn("ADMIN_TOKEN not set — admin API endpoints will reject requests")
	}

	srv := server.New(server.Options{
		Addr:         *addr,
		DataSvcURL:   *dataSvcURL,
		RagSvcURL:    *ragSvcURL,
		ApiSvcURL:    *apiSvcURL,
		AdminToken:   *adminToken,
		DataDir:      envOrDefault("DATA_DIR", ".data/uploads"),
	})

	slog.Info("starting admin dashboard", "addr", *addr, "data_service", *dataSvcURL)

	httpServer := &http.Server{
		Addr:         *addr,
		Handler:      srv.Router(),
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  30 * time.Second,
	}

	fmt.Printf("🌐 Admin Dashboard: http://localhost%s\n", *addr)
	if err := httpServer.ListenAndServe(); err != nil {
		slog.Error("server error", "error", err)
		os.Exit(1)
	}
}

func envOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}
