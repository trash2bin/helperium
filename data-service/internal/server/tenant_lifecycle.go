// ── Tenant Lifecycle ──

package server

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// RegisterTenantInstance registers a pre-built TenantInstance directly.
// Used by tests that already have an adapter and router — bypasses
// DB connection opening so in-memory DBs persist across the seed-build-test cycle.
func (ts *TenantStore) RegisterTenantInstance(inst *TenantInstance) error {
	if inst.healthMu == nil {
		inst.healthMu = &sync.Mutex{}
	}
	if inst.schemaMu == nil {
		inst.schemaMu = &sync.RWMutex{}
	}
	ts.mu.Lock()
	defer ts.mu.Unlock()
	if _, exists := ts.tenants[inst.ID]; exists {
		return fmt.Errorf("tenant %q already exists", inst.ID)
	}
	ts.tenants[inst.ID] = inst
	return nil
}

// AddTenant creates a new TenantInstance: validates config, connects DB,
// builds router, and stores it atomically.
func (ts *TenantStore) AddTenant(ctx context.Context, id string, cfg *config.Config, configPath string) (*TenantInstance, error) {
	ts.mu.RLock()
	_, exists := ts.tenants[id]
	ts.mu.RUnlock()
	if exists {
		return nil, fmt.Errorf("tenant %q already exists", id)
	}

	inst, err := buildTenantInstance(ctx, ts, ts.registry, id, cfg, configPath)
	if err != nil {
		return nil, fmt.Errorf("add tenant %q: %w", id, err)
	}

	ts.mu.Lock()
	// Double-check after acquiring write lock
	if _, exists := ts.tenants[id]; exists {
		ts.mu.Unlock()
		// Clean up connections we just opened — both main and readonly pools,
		// otherwise the readonly pool (readonly_dsn) leaks on duplicate AddTenant.
		closeTenantConns(inst)
		return nil, fmt.Errorf("tenant %q already exists", id)
	}
	ts.tenants[id] = inst
	ts.mu.Unlock()

	slog.Info("tenant store: tenant added",
		"id", id,
		"driver", cfg.DataSource.Driver,
		"entities", len(cfg.Entities),
		"endpoints", len(cfg.Endpoints),
	)
	return inst, nil
}

// RemoveTenant removes a tenant and closes its connection pool.
//
// Two-phase removal so in-flight requests (that already resolved the instance)
// are not served from a closed pool:
//  1. Under ts.mu.Lock: deregister from the map and mark inst.removing — new
//     lookups (resolveTenant/resolveTenantAndLock) immediately return nil/404.
//  2. Outside the lock: drain the pool — stop reusing idle connections and wait
//     for currently-executing queries to return their connections (bounded wait).
//  3. closeTenantConns — the actual Close() once no more work can start.
func (ts *TenantStore) RemoveTenant(ctx context.Context, id string) error {
	ts.mu.Lock()
	inst, ok := ts.tenants[id]
	if !ok {
		ts.mu.Unlock()
		return fmt.Errorf("tenant %q not found", id)
	}
	delete(ts.tenants, id)
	inst.removing.Store(true)
	ts.mu.Unlock()

	// Drain in-flight connections before closing. Requests that already hold an
	// RLock (via resolveTenantAndLock) keep the instance pinned until they finish;
	// we wait for their queries to return the connections back to the pool.
	drainTenantConns(ctx, inst)

	// Close connection outside the lock to avoid blocking readers.
	closeTenantConns(inst)

	slog.Info("tenant store: tenant removed", "id", id)
	return nil
}

// drainTenantConns waits for in-flight queries to finish before Close().
// It sets MaxIdleConns(0) so no new idle connections are retained, then polls
// the pool's InUse counter until it hits zero or the drain deadline elapses.
// The underlying connection is *sql.DB for both adapters; if the type-assert
// fails (custom Conn implementation), it falls back to a short grace period.
func drainTenantConns(ctx context.Context, inst *TenantInstance) {
	if inst == nil {
		return
	}

	// Bound the drain wait. 2s matches the health-ping timeout and the typical
	// query budget; long-running queries will still get their results, they just
	// won't block removal forever.
	drainCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	drained := func() bool {
		db, ok := asSQLDB(inst.Conn)
		if !ok {
			return false // cannot introspect — fall back to grace period below
		}
		db.SetMaxIdleConns(0)
		for {
			if drainCtx.Err() != nil {
				return false
			}
			stats := db.Stats()
			if stats.InUse == 0 {
				return true
			}
			time.Sleep(10 * time.Millisecond)
		}
	}()

	if !drained {
		// Either the pool isn't a *sql.DB, or in-flight work is still running at
		// the deadline. Give the pool a short grace period before Close() so
		// already-started requests can finish their queries.
		select {
		case <-ctx.Done():
		case <-time.After(2 * time.Second):
		}
	}
}

