// data-service — config-driven HTTP-сервис доступа к произвольной БД.
//
// Читает конфиг (JSON), строит REST API на основе схемы БД клиента.
// Никакого захардкоженного знания о домене.
//
// Запуск:
//
//	go run ./cmd/server/                                    # config-driven, дефолтный конфиг
//	go run ./cmd/server/ --config path/to/config.json       # кастомный конфиг
//	go run ./cmd/server/ --materialize testdata/scenarios/sqlite-testseed  # создать БД из сценария
//	go run ./cmd/server/ --materialize testdata/scenarios/sqlite-testseed --force  # пересоздать БД
//
// Переменные окружения:
//
//	PORT          — порт HTTP (по умолчанию 8084)
//	DS_CONFIG     — путь к конфигу (по умолчанию specs/config.example.json)
//	LOG_LEVEL     — info (по умолчанию) или debug
//	ADMIN_TOKEN   — Bearer-токен для /admin/* эндпоинтов (опционально; без токена admin API возвращает 401)
//
// Multi-tenancy: один процесс обслуживает несколько изолированных конфигов.
// Все запросы диспатчатся через TenantStore (default tenant по умолчанию,
// либо X-Tenant-ID заголовок). Tenant CRUD — через /admin/tenants.
// Hot reload конфига — через fsnotify на config-файле (см. cmd/server/main.go → watchConfig).
//
// Seed-режим (dev-only) вынесен в отдельную утилиту cmd/seed-cli.
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
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/go-chi/chi/v5"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/helperium-go/pkg/metrics"
	"github.com/trash2bin/helperium/data-service/internal/configgen"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/seedgen"
	"github.com/trash2bin/helperium/data-service/internal/server"
)

const defaultConfigPath = "specs/config.example.json"

