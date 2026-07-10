// Package server настраивает HTTP-сервер с middleware и config-driven роутами.
//
// Новая архитектура (фаза 3.3+):
//   - Нет захардкоженных репозиториев, хендлеров или моделей.
//   - Все маршруты строятся из конфига через NewRouterFromConfig.
//   - Middleware (Recovery, RequestID, StructuredLogging) остаются.
package server

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/agent-tutor-go/pkg/metrics"
)

// ── Middleware ──

// RequestIDMiddleware извлекает или генерирует X-Correlation-ID,
// добавляет его в контекст и в заголовок ответа.
func RequestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		correlationID := r.Header.Get("X-Correlation-ID")
		if correlationID == "" {
			correlationID = r.Header.Get("x-correlation-id")
		}

		w.Header().Set("X-Correlation-ID", correlationID)
		ctx := context.WithValue(r.Context(), correlationIDKey, correlationID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// StructuredLoggingMiddleware логирует каждый запрос в JSON-формате через slog.
func StructuredLoggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		correlationID, _ := r.Context().Value(correlationIDKey).(string)

		// Оборачиваем ResponseWriter для захвата статус-кода
		wrapped := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(wrapped, r)

		// Prometheus metrics — entity="all" для корневого уровня
		metrics.DataRequestsTotal.WithLabelValues("all", r.Method, strconv.Itoa(wrapped.statusCode)).Inc()
		metrics.DataRequestDuration.WithLabelValues("all", r.Method).Observe(float64(time.Since(start).Milliseconds()))

		slog.Info("request",
			"method", r.Method,
			"path", r.URL.Path,
			"query", r.URL.RawQuery,
			"status", wrapped.statusCode,
			"duration_ms", time.Since(start).Milliseconds(),
			"correlation_id", correlationID,
		)
	})
}

// RecoveryMiddleware перехватывает паники и возвращает 500.
func RecoveryMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic recovered",
					"panic", rec,
					"path", r.URL.Path,
					"method", r.Method,
				)
				http.Error(w, `{"error":"internal server error"}`, http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// BodyLimitMiddleware ограничивает размер тела запроса для POST/PUT/PATCH.
// Если Content-Length превышает limit — возвращает 413 Request Entity Too Large.
func BodyLimitMiddleware(limit int64) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.ContentLength > limit {
				slog.Warn("request body too large",
					"method", r.Method,
					"path", r.URL.Path,
					"content_length", r.ContentLength,
					"limit", limit,
				)
				http.Error(w, `{"error":"body_too_large","message":"Request body exceeds maximum size"}`, http.StatusRequestEntityTooLarge)
				return
			}
			// Wrap body with LimitReader для потоковой защиты
			r.Body = http.MaxBytesReader(w, r.Body, limit)
			next.ServeHTTP(w, r)
		})
	}
}

// ── Admin Rate Limit ──

// adminRateLimiter — token bucket rate limiter для /admin/* endpoint'ов.
// Использует time-based расчёт без фоновой горутины.
// Параметры: ADMIN_RATE_LIMIT_RPS (default 5), ADMIN_RATE_LIMIT_BURST (default 10).
type adminRateLimiter struct {
	mu       sync.Mutex
	rps      int
	burst    int
	tokens   float64
	lastTime time.Time
}

func newAdminRateLimiter(rps, burst int) *adminRateLimiter {
	return &adminRateLimiter{
		rps:      rps,
		burst:    burst,
		tokens:   float64(burst),
		lastTime: time.Now(),
	}
}

func (rl *adminRateLimiter) Allow() bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(rl.lastTime).Seconds()
	rl.lastTime = now

	// Добавляем токены пропорционально прошедшему времени
	rl.tokens += elapsed * float64(rl.rps)
	if rl.tokens > float64(rl.burst) {
		rl.tokens = float64(rl.burst)
	}

	if rl.tokens >= 1.0 {
		rl.tokens--
		return true
	}
	return false
}

