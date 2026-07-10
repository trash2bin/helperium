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
)

// ── Per-IP Token Bucket Rate Limiter ──

// ipBucket holds token bucket state for a single IP.
type ipBucket struct {
	tokens    float64
	lastTime  time.Time
	mu        sync.Mutex
}

// rateLimiter manages per-IP token buckets with configurable RPS and burst.
type rateLimiter struct {
	mu      sync.Mutex
	rps     int
	burst   int
	buckets map[string]*ipBucket
}

// newRateLimiter creates a rate limiter with the given RPS and burst.
// If rps <= 0, defaults to 10. If burst <= 0, defaults to 20.
func newRateLimiter(rps, burst int) *rateLimiter {
	if rps <= 0 {
		rps = 10
	}
	if burst <= 0 {
		burst = 20
	}
	return &rateLimiter{
		rps:     rps,
		burst:   burst,
		buckets: make(map[string]*ipBucket),
	}
}

// Allow checks if a request from the given IP should be allowed.
// Returns true if within rate limit, false if rate limited.
func (rl *rateLimiter) Allow(ip string) bool {
	rl.mu.Lock()
	b, ok := rl.buckets[ip]
	if !ok {
		b = &ipBucket{
			tokens:   float64(rl.burst),
			lastTime: time.Now(),
		}
		rl.buckets[ip] = b
	}
	rl.mu.Unlock()

	b.mu.Lock()
	defer b.mu.Unlock()

	now := time.Now()
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

// advanceTime artificially advances the last access time for a given IP.
// Used ONLY in tests to verify token replenishment without calling time.Sleep.
func (rl *rateLimiter) advanceTime(ip string, d time.Duration) {
	rl.mu.Lock()
	b, ok := rl.buckets[ip]
	rl.mu.Unlock()
	if ok {
		b.mu.Lock()
		b.lastTime = b.lastTime.Add(-d)
		b.mu.Unlock()
	}
}

// ── Rate Limit Middleware ──

// mcpRateLimitMiddleware returns an HTTP middleware that rate-limits
// requests per IP address using a token bucket algorithm.
// Parameters are read from env:
//
//	MCP_RATE_LIMIT_RPS   — requests per second (default 10)
//	MCP_RATE_LIMIT_BURST — burst size (default 20)
//
// Returns 429 Too Many Requests when limit is exceeded.
func mcpRateLimitMiddleware() func(http.Handler) http.Handler {
	rps, burst := resolveRateLimitParams()
	rl := newRateLimiter(rps, burst)
	slog.Info("Rate limiter initialized", "rps", rps, "burst", burst)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := extractIP(r.RemoteAddr)
			if !rl.Allow(ip) {
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