func main() {
	// ── CLI флаги ──
	discoverFlag := flag.Bool("discover", false, "прочитать схему БД и вывести сгенерированный конфиг в stdout")
	cfgPath := flag.String("config", "", "путь к JSON-конфигу (по умолчанию $DS_CONFIG или specs/config.example.json)")
	materializeDir := flag.String("materialize", "", "директория сценария (config.json + seed.json) — создать БД")
	forceFlag := flag.Bool("force", false, "для --materialize: пересоздать БД, даже если уже существует")
	flag.Parse()

	server.InitLogger()
	metrics.RegisterMetrics()

	// ── Materialize-режим: создать БД из сценария и выйти ──
	if *materializeDir != "" {
		if err := runMaterialize(*materializeDir, *forceFlag); err != nil {
			slog.Error("materialize failed", "error", err)
			os.Exit(1)
		}
		return
	}

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

	// ── Determin DSN resolution (для относительных SQLite-путей) ──
	if cfg.DataSource.Driver == config.DriverSQLite && !filepath.IsAbs(cfg.DataSource.DSN) && cfg.DataSource.DSN != ":memory:" && !strings.HasPrefix(cfg.DataSource.DSN, ":memory:?") {
		cfg.DataSource.DSN = filepath.Join(filepath.Dir(absCfgPath), cfg.DataSource.DSN)
	}

	// ── Реестр адаптеров ──
	registry := datasource.NewDefaultRegistry()
	_, ok := registry.Get(string(cfg.DataSource.Driver))
	if !ok {
		slog.Error("unsupported driver", "driver", cfg.DataSource.Driver, "drivers", registry.Drivers())
		os.Exit(1)
	}

	// ── Persist tenant configs в .data/tenants/{id}.json ──
	tenantsDir := os.Getenv("TENANTS_DIR")
	if tenantsDir == "" {
		tenantsDir = filepath.Join(filepath.Dir(absCfgPath), "..", ".data", "tenants")
	}

	// ── TenantStore: multi-tenant foundation (фаза 3.7) ──
	store := server.NewTenantStore(registry, tenantsDir)

	// ── Загружаем все сохранённые tenants из файловой системы ──
	// Это позволяет tenant'ам, добавленным через admin API или agent-db register,
	// пережить рестарт data-service.
	entries, err := os.ReadDir(tenantsDir)
	if err != nil {
		slog.Info("tenants directory not found — creating", "dir", tenantsDir)
		if mkErr := os.MkdirAll(tenantsDir, 0755); mkErr != nil {
			slog.Warn("failed to create tenants directory", "error", mkErr)
		}
	} else {
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			tenantName := strings.TrimSuffix(entry.Name(), ".json")
			tenantCfgPath := filepath.Join(tenantsDir, entry.Name())
			tenantCfg, loadErr := config.Load(tenantCfgPath)
			if loadErr != nil {
				slog.Warn("failed to load tenant config", "tenant", tenantName, "error", loadErr)
				continue
			}
			bCtx, bCancel := context.WithTimeout(context.Background(), 30*time.Second)
			if _, addErr := store.AddTenant(bCtx, tenantName, tenantCfg, tenantCfgPath); addErr != nil {
				slog.Warn("failed to restore tenant", "tenant", tenantName, "error", addErr)
			} else {
				slog.Info("restored tenant from disk", "tenant", tenantName, "path", tenantCfgPath)
			}
			bCancel()
		}
	}

	// ── Bootstrap the default tenant from the config file ──

	// Build admin router (requires introspection adapter)
	adapter, _ := registry.Get(string(cfg.DataSource.Driver))
	var atomicRouter atomic.Value
	adminCtx := &server.AdminContext{
		ConfigPath:   absCfgPath,
		AtomicRouter: &atomicRouter,
	}
	adminRouter := store.BuildAdminRouter(adapter, absCfgPath, adminCtx, cfg)

	// ── Hot reload: fsnotify on config-file ──
	// Now we only reload if a specific tenant is requested or through admin API.
	// But we can still watch the initial config file and reload it as a specific tenant 'default-bootstrap'
	// Or simply remove this if we want strictly Admin API managed tenants.
	// For backward compatibility with the a single-file start, let's add it as a tenant.
	bctx, bcancel := context.WithTimeout(context.Background(), 30*time.Second)
	if _, err := store.AddTenant(bctx, "default", cfg, absCfgPath); err != nil {
		slog.Error("bootstrap initial tenant", "error", err)
		// We continue, but the system starts empty or with this error
	}
	bcancel()

	go watchConfig(absCfgPath, func() {
		rctx, rcancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer rcancel()
		if err := store.ReloadTenant(rctx, "default", absCfgPath); err != nil {
			slog.Error("hot reload: default tenant reload failed", "error", err)
		}
	})

	// ── Top-level router ──
	rootRouter := chi.NewRouter()
	rootRouter.Use(server.RecoveryMiddleware)
	rootRouter.Use(server.RequestIDMiddleware)
	rootRouter.Use(server.StructuredLoggingMiddleware)
	rootRouter.Use(server.TenantIDMiddleware("X-Tenant-ID"))

	// Mount admin endpoints separately to avoid routing conflicts
	rootRouter.Mount("/admin", adminRouter)
	rootRouter.Mount("/", store)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8084"
	}

	addr := fmt.Sprintf(":%s", port)

	httpServer := &http.Server{
		Addr:         addr,
		Handler:      rootRouter,
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

// runMaterialize читает config.json (+ опциональный seed.json) из директории
// сценария и создаёт БД.
//
// Если seed.json отсутствует — БД создаётся со схемой, но без данных.
// Это нужно для сценариев с bootstrap.sh (например, 'shop'), где начальные
// данные генерируются отдельным скриптом, а в config описаны только сущности
// и endpoint'ы.
func runMaterialize(dir string, force bool) error {
	absDir, err := filepath.Abs(dir)
	if err != nil {
		return fmt.Errorf("resolve scenario dir: %w", err)
	}

	cfg, err := config.Load(filepath.Join(absDir, "config.json"))
	if err != nil {
		return fmt.Errorf("load config.json: %w", err)
	}

	seedPath := filepath.Join(absDir, "seed.json")
	var seed *seedgen.Seed
	if _, statErr := os.Stat(seedPath); statErr == nil {
		seed, err = seedgen.Load(seedPath)
		if err != nil {
			return fmt.Errorf("load seed.json: %w", err)
		}
	} else {
		slog.Info("seed.json not found — schema only, no data loaded")
	}

	registry := datasource.NewDefaultRegistry()
	adapter, ok := registry.Get(string(cfg.DataSource.Driver))
	if !ok {
		return fmt.Errorf("unsupported driver: %s", cfg.DataSource.Driver)
	}

	ctx := context.Background()
	if err := seedgen.Materialize(ctx, adapter, cfg, seed, absDir, force); err != nil {
		return err
	}

	slog.Info("materialize: done", "dir", absDir, "driver", cfg.DataSource.Driver, "dsn", cfg.DataSource.DSN)
	return nil
}

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
	defer func() { _ = conn.Close() }()

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

// watchConfig отслеживает изменения config-файла через fsnotify и вызывает
// onReload при каждом изменении. Не следит за рекурсивными директориями —
// только за файлом конфига.
//
// Требует абсолютного пути.
func watchConfig(configPath string, onReload func()) {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		slog.Error("fsnotify create watcher", "error", err)
		return
	}

	configDir := filepath.Dir(configPath)
	if err := watcher.Add(configDir); err != nil {
		slog.Error("fsnotify add directory", "dir", configDir, "error", err)
		return
	}

	slog.Info("hot reload: watching config", "path", configPath)

	// Debounce: несколько событий подряд за N мс → один перезапуск
	var debounceTimer *time.Timer
	const debounce = 500 * time.Millisecond

	go func() {
		defer func() { _ = watcher.Close() }()
		for {
			select {
			case event, ok := <-watcher.Events:
				if !ok {
					return
				}
				// Игнорируем события кроме WRITE/CREATE для нашего файла
				if event.Op&(fsnotify.Write|fsnotify.Create) == 0 {
					continue
				}
				// Разрешаем как точное совпадение, так и события на директории
				if event.Name != configPath && event.Name == configDir {
					// CREATE на директории — проверим stat файла
					if _, err := os.Stat(configPath); os.IsNotExist(err) {
						continue
					}
				} else if event.Name != configPath {
					continue
				}
				slog.Debug("hot reload: config change detected", "event", event.String())
				if debounceTimer != nil {
					debounceTimer.Stop()
				}
				debounceTimer = time.AfterFunc(debounce, onReload)
			case err, ok := <-watcher.Errors:
				if !ok {
					return
				}
				slog.Error("fsnotify error", "error", err)
			}
		}
	}()
}
