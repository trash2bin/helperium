// data-service — HTTP-сервис доступа к данным университета.
//
// Единственный сервис, который знает схему БД.
// Предоставляет REST API для потребителей (MCP, API, CLI).
//
// Запуск:
//
//	go run ./cmd/server/
//
// Переменные окружения:
//
//	DB_DRIVER     — sqlite (по умолчанию) или postgres
//	DB_PATH       — путь к файлу SQLite (по умолчанию university.db)
//	DATABASE_URL  — строка подключения PostgreSQL
//	PORT          — порт HTTP (по умолчанию 8084)
//	LOG_LEVEL     — info (по умолчанию) или debug
package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/server"
)

func main() {
	server.InitLogger()

	// ── Открываем БД ──
	database, err := db.New()
	if err != nil {
		slog.Error("failed to open database", "error", err)
		os.Exit(1)
	}
	defer database.Close()

	// ── Настраиваем HTTP ──
	router := server.NewRouter(database)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8084"
	}

	addr := fmt.Sprintf(":%s", port)

	httpServer := &http.Server{
		Addr:         addr,
		Handler:      router,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	// ── Graceful shutdown ──
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

	slog.Info("data-service starting",
		"port", port,
		"driver", os.Getenv("DB_DRIVER"),
	)

	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}

	slog.Info("data-service stopped")
}
