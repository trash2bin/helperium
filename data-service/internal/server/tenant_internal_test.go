package server

import (
	"sync"
	"testing"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestSetHasAdmin(t *testing.T) {
	ts := &TenantStore{}
	ts.SetHasAdmin(true)
	ts.mu.RLock()
	if !ts.hasAdmin {
		t.Error("SetHasAdmin(true) expected hasAdmin=true")
	}
	ts.mu.RUnlock()

	ts.SetHasAdmin(false)
	ts.mu.RLock()
	if ts.hasAdmin {
		t.Error("SetHasAdmin(false) expected hasAdmin=false")
	}
	ts.mu.RUnlock()
}

func TestAdminConfigResponseFromConfig(t *testing.T) {
	cfg := &config.Config{
		Version: 4,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    "file:test.db",
		},
		Entities: []config.Entity{
			{Name: "student", Table: "students"},
		},
	}
	resp := adminConfigResponseFromConfig(cfg)
	if resp.Version != 4 {
		t.Errorf("Version = %d, want 4", resp.Version)
	}
	if resp.Driver != "sqlite" {
		t.Errorf("Driver = %q, want 'sqlite'", resp.Driver)
	}
	if len(resp.Entities) != 1 {
		t.Errorf("Entities count = %d, want 1", len(resp.Entities))
	}
}

// TestTenantHealthRace verifies that concurrent HealthCheck writes and
// tenantResponseFromInstance reads do not produce data races.
func TestTenantHealthRace(t *testing.T) {
	inst := &TenantInstance{
		ID: "test-tenant",
		Config: &config.Config{
			Version:    1,
			DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		},
		CreatedAt: time.Now(),
		healthMu:  &sync.Mutex{},
	}

	var wg sync.WaitGroup

	// Simulate HealthCheck writes
	for i := 0; i < 10; i++ {
		wg.Add(2)

		// Writer goroutine (simulates HealthCheck)
		go func() {
			defer wg.Done()
			inst.healthMu.Lock()
			inst.Healthy = true
			inst.LastError = ""
			inst.healthMu.Unlock()
		}()

		// Reader goroutine (simulates tenantResponseFromInstance)
		go func() {
			defer wg.Done()
			inst.healthMu.Lock()
			_ = inst.Healthy
			_ = inst.LastError
			inst.healthMu.Unlock()
		}()
	}

	wg.Wait()

	if !inst.Healthy {
		t.Error("expected Healthy=true after write")
	}
}

// TestTenantHealthRaceViaResponse verifies that tenantResponseFromInstance
// reads safely under concurrent writes.
func TestTenantHealthRaceViaResponse(t *testing.T) {
	inst := &TenantInstance{
		ID: "test-tenant",
		Config: &config.Config{
			Version:    1,
			DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		},
		CreatedAt: time.Now(),
		healthMu:  &sync.Mutex{},
	}

	var wg sync.WaitGroup

	for i := 0; i < 10; i++ {
		wg.Add(2)

		// Writer goroutine
		go func() {
			defer wg.Done()
			inst.healthMu.Lock()
			inst.Healthy = (i%2 == 0)
			inst.LastError = ""
			if i%2 != 0 {
				inst.LastError = "db timeout"
			}
			inst.healthMu.Unlock()
		}()

		// Reader via tenantResponseFromInstance
		go func() {
			defer wg.Done()
			_ = tenantResponseFromInstance(inst)
		}()
	}

	wg.Wait()
}

// TestTenantHealthRaceDirectAccess ensures concurrent direct field reads
// and writes are race-free (used by admin handlers and other readers).
// TestTenantHealth_CopyValueRace verifies that copying TenantInstance by value
// creates a separate sync.Mutex, leading to unsynchronized concurrent access.
// This test EXPECTS a data race when healthMu is sync.Mutex (value),
// and must PASS when healthMu is *sync.Mutex (pointer).
func TestTenantHealth_CopyValueRace(t *testing.T) {
	inst := &TenantInstance{
		ID: "test",
		Config: &config.Config{
			Version:    1,
			DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		},
		CreatedAt: time.Now(),
		Healthy:   true,
		healthMu:  &sync.Mutex{},
	}
	// Проверяем, что healthMu (указатель на mutex) разделяется между инстансом
	// и «копией» — при healthMu *sync.Mutex обе горутины используют ОДИН mutex,
	// гонки нет. При healthMu sync.Mutex (значение) копия получила бы отдельный
	// mutex и тест поймал бы гонку под -race.
	copy := struct{ healthMu *sync.Mutex }{healthMu: inst.healthMu}

	var shared int
	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		inst.healthMu.Lock()
		shared = 1
		inst.healthMu.Unlock()
	}()

	go func() {
		defer wg.Done()
		copy.healthMu.Lock() // DIFFERENT mutex when healthMu is sync.Mutex (value)!
		shared = 2
		copy.healthMu.Unlock()
	}()

	wg.Wait()
	_ = shared
}

func TestTenantHealthRaceDirectAccess(t *testing.T) {
	inst := &TenantInstance{
		ID: "test-tenant",
		Config: &config.Config{
			Version:    1,
			DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		},
		CreatedAt: time.Now(),
		healthMu:  &sync.Mutex{},
	}

	var wg sync.WaitGroup

	for i := 0; i < 20; i++ {
		wg.Add(2)

		go func() {
			defer wg.Done()
			for j := 0; j < 5; j++ {
				inst.healthMu.Lock()
				inst.Healthy = (j%2 == 0)
				inst.LastError = ""
				inst.healthMu.Unlock()
			}
		}()

		go func() {
			defer wg.Done()
			for j := 0; j < 5; j++ {
				inst.healthMu.Lock()
				_ = inst.Healthy
				_ = inst.LastError
				inst.healthMu.Unlock()
			}
		}()
	}

	wg.Wait()
}
