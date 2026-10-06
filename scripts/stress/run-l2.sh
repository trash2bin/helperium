#!/usr/bin/env bash
# L2: чат через api-service, стаб ~0 мс. Главный метрик плана (§2.1).
# rates 5..80, бюджет 500 мс, repeats=2 — headline по медиане.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/stress/stand.env
export ADMIN_TOKEN MCP_API_KEY API_BEARER_TOKEN
export BACKLOG_MODE GUARDRAIL_ENABLED LOG_LEVEL AGENT_MAX_TURN_TOKENS
export AGENT_MAX_ITERATIONS=5
exec uv run --package agent-db agent-db-stress run services/agent-db/agent_db/stress/profiles/chat-zero-latency-l2.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 5,10,20,40,80 \
  --duration-s 90 --warmup-s 20 \
  --t-budget-ms 500 \
  --repeats 2 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  "$@"
