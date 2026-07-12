// Package metrics provides shared Prometheus metric definitions for all Go services.
//
// Each service should register the counters/gauge/histograms it needs via its own
// init() or a setup function and expose /metrics via promhttp.Handler.
//
// Usage:
//
//	import "github.com/trash2bin/helperium/helperium-go/pkg/metrics"
//	import "github.com/prometheus/client_golang/prometheus/promhttp"
//
//	r.Handle("/metrics", promhttp.Handler())
//	metrics.DataRequestsTotal.WithLabelValues("students", "GET", "200").Inc()
package metrics

import "github.com/prometheus/client_golang/prometheus"

// ── Data-Service Metrics ────────────────────────────────────────────────────

// DataRequestsTotal counts every request handled by data-service, labelled by entity, operation, and status.
var DataRequestsTotal = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "data_requests_total",
		Help: "Total requests by entity, operation, and HTTP status.",
	},
	[]string{"entity", "operation", "status"},
)

// DataRequestDuration tracks request latency in milliseconds.
var DataRequestDuration = prometheus.NewHistogramVec(
	prometheus.HistogramOpts{
		Name:    "data_request_duration_ms",
		Help:    "Request latency in milliseconds.",
		Buckets: []float64{1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000},
	},
	[]string{"entity", "operation"},
)

// DBQueryDuration tracks database query latency by tenant.
var DBQueryDuration = prometheus.NewHistogramVec(
	prometheus.HistogramOpts{
		Name:    "data_db_query_duration_ms",
		Help:    "Database query latency in milliseconds.",
		Buckets: []float64{1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000},
	},
	[]string{"tenant"},
)

// ── MCP-Gateway Metrics ─────────────────────────────────────────────────────

// MCPToolCallsTotal counts every MCP tool call, labelled by tool, tenant, and status.
var MCPToolCallsTotal = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "mcp_tool_calls_total",
		Help: "Total MCP tool calls by tool, tenant, and status.",
	},
	[]string{"tool", "tenant", "status"},
)

// MCPSessionsActive tracks the current number of active SSE sessions per tenant.
var MCPSessionsActive = prometheus.NewGaugeVec(
	prometheus.GaugeOpts{
		Name: "mcp_sessions_active",
		Help: "Currently active SSE sessions per tenant.",
	},
	[]string{"tenant"},
)

// MCPRateLimitHits counts how many requests were rate-limited per tenant.
var MCPRateLimitHits = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "mcp_rate_limit_hits_total",
		Help: "Rate-limited requests by tenant.",
	},
	[]string{"tenant"},
)

// ── Admin-Dashboard Metrics ─────────────────────────────────────────────────

// AdminRequestsTotal counts admin dashboard requests.
var AdminRequestsTotal = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "admin_requests_total",
		Help: "Total admin dashboard requests by path and status.",
	},
	[]string{"path", "status"},
)

// AdminAbuseConfigChanges counts abuse config change operations.
var AdminAbuseConfigChanges = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "admin_abuse_config_changes_total",
		Help: "Abuse configuration changes by scope (global/agent).",
	},
	[]string{"scope"},
)

// ── Registration ────────────────────────────────────────────────────────────

// RegisterMetrics explicitly registers all metrics with the default Prometheus registry.
// Call from main() to ensure registration happens regardless of init() ordering.
func RegisterMetrics() {
	prometheus.MustRegister(DataRequestsTotal)
	prometheus.MustRegister(DataRequestDuration)
	prometheus.MustRegister(DBQueryDuration)
	prometheus.MustRegister(MCPToolCallsTotal)
	prometheus.MustRegister(MCPSessionsActive)
	prometheus.MustRegister(MCPRateLimitHits)
	prometheus.MustRegister(AdminRequestsTotal)
	prometheus.MustRegister(AdminAbuseConfigChanges)
}
