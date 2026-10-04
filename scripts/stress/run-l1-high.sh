#!/usr/bin/env bash
# L1-high: ищем настоящий потолок платформы (rates 80..1280, бюджет 50 мс).
#
# Важно про gen CPU: стадия с gen CPU >= 80% — это потолок прибора
# (генератора), а не сервиса (§0.4 плана). Если runl'ы выше 80 rps уходят в
# invalid по генератору — это сигнал разгрузить генератор (см.
# run-l1-relieved.sh), а не утверждать, что платформа не тянет.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/stress/stand.env
export ADMIN_TOKEN MCP_API_KEY API_BEARER_TOKEN
export BACKLOG_MODE GUARDRAIL_ENABLED LOG_LEVEL AGENT_MAX_TURN_TOKENS
export AGENT_MAX_ITERATIONS=5
exec uv run --package agent-db agent-db-stress run services/agent-db/agent_db/stress/profiles/mcp-tool-call-l1.json \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2,stress-3,stress-4 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 80,160,320,640,1280 \
  --duration-s 120 --warmup-s 30 \
  --t-budget-ms 50 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics
