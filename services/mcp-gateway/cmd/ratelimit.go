package main

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Per-IP Token Bucket Rate Limiter ──

// ipBucket holds token bucket state for a single IP.
type ipBucket struct {
	tokens   float64
	lastTime time.Time
	mu       sync.Mutex
}

// rateLimiter manages per-IP token buckets with configurable RPS and burst.
// maxBuckets bounds the bucket map: without it, one bucket per scanner IP
// grows memory without limit. When the cap is reached, the least recently
// used buckets are evicted (evicted IPs simply start with a fresh burst).
type rateLimiter struct {
	mu         sync.Mutex
	rps        int
	burst      int
	maxBuckets int
	buckets    map[string]*ipBucket
	lastSeen   map[string]time.Time
}

// defaultMaxRateLimitBuckets bounds memory under scanner traffic when
// MCP_RATE_LIMIT_MAX_IPS is not set.
const defaultMaxRateLimitBuckets = 10_000

// maxRateLimitBuckets reads MCP_RATE_LIMIT_MAX_IPS (0/invalid → default).
// Can be overridden in tests via t.Setenv before middleware construction.
func maxRateLimitBuckets() int {
	if v := os.Getenv("MCP_RATE_LIMIT_MAX_IPS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return defaultMaxRateLimitBuckets
}

// newRateLimiter creates a rate limiter with the given RPS and burst.
// If rps <= 0, defaults to 10. If burst <= 0, defaults to 20.
// If maxBuckets <= 0, defaults to defaultMaxRateLimitBuckets.
func newRateLimiter(rps, burst, maxBuckets int) *rateLimiter {
	if rps <= 0 {
		rps = 10
	}
	if burst <= 0 {
		burst = 20
	}
	if maxBuckets <= 0 {
		maxBuckets = defaultMaxRateLimitBuckets
	}
	return &rateLimiter{
		rps:        rps,
		burst:      burst,
		maxBuckets: maxBuckets,
		buckets:    make(map[string]*ipBucket),
		lastSeen:   make(map[string]time.Time),
	}
}

// evictOldest removes the least recently used bucket and reports whether
// at least one slot was freed. Callers must hold rl.mu.
func (rl *rateLimiter) evictOldest() bool {
	if len(rl.buckets) == 0 {
		return false
	}
	var oldestIP string
	var oldest time.Time
	for ip, seen := range rl.lastSeen {
		if oldestIP == "" || seen.Before(oldest) {
			oldestIP = ip
			oldest = seen
		}
	}
	if oldestIP == "" {
		return false
	}
	delete(rl.buckets, oldestIP)
	delete(rl.lastSeen, oldestIP)
	return true
}

// Allow checks if a request from the given IP should be allowed.
// Returns true if within rate limit, false if rate limited.
func (rl *rateLimiter) Allow(ip string) bool {
	rl.mu.Lock()
	b, ok := rl.buckets[ip]
	if !ok {
		if len(rl.buckets) >= rl.maxBuckets {
			rl.evictOldest()
		}
		b = &ipBucket{
			tokens:   float64(rl.burst),
			lastTime: nowFunc(),
		}
		rl.buckets[ip] = b
	}
	rl.lastSeen[ip] = nowFunc()
	rl.mu.Unlock()

	b.mu.Lock()
	defer b.mu.Unlock()

	now := nowFunc()
	elapsed := now.Sub(b.lastTime).Seconds()
	b.lastTime = now

	// Refill tokens proportional to elapsed time
	b.tokens += elapsed * float64(rl.rps)
	if b.tokens > float64(rl.burst) {
		b.tokens = float64(rl.burst)
	}

	if b.tokens >= 1.0 {
		b.tokens--
		return true
	}
	return false
}

// bucketCount returns the current number of tracked IP buckets (tests only).
func (rl *rateLimiter) bucketCount() int {
	rl.mu.Lock()
	defer rl.mu.Unlock()
	return len(rl.buckets)
}

// advanceTime artificially advances the last access time for a given IP.
// Used ONLY in tests to verify token replenishment without calling time.Sleep.
func (rl *rateLimiter) advanceTime(ip string, d time.Duration) {
	rl.mu.Lock()
	rl.lastSeen[ip] = rl.lastSeen[ip].Add(-d)
	b, ok := rl.buckets[ip]
	rl.mu.Unlock()
	if ok {
		b.mu.Lock()
		b.lastTime = b.lastTime.Add(-d)
		b.mu.Unlock()
	}
}

// ── Metric label cardinality guard ──

// rateLimitLabel builds the Prometheus label for mcpRateLimitHits. Tenant IDs
// come from the browser-controlled X-Tenant-ID header, so raw values must
// never become label values: unbounded cardinality is a memory/DoS vector.
// Invalid IDs (repo-wide tenant-ID contract) collapse to "invalid", an absent
// header to "none". The label keeps one entry per distinct *shape* of scope,
// not per distinct attacker-controlled string.
func rateLimitLabel(tenantIDs []string) string {
	if len(tenantIDs) == 0 {
		return "none"
	}
	valid := make([]string, 0, len(tenantIDs))
	for _, id := range tenantIDs {
		if config.ValidTenantID(id) {
			valid = append(valid, id)
		}
	}
	if len(valid) == 0 {
		return "invalid"
	}
	label := strings.Join(valid, ",")
	if len(label) > 256 {
		label = label[:256]
	}
	return label
}

// ── Rate Limit Middleware ──

// mcpRateLimitMiddleware returns an HTTP middleware that rate-limits
// requests per IP address using a token bucket algorithm.
// Parameters are read from env:
//
//	MCP_RATE_LIMIT_RPS     — requests per second (default 10)
//	MCP_RATE_LIMIT_BURST   — burst size (default 20)
//	MCP_RATE_LIMIT_MAX_IPS — max tracked IPs before LRU eviction
//	                         (default 10000)
//
// Returns 429 Too Many Requests when limit is exceeded.
func mcpRateLimitMiddleware() func(http.Handler) http.Handler {
	rps, burst := resolveRateLimitParams()
	maxIPs := maxRateLimitBuckets()
	rl := newRateLimiter(rps, burst, maxIPs)
	slog.Info("Rate limiter initialized",
		"rps", rps, "burst", burst, "max_ips", maxIPs)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := extractIP(r.RemoteAddr)
			if !rl.Allow(ip) {
				mcpRateLimitHits.WithLabelValues(rateLimitLabel(resolveTenantIDs(r))).Inc()
				slog.Warn("rate limit exceeded", "ip", ip, "path", r.URL.Path)
				w.Header().Set("Retry-After", "1")
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusTooManyRequests)
				json.NewEncoder(w).Encode(map[string]string{
					"error":   "rate_limit_exceeded",
					"message": "Too many requests",
				})
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// extractIP strips the port from RemoteAddr (e.g. "10.0.0.1:54321" → "10.0.0.1").
func extractIP(remoteAddr string) string {
	if idx := strings.LastIndex(remoteAddr, ":"); idx != -1 {
		if strings.HasSuffix(remoteAddr[:idx], "]") {
			// IPv6: [::1]:port → strip brackets
			ip := strings.TrimPrefix(remoteAddr[:idx], "[")
			ip = strings.TrimSuffix(ip, "]")
			return ip
		}
		return remoteAddr[:idx]
	}
	return remoteAddr
}

// resolveRateLimitParams reads rate limit configuration from env vars.
func resolveRateLimitParams() (rps, burst int) {
	rps = 10
	burst = 20

	if v := os.Getenv("MCP_RATE_LIMIT_RPS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			rps = n
		}
	}
	if v := os.Getenv("MCP_RATE_LIMIT_BURST"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			burst = n
		}
	}
	return rps, burst
}