// AdminRateLimitMiddleware возвращает middleware, ограничивающий частоту запросов
// token bucket алгоритмом. Параметры читаются из env:
//   ADMIN_RATE_LIMIT_RPS   — запросов в секунду (default 20)
//   ADMIN_RATE_LIMIT_BURST — burst размер (default 50)
// При превышении лимита возвращает 429 Too Many Requests с Retry-After.
func AdminRateLimitMiddleware() func(http.Handler) http.Handler {
	rps := resolveIntEnv("ADMIN_RATE_LIMIT_RPS", 0, 20)
	burst := resolveIntEnv("ADMIN_RATE_LIMIT_BURST", 0, 50)
	rl := newAdminRateLimiter(rps, burst)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !rl.Allow() {
				w.Header().Set("Retry-After", "1")
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusTooManyRequests)
				w.Write([]byte(`{"error":"rate_limit_exceeded","message":"Too many admin requests, try again later"}`))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// ThrottleMiddleware ограничивает количество одновременных запросов.
// Если превышен лимит — возвращает 503 Service Unavailable.
func ThrottleMiddleware(maxConcurrent int) func(http.Handler) http.Handler {
	var mu sync.Mutex
	active := 0

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			if active >= maxConcurrent {
				mu.Unlock()
				w.Header().Set("Retry-After", "1")
				http.Error(w, `{"error":"too_many_requests","message":"Server at capacity, try again later"}`, http.StatusServiceUnavailable)
				return
			}
			active++
			mu.Unlock()

			defer func() {
				mu.Lock()
				active--
				mu.Unlock()
			}()

			next.ServeHTTP(w, r)
		})
	}
}

// ── Resolver functions: env → config → default ──

// resolveServerTimeout возвращает таймаут запроса в секундах.
// Приоритет: DS_REQUEST_TIMEOUT env → cfg.Server.RequestTimeoutSeconds → 30.
func ResolveRequestTimeout(cfg *config.Config) int {
	return resolveIntEnv("DS_REQUEST_TIMEOUT",
		configValue(cfg, func(c *config.Config) *int {
			if c.Server != nil {
				return c.Server.RequestTimeoutSeconds
			}
			return nil
		}),
		30)
}

// resolveBodyLimit возвращает лимит тела запроса в байтах.
// Приоритет: DS_BODY_LIMIT_MB env → cfg.Server.BodyLimitMB → 10 MB.
func ResolveBodyLimit(cfg *config.Config) int64 {
	mb := resolveIntEnv("DS_BODY_LIMIT_MB",
		configValue(cfg, func(c *config.Config) *int {
			if c.Server != nil {
				return c.Server.BodyLimitMB
			}
			return nil
		}),
		10)
	return int64(mb) << 20
}

// resolveMaxConcurrent возвращает максимум одновременных запросов.
// Приоритет: DS_MAX_CONCURRENT env → cfg.Server.MaxConcurrent → 100.
func ResolveMaxConcurrent(cfg *config.Config) int {
	return resolveIntEnv("DS_MAX_CONCURRENT",
		configValue(cfg, func(c *config.Config) *int {
			if c.Server != nil {
				return c.Server.MaxConcurrent
			}
			return nil
		}),
		100)
}

// resolveIntEnv читает env-переменную, парсит как int.
// Если не задана или не парсится — возвращает fallback.
func resolveIntEnv(key string, fallback int, defaultVal int) int {
	raw := os.Getenv(key)
	if raw != "" {
		if val, err := strconv.Atoi(raw); err == nil && val > 0 {
			return val
		}
	}
	if fallback > 0 {
		return fallback
	}
	return defaultVal
}

// configValue извлекает опциональное *int из Config через getter.
func configValue(cfg *config.Config, getter func(*config.Config) *int) int {
	if v := getter(cfg); v != nil {
		return *v
	}
	return 0
}

// ── Вспомогательные типы ──

type contextKey string

const (
	correlationIDKey contextKey = "correlation_id"
	tenantIDKey      contextKey = "tenant_id"
)

// TenantIDMiddleware извлекает tenant_id из заголовка (по конфигу auth.tenant_header).
// Если auth не настроен — пропускает (tenantIDKey в контексте = "").
func TenantIDMiddleware(tenantHeader string) func(http.Handler) http.Handler {
	if tenantHeader == "" {
		tenantHeader = "X-Tenant-ID"
	}
	systemPaths := map[string]struct{}{
		"/docs":         {},
		"/openapi.json": {},
		"/metrics":      {},
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			path := r.URL.Path
			if _, ok := systemPaths[path]; ok {
				next.ServeHTTP(w, r)
				return
			}
			tenantID := r.Header.Get(tenantHeader)
			if tenantID == "" {
				// Case-insensitive fallback
				for k, v := range r.Header {
					if len(v) > 0 && strings.EqualFold(k, tenantHeader) {
						tenantID = v[0]
						break
					}
				}
			}
			ctx := context.WithValue(r.Context(), tenantIDKey, tenantID)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

type responseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.statusCode = code
	rw.ResponseWriter.WriteHeader(code)
}

// ── Инициализация логгера ──

// InitLogger настраивает structured JSON-логирование.
func InitLogger() {
	level := slog.LevelInfo
	if os.Getenv("LOG_LEVEL") == "debug" {
		level = slog.LevelDebug
	}

	handler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{
		Level: level,
	})
	slog.SetDefault(slog.New(handler))

	slog.Info("logger initialized", "level", level.String())
}
