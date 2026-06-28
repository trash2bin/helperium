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
	"time"
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

// ── Вспомогательные типы ──

type contextKey string

const correlationIDKey contextKey = "correlation_id"

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