// asSQLDB returns the underlying *sql.DB if the Conn is backed by one.
func asSQLDB(c datasource.Conn) (*sql.DB, bool) {
	db, ok := c.(*sql.DB)
	return db, ok
}

// closeTenantConns closes both the main and the read-only connection pools
// of a TenantInstance, logging any errors. Safe to call on a partially-built
// instance (nil fields are skipped).
func closeTenantConns(inst *TenantInstance) {
	if inst == nil {
		return
	}
	if inst.Conn != nil {
		if err := inst.Conn.Close(); err != nil {
			slog.Warn("tenant store: error closing connection", "id", inst.ID, "error", err)
		}
	}
	if inst.ReadonlyConn != nil {
		if err := inst.ReadonlyConn.Close(); err != nil {
			slog.Warn("tenant store: error closing readonly connection", "id", inst.ID, "error", err)
		}
	}
}

// GetTenant returns the TenantInstance for the given id, or (nil, false).
func (ts *TenantStore) GetTenant(id string) (*TenantInstance, bool) {
	ts.mu.RLock()
	inst, ok := ts.tenants[id]
	ts.mu.RUnlock()
	return inst, ok
}

// ListTenants returns a snapshot of all tenants, sorted by creation time.
func (ts *TenantStore) ListTenants() []*TenantInstance {
	ts.mu.RLock()
	result := make([]*TenantInstance, 0, len(ts.tenants))
	for _, inst := range ts.tenants {
		result = append(result, inst)
	}
	ts.mu.RUnlock()

	sort.Slice(result, func(i, j int) bool {
		return result[i].CreatedAt.Before(result[j].CreatedAt)
	})
	return result
}

// ReloadTenant reloads the config for a specific tenant from disk and rebuilds its router.
// Если изменился DSN/ReadonlyDSN/Driver — пересоздаёт соединение целиком
// (buildTenantInstance) и подменяет inst; иначе переиспользует существующий
// AdapterSub (лёгкий путь — только router rebuild).
func (ts *TenantStore) ReloadTenant(ctx context.Context, tenantID string, configPath string) error {
	newCfg, err := config.Load(configPath)
	if err != nil {
		return fmt.Errorf("reload tenant %q: load config: %w", tenantID, err)
	}

	ts.mu.RLock()
	inst, ok := ts.tenants[tenantID]
	ts.mu.RUnlock()
	if !ok {
		return fmt.Errorf("reload tenant %q: not found", tenantID)
	}

	// DSN изменился? Тогда валидированный dry-run'ом конфиг должен реально
	// переподключиться (иначе изменение DSN молча игнорируется).
	dsnChanged := inst.Config == nil ||
		inst.Config.DataSource.DSN != newCfg.DataSource.DSN ||
		inst.Config.DataSource.ReadonlyDSN != newCfg.DataSource.ReadonlyDSN ||
		inst.Config.DataSource.Driver != newCfg.DataSource.Driver

	if dsnChanged {
		newInst, err := buildTenantInstance(ctx, ts, ts.registry, tenantID, newCfg, configPath)
		if err != nil {
			return fmt.Errorf("reload tenant %q: rebuild instance: %w", tenantID, err)
		}
		oldInst := inst
		ts.mu.Lock()
		_, ok := ts.tenants[tenantID]
		if ok {
			ts.tenants[tenantID] = newInst
		}
		ts.mu.Unlock()
		if !ok {
			// Тенант удалён между lookup и rebuild — не публикуем, закрываем новое.
			if newInst.Conn != nil {
				_ = newInst.Conn.Close()
			}
			if newInst.ReadonlyConn != nil {
				_ = newInst.ReadonlyConn.Close()
			}
			return fmt.Errorf("reload tenant %q: removed during reload", tenantID)
		}
		// Закрываем старые коннекты ПОСЛЕ публикации нового inst
		// (in-flight запросы со старым Router завершат работу по нему).
		if oldInst != nil {
			if oldInst.Conn != nil {
				_ = oldInst.Conn.Close()
			}
			if oldInst.ReadonlyConn != nil {
				_ = oldInst.ReadonlyConn.Close()
			}
		}
		slog.Info("tenant store: config reloaded (DSN changed, reconnected)",
			"tenant", tenantID,
			"entities", len(newCfg.Entities),
			"endpoints", len(newCfg.Endpoints),
		)
		return nil
	}

	// Build new router using existing connection
	newRouter, err := NewRouterFromConfig(ts, newCfg, inst.AdapterSub)
	if err != nil {
		return fmt.Errorf("reload tenant %q: build router: %w", tenantID, err)
	}

	ts.mu.Lock()
	inst.Config = newCfg
	inst.Router = newRouter
	inst.ConfigPath = configPath
	ts.mu.Unlock()

	slog.Info("tenant store: config reloaded",
		"tenant", tenantID,
		"entities", len(newCfg.Entities),
		"endpoints", len(newCfg.Endpoints),
	)
	return nil
}

