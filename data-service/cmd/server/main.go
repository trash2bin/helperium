// data-service — HTTP-сервис доступа к данным университета.
//
// Единственный сервис, который знает схему БД.
// Предоставляет REST API для потребителей (MCP, API, CLI).
//
// Запуск:
//
//	go run ./cmd/server/
//	go run ./cmd/server/ --seed                     # залить fixtures/seed.json в пустую БД (fatal если не пустая)
//	go run ./cmd/server/ --seed path/to/seed.json  # то же с указанием файла
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
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/seedgen"
	"github.com/agent-tutor/data-service/internal/server"
)

const defaultSeedPath = "fixtures/seed.json"

func main() {
	// ── CLI флаги ──
	seedFlag := flag.Bool("seed", false, "залить seed-данные в пустую БД и завершиться (dev-only)")
	seedPath := flag.String("seed-path", defaultSeedPath, "путь к JSON с seed-данными (используется с --seed)")
	flag.Parse()

	server.InitLogger()

	// ── Открываем БД ──
	database, err := db.New()
	if err != nil {
		slog.Error("failed to open database", "error", err)
		os.Exit(1)
	}
	defer database.Close()

	// ── Seed-режим: залить данные и выйти ──
	if *seedFlag {
		if err := runSeed(database, *seedPath); err != nil {
			slog.Error("seed failed", "error", err)
			os.Exit(1)
		}
		slog.Info("seed completed successfully, exiting")
		return
	}

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

// runSeed загружает JSON и применяет его к БД.
// Паникует (os.Exit в main), если БД уже содержит данные — это защита от перезаписи prod-БД.
func runSeed(database db.DB, path string) error {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return fmt.Errorf("resolve seed path: %w", err)
	}
	slog.Info("seed mode", "path", absPath)

	seed, err := seedgen.Load(absPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("%w: %s", seedgen.ErrSeedFileMissing, absPath)
		}
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	if err := seedgen.Apply(ctx, database, seed); err != nil {
		return err
	}
	return nil
}
