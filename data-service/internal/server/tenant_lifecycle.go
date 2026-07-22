// ── Tenant Lifecycle ──

package server

import (
	"context"
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
		// Clean up the connection we just opened
		_ = inst.Conn.Close()
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
func (ts *TenantStore) RemoveTenant(ctx context.Context, id string) error {
	ts.mu.Lock()
	inst, ok := ts.tenants[id]
	if !ok {
		ts.mu.Unlock()
		return fmt.Errorf("tenant %q not found", id)
	}
	delete(ts.tenants, id)
	ts.mu.Unlock()

	// Close connection outside the lock to avoid blocking readers
	if inst.Conn != nil {
		if err := inst.Conn.Close(); err != nil {
			slog.Warn("tenant store: error closing connection", "id", id, "error", err)
		}
	}
	if inst.ReadonlyConn != nil {
		if err := inst.ReadonlyConn.Close(); err != nil {
			slog.Warn("tenant store: error closing readonly connection", "id", id, "error", err)
		}
	}

	slog.Info("tenant store: tenant removed", "id", id)
	return nil
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

	// Build new router using existing connection, preserving approved tools
	approvedTools := make(map[string]bool)
	for _, p := range newCfg.ApprovedTools {
		approvedTools[p.Endpoint] = true
	}
	newRouter, err := NewRouterFromConfig(ts, newCfg, inst.AdapterSub, approvedTools)
	if err != nil {
		return fmt.Errorf("reload tenant %q: build router: %w", tenantID, err)
	}

	ts.mu.Lock()
	inst.Config = newCfg
	inst.Router = newRouter
	inst.ConfigPath = configPath
	inst.ApprovedTools = approvedTools
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
	// Build approved tools map from config
	approvedTools := make(map[string]bool)
	for _, p := range cfg.ApprovedTools {
		approvedTools[p.Endpoint] = true
	}
	router, err := NewRouterFromConfig(ts, cfg, adapterSub, approvedTools)
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
		ApprovedTools: approvedTools,
	}, nil
}
