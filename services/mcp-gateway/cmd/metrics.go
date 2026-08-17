package main

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// ── MCP-Gateway Metrics ─────────────────────────────────────────────────────

// mcpSessionsActive tracks the current number of active Streamable HTTP
// sessions per resolved tenant scope. Its value is maintained by MCP SDK
// register/unregister session hooks in main.go.
var mcpSessionsActive = promauto.NewGaugeVec(
	prometheus.GaugeOpts{
		Name: "mcp_sessions_active",
		Help: "Currently active Streamable HTTP sessions per resolved tenant scope.",
	},
	[]string{"tenant_scope"},
)

// mcpRateLimitHits counts how many requests were rate-limited per tenant.
var mcpRateLimitHits = promauto.NewCounterVec(
	prometheus.CounterOpts{
		Name: "mcp_rate_limit_hits_total",
		Help: "Rate-limited requests by tenant.",
	},
	[]string{"tenant"},
)
