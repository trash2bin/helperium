package main

import (
	"net/http"
	"testing"
	"time"
)

// withFakeClock overrides the package-level nowFunc used by
// streamableTenantRegistry for idle-eviction timestamps and restores the real
// clock when the test finishes.
func withFakeClock(t *testing.T, fake *fakeClock) {
	t.Helper()
	previous := nowFunc
	nowFunc = fake.Now
	t.Cleanup(func() { nowFunc = previous })
}

// fakeClock is a deterministic time source for eviction tests. Advancing it
// simulates the passage of time without sleeping.
type fakeClock struct {
	current time.Time
}

func newFakeClock(t *testing.T) *fakeClock {
	t.Helper()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	clk := &fakeClock{current: base}
	withFakeClock(t, clk)
	return clk
}

func (c *fakeClock) Now() time.Time { return c.current }

func (c *fakeClock) advance(d time.Duration) { c.current = c.current.Add(d) }

// seededRegistry returns a registry pre-populated with occupied scopes. The
// stored handlers are stubs, so no data-service manifests are loaded. Use
// configureManifestClient when a test must exercise the insert path.
func seededRegistry(t *testing.T, max int, keys ...string) *streamableTenantRegistry {
	t.Helper()
	registry := &streamableTenantRegistry{
		handlers:   make(map[string]http.Handler),
		lastAccess: make(map[string]time.Time),
		max:        max,
	}
	for _, key := range keys {
		registry.handlers[key] = http.NotFoundHandler()
		registry.lastAccess[key] = nowFunc()
	}
	return registry
}

func TestRegistryAtCapacityFreshEntriesStillRejected(t *testing.T) {
	clk := newFakeClock(t)
	registry := seededRegistry(t, 1, "tenant-a")

	_, err := registry.handlerFor([]string{"tenant-b"})

	if err != errMaxStreamableTenantScopes {
		t.Fatalf("handlerFor() error = %v, want errMaxStreamableTenantScopes", err)
	}
	if len(registry.handlers) != 1 || len(registry.lastAccess) != 1 {
		t.Fatalf("registry mutated at capacity: handlers=%d lastAccess=%d", len(registry.handlers), len(registry.lastAccess))
	}
	_ = clk
}

func TestRegistryEvictsIdleScopeAtCapacity(t *testing.T) {
	configureManifestClient(t)
	clk := newFakeClock(t)
	registry := seededRegistry(t, 1, "tenant-a")

	// Age the only entry past the idle TTL.
	clk.advance(registryIdleEvictionTTL + time.Minute)

	handler, err := registry.handlerFor([]string{"tenant-b"})

	if err != nil {
		t.Fatalf("handlerFor() error = %v, want nil after idle eviction", err)
	}
	if handler == nil {
		t.Fatal("handlerFor() returned nil handler without error")
	}
	if _, ok := registry.handlers["tenant-a"]; ok {
		t.Error("idle scope tenant-a was not evicted from handlers")
	}
	if _, ok := registry.lastAccess["tenant-b"]; !ok {
		t.Error("new scope tenant-b missing from lastAccess")
	}
	if _, ok := registry.handlers["tenant-b"]; !ok {
		t.Error("new scope tenant-b missing from handlers")
	}
}

func TestRegistryLastAccessRefreshedOnCacheHit(t *testing.T) {
	clk := newFakeClock(t)
	registry := seededRegistry(t, 1, "tenant-a")
	stale := registry.lastAccess["tenant-a"]

	// A cache hit well after insertion must refresh lastAccess so the entry
	// is not treated as idle.
	clk.advance(10 * time.Minute)
	if _, err := registry.handlerFor([]string{"tenant-a"}); err != nil {
		t.Fatalf("handlerFor() cache hit error = %v", err)
	}
	if !registry.lastAccess["tenant-a"].After(stale) {
		t.Fatal("lastAccess was not refreshed on cache hit")
	}

	// Past the TTL measured from the *refreshed* timestamp: still rejected,
	// because the entry was recently accessed.
	clk.advance(registryIdleEvictionTTL - 11*time.Minute)
	if _, err := registry.handlerFor([]string{"tenant-b"}); err != errMaxStreamableTenantScopes {
		t.Fatalf("recently-accessed entry was evicted; err = %v, want errMaxStreamableTenantScopes", err)
	}
	if _, ok := registry.handlers["tenant-a"]; !ok {
		t.Fatal("recently-accessed scope tenant-a was evicted")
	}
}

func TestRegistryMapsStayConsistentAfterEvictions(t *testing.T) {
	configureManifestClient(t)
	clk := newFakeClock(t)
	registry := seededRegistry(t, 2, "tenant-a", "tenant-b")

	// Age both entries past the TTL and insert two new scopes in sequence:
	// each insert path must evict one idle entry and keep both maps aligned.
	clk.advance(registryIdleEvictionTTL + time.Minute)
	if _, err := registry.handlerFor([]string{"tenant-c"}); err != nil {
		t.Fatalf("handlerFor(tenant-c) error = %v", err)
	}
	clk.advance(registryIdleEvictionTTL + time.Minute)
	if _, err := registry.handlerFor([]string{"tenant-d"}); err != nil {
		t.Fatalf("handlerFor(tenant-d) error = %v", err)
	}

	if len(registry.handlers) != len(registry.lastAccess) {
		t.Fatalf("maps diverged: handlers=%d lastAccess=%d", len(registry.handlers), len(registry.lastAccess))
	}
	if len(registry.handlers) > registry.max {
		t.Fatalf("registry exceeded cap: %d > %d", len(registry.handlers), registry.max)
	}
	// Only the freshly inserted scopes survive both eviction rounds.
	for _, key := range []string{"tenant-a", "tenant-b"} {
		if _, ok := registry.handlers[key]; ok {
			t.Errorf("stale scope %s survived eviction", key)
		}
	}
	for _, key := range []string{"tenant-c", "tenant-d"} {
		if _, ok := registry.handlers[key]; !ok {
			t.Errorf("fresh scope %s missing after eviction", key)
		}
	}
}

func TestRegistryZeroTTLDisablesIdleEviction(t *testing.T) {
	previous := registryIdleEvictionTTL
	registryIdleEvictionTTL = 0
	t.Cleanup(func() { registryIdleEvictionTTL = previous })

	clk := newFakeClock(t)
	registry := seededRegistry(t, 1, "tenant-a")
	clk.advance(time.Hour)

	if _, err := registry.handlerFor([]string{"tenant-b"}); err != errMaxStreamableTenantScopes {
		t.Fatalf("TTL=0 must disable eviction; err = %v, want errMaxStreamableTenantScopes", err)
	}
}
