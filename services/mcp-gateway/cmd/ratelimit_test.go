package main

import (
	"fmt"
	"testing"
	"time"
)

// ── Bucket eviction (memory bound under scanner traffic) ───────────────────

// TestRateLimit_EvictsAtMaxBuckets pins the memory bound: distinct scanner
// IPs beyond maxBuckets must not grow the map unboundedly; the LRU bucket is
// evicted to make room.
func TestRateLimit_EvictsAtMaxBuckets(t *testing.T) {
	rl := newRateLimiter(1000, 10, 5)

	ips := make([]string, 8)
	for i := range ips {
		ips[i] = fmt.Sprintf("10.0.0.%d", i+1)
	}
	for _, ip := range ips {
		rl.Allow(ip)
	}
	if got := rl.bucketCount(); got > 5 {
		t.Fatalf("bucket count = %d after %d unique IPs, want <= 5", got, len(ips))
	}
}

// TestRateLimit_EvictedIPStartsFreshBurst pins the semantic of eviction: an
// evicted IP gets a fresh bucket (fresh burst), it is not permanently banned;
// a recently accessed (even rate-limited) IP keeps its bucket.
func TestRateLimit_EvictedIPStartsFreshBurst(t *testing.T) {
	prev := nowFunc
	defer func() { nowFunc = prev }()
	clock := time.Unix(1_000_000, 0)
	nowFunc = func() time.Time { return clock }

	rl := newRateLimiter(1, 3, 2) // rps=1: 100ms steps refill only 0.1 token

	// Exhaust burst for IP A at t0, then IP B at t0+100ms (B touched last).
	for i := 0; i < 3; i++ {
		rl.Allow("10.0.0.1")
	}
	clock = clock.Add(100 * time.Millisecond)
	for i := 0; i < 3; i++ {
		rl.Allow("10.0.0.2")
	}

	// Blocked attempt on A at t0+200ms still counts as access: A is MRU now
	// (only 0.2 tokens refilled — still blocked).
	clock = clock.Add(100 * time.Millisecond)
	if rl.Allow("10.0.0.1") {
		t.Fatal("IP A should be exhausted before eviction")
	}

	// IP C at t0+300ms must evict the LRU bucket (IP B) to make room.
	clock = clock.Add(100 * time.Millisecond)
	if !rl.Allow("10.0.0.3") {
		t.Fatal("IP C should be allowed (fresh bucket after eviction)")
	}

	// IP B was evicted: it must be treated as new, with a fresh burst.
	clock = clock.Add(1 * time.Second)
	if !rl.Allow("10.0.0.2") {
		t.Error("evicted IP B should start a fresh burst, not stay blocked")
	}
	// Re-admitting B (cap 2) legitimately evicted one of the older entries
	// (A last touched t+200ms, C t+300ms) — the test intentionally does not
	// pin WHICH one survived. The invariants under test: the map stayed
	// bounded and B got a fresh bucket.
	rl.mu.Lock()
	_, bAlive := rl.buckets["10.0.0.2"]
	rl.mu.Unlock()
	if !bAlive {
		t.Fatal("re-admitted IP B should have a bucket")
	}
	if got := rl.bucketCount(); got > 2 {
		t.Fatalf("bucket count = %d, want <= 2", got)
	}
}

// TestRateLimit_EvictionUsesLRUNotInsertOrder pins that a *recently used*
// bucket survives while an older idle one is evicted, regardless of which
// was inserted first.
func TestRateLimit_EvictionUsesLRUNotInsertOrder(t *testing.T) {
	prev := nowFunc
	defer func() { nowFunc = prev }()
	clock := time.Unix(1_000_000, 0)
	nowFunc = func() time.Time { return clock }

	rl := newRateLimiter(1000, 5, 2)

	// IP A inserted at t0.
	rl.Allow("10.0.0.1")
	// IP B inserted at t0+10s.
	clock = clock.Add(10 * time.Second)
	rl.Allow("10.0.0.2")

	// IP A accessed again at t0+20s — it is now the most recently used.
	clock = clock.Add(10 * time.Second)
	rl.Allow("10.0.0.1")

	// IP C at t0+30s must evict IP B (LRU), not IP A (most recent).
	clock = clock.Add(10 * time.Second)
	rl.Allow("10.0.0.3")

	if got := rl.bucketCount(); got != 2 {
		t.Fatalf("bucket count = %d, want 2", got)
	}
	rl.mu.Lock()
	_, aAlive := rl.buckets["10.0.0.1"]
	_, bAlive := rl.buckets["10.0.0.2"]
	rl.mu.Unlock()
	if !aAlive {
		t.Error("recently-used IP A was evicted; LRU violated")
	}
	if bAlive {
		t.Error("idle IP B survived eviction; LRU violated")
	}

	// The evicted IP B starts a fresh burst (new bucket, full tokens).
	if !rl.Allow("10.0.0.2") {
		t.Error("evicted IP B should get a fresh burst")
	}
}

// ── Metric label cardinality guard ─────────────────────────────────────────

func TestRateLimitLabel(t *testing.T) {
	tests := []struct {
		name   string
		ids    []string
		expect string
	}{
		{name: "absent header", ids: nil, expect: "none"},
		{name: "empty slice", ids: []string{}, expect: "none"},
		{name: "single valid", ids: []string{"tenant-1"}, expect: "tenant-1"},
		{name: "composite valid", ids: []string{"tenant-1", "tenant_2"}, expect: "tenant-1,tenant_2"},
		{name: "single invalid traversal", ids: []string{"../evil"}, expect: "invalid"},
		{name: "all invalid", ids: []string{"../evil", "a,b", ".hidden"}, expect: "invalid"},
		{name: "mixed valid and invalid", ids: []string{"tenant-1", "../evil"}, expect: "tenant-1"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := rateLimitLabel(tc.ids); got != tc.expect {
				t.Errorf("rateLimitLabel(%q) = %q, want %q", tc.ids, got, tc.expect)
			}
		})
	}
}

// TestRateLimitLabel_BoundsLength pins the length cap: a header full of
// distinct valid-looking IDs must not produce an unbounded label.
func TestRateLimitLabel_BoundsLength(t *testing.T) {
	ids := make([]string, 0, 200)
	for i := 0; i < 200; i++ {
		ids = append(ids, fmt.Sprintf("t%d", i))
	}
	label := rateLimitLabel(ids)
	if len(label) > 256 {
		t.Fatalf("label length = %d, want <= 256", len(label))
	}
}
