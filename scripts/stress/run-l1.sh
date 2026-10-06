#!/usr/bin/env bash
# L1: MCP → gateway → data-service, без LLM. rates 5..80, бюджет 50 мс.
# Ожидание: стенд поднят (start-stand.sh). Перед прогоном проверь, что
# стенд жив: stop-stand.sh только гасит, он не health-check'ит.
#
# Лишние аргументы пробрасываются в CLI ("$@"), чтобы здесь же объявлять флаги
# манифеста: --api-workers N, --binary LABEL=PATH[:SOURCE_ROOT], --budget NAME=VALUE.
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
  --rates 5,10,20,40,80 \
  --duration-s 120 --warmup-s 30 \
  --t-budget-ms 50 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  "$@"
