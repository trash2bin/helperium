// ── Health ──

package server

import (
	"context"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
)

// TenantHealth is the DTO for per-tenant health status.
type TenantHealth struct {
	ID       string `json:"id"`
	Driver   string `json:"driver"`
	Status   string `json:"status"`
	Error    string `json:"error,omitempty"`
	Entities int    `json:"entities"`
}

// HealthCheck pings all tenant databases and returns aggregated status.
//
// The per-tenant config fields are snapshotted under ts.mu.RLock before the
// goroutines start, so a concurrent ReloadTenant (which swaps inst.Config under
// ts.mu.Lock) cannot race with the reads in tenant_health.go:38-39. Concurrency
// is bounded by a semaphore (maxConcurrentPings) so N tenants never spawn N
// unbounded goroutines per /health request.
const maxConcurrentHealthPings = 8

func (ts *TenantStore) HealthCheck(ctx context.Context) []TenantHealth {
	instances := ts.ListTenants()

	// Snapshot the config-derived fields under RLock: ListTenants releases the
	// lock before returning, so reading ti.Config here without the lock would
	// race with ReloadTenant. Copy the fields we need up-front.
	snapshots := make([]tenantHealthSnapshot, 0, len(instances))
	ts.mu.RLock()
	for _, inst := range instances {
		// Skip tenants that are being removed — their pools are being drained.
		if inst.removing.Load() {
			continue
		}
		if inst.Config == nil {
			snapshots = append(snapshots, tenantHealthSnapshot{ID: inst.ID})
			continue
		}
		snapshots = append(snapshots, tenantHealthSnapshot{
			ID:       inst.ID,
			Driver:   string(inst.Config.DataSource.Driver),
			Entities: len(inst.Config.Entities),
			Conn:     inst.Conn,
		})
	}
	ts.mu.RUnlock()

	results := make([]TenantHealth, 0, len(snapshots))
	sem := make(chan struct{}, maxConcurrentHealthPings)

	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, snap := range snapshots {
		snap := snap
		wg.Add(1)
		go func() {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			h := TenantHealth{
				ID:       snap.ID,
				Driver:   snap.Driver,
				Entities: snap.Entities,
			}

			// Health from Ping, or assume healthy if no Conn (e.g. test instances)
			if snap.Conn != nil {
				pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
				defer cancel()

				if err := snap.Conn.PingContext(pingCtx); err != nil {
					h.Status = "unhealthy"
					h.Error = err.Error()
				} else {
					h.Status = "healthy"
				}
			} else {
				h.Status = "healthy"
			}

			mu.Lock()
			results = append(results, h)
			mu.Unlock()
		}()
	}

	wg.Wait()

	// Sort by ID for deterministic output
	sort.Slice(results, func(i, j int) bool {
		return results[i].ID < results[j].ID
	})
	return results
}

// tenantHealthSnapshot holds the config-derived fields needed by HealthCheck,
// copied under ts.mu.RLock to avoid racing with ReloadTenant.
type tenantHealthSnapshot struct {
	ID       string
	Driver   string
	Entities int
	Conn     datasource.Conn
}

// multiTenantHealthHandler serves GET /health with per-tenant status.
func (ts *TenantStore) multiTenantHealthHandler(w http.ResponseWriter, r *http.Request) {
	health := ts.HealthCheck(r.Context())

	// Backward-compatible single-tenant response
	if len(health) == 1 && health[0].Status == "healthy" {
		handlers.RespondJSON(w, http.StatusOK, map[string]string{"status": "ok"})
		return
	}

	// Multi-tenant / degraded response
	overall := computeOverallStatus(health)
	statusCode := http.StatusOK
	if overall == "unhealthy" {
		statusCode = http.StatusServiceUnavailable
	}

	handlers.RespondJSON(w, statusCode, map[string]any{
		"status":  overall,
		"tenants": health,
	})
}

func computeOverallStatus(health []TenantHealth) string {
	if len(health) == 0 {
		return "unhealthy"
	}
	allHealthy := true
	anyHealthy := false
	for _, h := range health {
		if h.Status == "healthy" {
			anyHealthy = true
		} else {
			allHealthy = false
		}
	}
	if allHealthy {
		return "healthy"
	}
	if anyHealthy {
		return "degraded"
	}
	return "unhealthy"
}