// ── buildTenantInstance ──

// buildTenantInstance validates config, connects to DB, and builds a router.
// Used by both SetDefault and AddTenant.
func buildTenantInstance(ctx context.Context, ts *TenantStore, registry *datasource.Registry, id string, cfg *config.Config, configPath string) (*TenantInstance, error) {
	adapter, ok := registry.Get(string(cfg.DataSource.Driver))
	if !ok {
		return nil, fmt.Errorf("unsupported driver: %s", cfg.DataSource.Driver)
	}

	resolvePath := func(dsn string) string {
		// URL-формат (postgres://, file:, etc.) — не трогаем, это not a file path
		if strings.Contains(dsn, "://") {
			return dsn
		}
		if dsn != "" && !filepath.IsAbs(dsn) && configPath != "" {
			return filepath.Join(filepath.Dir(configPath), dsn)
		}
		return dsn
	}

	// Main connection (readwrite DSN — для admin/introspection/health)
	dsn := resolvePath(cfg.DataSource.DSN)
	conn, err := adapter.Connect(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("connect to database: %w", err)
	}

	// Read-only connection (если задан readonly_dsn — database-level изоляция)
	var readonlyConn datasource.Conn
	readonlyDSN := cfg.DataSource.ReadonlyDSN
	if readonlyDSN != "" {
		readonlyDSN = resolvePath(readonlyDSN)
		roConn, err := adapter.Connect(ctx, readonlyDSN)
		if err != nil {
			_ = conn.Close()
			return nil, fmt.Errorf("connect to readonly database: %w", err)
		}
		readonlyConn = roConn
		slog.Info("tenant: read-only connection established",
			"id", id, "readonly_dsn", readonlyDSN)
	}

	// AdapterSub для хендлеров: если read-only коннект есть — используем его
	adapterSubConn := conn
	if readonlyConn != nil {
		adapterSubConn = readonlyConn
	}
	// ReadOnlyConn обёртка — блокирует ExecContext на уровне Go.
	// Все data-запросы идут через неё; admin/introspection — через оригинальную conn.
	queryConn := datasource.NewReadOnlyConn(adapterSubConn)
	adapterSub := &runtime.InstrumentedAdapter{Conn: queryConn, Adp: adapter}

	// Build router (no admin endpoints — those are on TenantStore)
	router, err := NewRouterFromConfig(ts, cfg, adapterSub)
	if err != nil {
		_ = conn.Close()
		if readonlyConn != nil {
			_ = readonlyConn.Close()
		}
		return nil, fmt.Errorf("build router: %w", err)
	}

	return &TenantInstance{
		ID:            id,
		Config:        cfg,
		Conn:          conn,
		ReadonlyConn:  readonlyConn,
		Adapter:       adapter,
		AdapterSub:    adapterSub,
		Router:        router,
		ConfigPath:    configPath,
		CreatedAt:     time.Now(),
		Healthy:       true,
		healthMu:      &sync.Mutex{},
		schemaMu:      &sync.RWMutex{},
	}, nil
}
