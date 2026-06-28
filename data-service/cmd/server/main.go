// data-service — config-driven HTTP-сервис доступа к произвольной БД.
//
// Читает конфиг (JSON), строит REST API на основе схемы БД клиента.
// Никакого захардкоженного знания о домене.
//
// Запуск:
//
//	go run ./cmd/server/                                    # config-driven, дефолтный конфиг
//	go run ./cmd/server/ --config path/to/config.json       # кастомный конфиг
//
// Переменные окружения:
//
//	PORT          — порт HTTP (по умолчанию 8084)
//	DS_CONFIG     — путь к конфигу (по умолчанию specs/config.example.json)
//	LOG_LEVEL     — info (по умолчанию) или debug
//
// Seed-режим (dev-only) вынесен в отдельную утилиту cmd/seed-cli.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/configgen"
	"github.com/agent-tutor/data-service/internal/datasource"
	"github.com/agent-tutor/data-service/internal/server"
)

const defaultConfigPath = "specs/config.example.json"

func main() {
	// ── CLI флаги ──
	discoverFlag := flag.Bool("discover", false, "прочитать схему БД и вывести сгенерированный конфиг в stdout")
	cfgPath := flag.String("config", "", "путь к JSON-конфигу (по умолчанию $DS_CONFIG или specs/config.example.json)")
	flag.Parse()

	server.InitLogger()

	// ── Discover-режим: прочитать схему, сгенерировать конфиг и выйти ──
	if *discoverFlag || os.Getenv("DS_DISCOVER") != "" {
		if err := runDiscover(); err != nil {
			slog.Error("discover failed", "error", err)
			os.Exit(1)
		}
		return
	}

	// ── Загружаем конфиг ──
	cfgFile := *cfgPath
	if cfgFile == "" {
		cfgFile = os.Getenv("DS_CONFIG")
	}
	if cfgFile == "" {
		cfgFile = defaultConfigPath
	}

	absCfgPath, err := filepath.Abs(cfgFile)
	if err != nil {
		slog.Error("resolve config path", "error", err)
		os.Exit(1)
	}

	cfg, err := config.Load(absCfgPath)
	if err != nil {
		slog.Error("load config", "error", err)
		os.Exit(1)
	}

	// ── Открываем БД через datasource registry ──
	registry := datasource.NewDefaultRegistry()
	adapter, ok := registry.Get(string(cfg.DataSource.Driver))
	if !ok {
		slog.Error("unsupported driver", "driver", cfg.DataSource.Driver, "drivers", registry.Drivers())
		os.Exit(1)
	}

	dsn := cfg.DataSource.DSN
	conn, err := adapter.Connect(context.Background(), dsn)
	if err != nil {
		slog.Error("connect to database", "driver", cfg.DataSource.Driver, "error", err)
		os.Exit(1)
	}
	defer conn.Close()

	// ── Оборачиваем в AdapterSubset ──
	dbAdapter := &connAdapter{conn: conn, adp: adapter}

	// ── Строим config-driven роутер ──
	router, err := server.NewRouterFromConfig(cfg, dbAdapter, dbAdapter, adapter, absCfgPath)
	if err != nil {
		slog.Error("build router", "error", err)
		os.Exit(1)
	}

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
		"driver", cfg.DataSource.Driver,
		"config", absCfgPath,
	)

	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}

	slog.Info("data-service stopped")
}

// connAdapter combines datasource.Conn (query/ping) and datasource.Adapter
// (quote/placeholder) into a single runtime.AdapterSubset-compatible type.
type connAdapter struct {
	conn datasource.Conn
	adp  datasource.Adapter
}

func (c *connAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return c.conn.QueryContext(ctx, query, args...)
}
func (c *connAdapter) PingContext(ctx context.Context) error          { return c.conn.PingContext(ctx) }
func (c *connAdapter) QuoteIdentifier(name string) string            { return c.adp.QuoteIdentifier(name) }
func (c *connAdapter) TranslatePlaceholder(index int) string         { return c.adp.TranslatePlaceholder(index) }

// runDiscover открывает БД по env, интроспектирует схему и выводит конфиг в stdout.
func runDiscover() error {
	driver := os.Getenv("DB_DRIVER")
	if driver == "" {
		driver = "sqlite"
	}
	registry := datasource.NewDefaultRegistry()
	adapter, ok := registry.Get(driver)
	if !ok {
		return fmt.Errorf("unknown driver: %s", driver)
	}

	var dsn string
	switch driver {
	case "sqlite":
		p := os.Getenv("DB_PATH")
		if p == "" {
			p = "university.db"
		}
		abs, err := filepath.Abs(p)
		if err != nil {
			return fmt.Errorf("resolve DB_PATH: %w", err)
		}
		dsn = abs
	case "postgres":
		url := os.Getenv("DATABASE_URL")
		if url == "" {
			return fmt.Errorf("DATABASE_URL required for postgres")
		}
		dsn = url
	default:
		return fmt.Errorf("unsupported driver: %s", driver)
	}

	conn, err := adapter.Connect(context.Background(), dsn)
	if err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer conn.Close()


	schema, err := adapter.Introspect(context.Background(), conn)
	if err != nil {
		return fmt.Errorf("introspect: %w", err)
	}

	ds := config.DataSourceConfig{Driver: config.Driver(driver), DSN: dsn}
	cfg := configgen.Generate(schema, ds, nil)

	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal config: %w", err)
	}

	// Выводим КОНФИГ в stdout (slog автоматически пишет в stderr с JSON-хендлером)
	fmt.Println(string(b))
	return nil
}


