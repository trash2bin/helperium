package main

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// ── MCP-Gateway Metrics ─────────────────────────────────────────────────────

// mcpToolCallsTotal counts every MCP tool call
var mcpToolCallsTotal = promauto.NewCounterVec(
	prometheus.CounterOpts{
		Name: "mcp_tool_calls_total",
		Help: "Total MCP tool calls by tool, tenant, and status.",
	},
	[]string{"tool", "tenant", "status"},
)

// mcpSessionsActive tracks the current number of active SSE sessions per tenant.
var mcpSessionsActive = promauto.NewGaugeVec(
	prometheus.GaugeOpts{
		Name: "mcp_sessions_active",
		Help: "Currently active SSE sessions per tenant.",
	},
	[]string{"tenant"},
)

// mcpRateLimitHits counts how many requests were rate-limited per tenant.
var mcpRateLimitHits = promauto.NewCounterVec(
	prometheus.CounterOpts{
		Name: "mcp_rate_limit_hits_total",
		Help: "Rate-limited requests by tenant.",
	},
	[]string{"tenant"},
)
