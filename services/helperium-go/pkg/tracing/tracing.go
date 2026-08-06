// Package tracing provides shared OpenTelemetry setup for all Helperium Go services.
//
// Usage:
//
//	import "github.com/trash2bin/helperium/helperium-go/pkg/tracing"
//
//	func main() {
//	    tracing.Setup("data-service")
//	    defer tracing.Shutdown()
//
//	    r := chi.NewRouter()
//	    r.Use(tracing.Middleware)
//	    // ... routes ...
//	}
//
// Environment variables:
//   OTEL_ENABLED              — set to "false" to disable tracing (default: "true")
//   OTEL_EXPORTER_OTLP_ENDPOINT — OTLP HTTP endpoint (default: http://localhost:4318)
//   OTEL_SERVICE_NAME          — override service name
package tracing

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

var (
	tracerProvider *sdktrace.TracerProvider
	tracer         trace.Tracer
)

// Setup initializes OpenTelemetry tracing with OTLP HTTP export.
// Returns false if tracing is disabled via OTEL_ENABLED=false.
func Setup(serviceName string) bool {
	if strings.ToLower(os.Getenv("OTEL_ENABLED")) == "false" {
		slog.Info("OpenTelemetry tracing disabled via OTEL_ENABLED=false")
		return false
	}

	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "http://localhost:4318"
	}

	overrideName := os.Getenv("OTEL_SERVICE_NAME")
	if overrideName != "" {
		serviceName = overrideName
	}
	fullName := fmt.Sprintf("helperium-%s", serviceName)

	res, err := resource.New(context.Background(),
		resource.WithAttributes(
			attribute.String("service.name", fullName),
		),
	)
	if err != nil {
		slog.Warn("failed to create OTel resource", "error", err)
		return false
	}

	// Strip http:// prefix for OTLP HTTP endpoint
	otelEndpoint := strings.TrimPrefix(endpoint, "http://")

	exporter, err := otlptracehttp.New(context.Background(),
		otlptracehttp.WithEndpoint(otelEndpoint),
		otlptracehttp.WithInsecure(),
	)
	if err != nil {
		slog.Warn("failed to create OTLP exporter", "error", err)
		return false
	}

	processor := sdktrace.NewBatchSpanProcessor(exporter)

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(res),
		sdktrace.WithSpanProcessor(processor),
	)
	tracerProvider = provider
	tracer = provider.Tracer(serviceName)

	// Set global propagator for traceparent header propagation
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	slog.Info("OpenTelemetry initialized",
		"service", serviceName,
		"endpoint", endpoint,
	)
	return true
}

// Shutdown flushes pending spans and shuts down the tracer provider.
func Shutdown() {
	if tracerProvider != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := tracerProvider.Shutdown(ctx); err != nil {
			slog.Warn("OTel shutdown error", "error", err)
		}
	}
}

// Middleware is an HTTP middleware that creates a span for each request
// and propagates trace context. Compatible with chi, net/http, etc.
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if tracerProvider == nil || tracer == nil {
			next.ServeHTTP(w, r)
			return
		}

		spanName := r.Method + " " + r.URL.Path
		ctx := otel.GetTextMapPropagator().Extract(r.Context(), propagation.HeaderCarrier(r.Header))

		ctx, span := tracer.Start(ctx, spanName,
			trace.WithSpanKind(trace.SpanKindServer),
		)
		defer span.End()

		// Set standard attributes
		span.SetAttributes(
			attribute.String("http.method", r.Method),
			attribute.String("http.target", r.URL.Path),
			attribute.String("http.host", r.Host),
		)

		// Enrich with X-Tenant-ID
		if tenantID := r.Header.Get("X-Tenant-ID"); tenantID != "" {
			span.SetAttributes(attribute.String("tenant.id", tenantID))
		}

		// Enrich with X-Correlation-ID
		if corrID := r.Header.Get("X-Correlation-ID"); corrID != "" {
			span.SetAttributes(attribute.String("correlation_id", corrID))
		}

		// Enrich request context with trace ID for structured logging
		sc := span.SpanContext()
		if sc.HasTraceID() {
			traceID := sc.TraceID().String()
			r = r.WithContext(context.WithValue(r.Context(), traceIDKey, traceID))
		}

		// Wrap response writer to capture status code
		wrapped := &statusRecorder{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(wrapped, r.WithContext(ctx))

		span.SetAttributes(attribute.Int("http.status_code", wrapped.statusCode))
	})
}

// statusRecorder wraps http.ResponseWriter to capture the status code.
type statusRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (sr *statusRecorder) WriteHeader(code int) {
	sr.statusCode = code
	sr.ResponseWriter.WriteHeader(code)
}

// Flush implements http.Flusher for SSE streaming support.
// Without this, middleware-wrapped SSE handlers fail with
// "Streaming unsupported" because the type assertion w.(http.Flusher)
// does not match statusRecorder even when the underlying writer flushes.
func (sr *statusRecorder) Flush() {
	if f, ok := sr.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

type contextKey string

const traceIDKey contextKey = "trace_id"

// TraceIDFromContext returns the current trace ID from context, or empty string.
// Checks context value first (set by Middleware), then falls back to active span.
func TraceIDFromContext(ctx context.Context) string {
	if ctx == nil {
		return ""
	}
	if tid, ok := ctx.Value(traceIDKey).(string); ok && tid != "" {
		return tid
	}
	span := trace.SpanFromContext(ctx)
	if span != nil && span.IsRecording() {
		return span.SpanContext().TraceID().String()
	}
	return ""
}
