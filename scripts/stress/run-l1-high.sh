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

# Эффективные бюджеты стенда для харнесса (§5, контракт stand.env): без них
# харнесс записывает документированные дефолты рядом со стендом, чьи лимиты
# сняты, а preflight объявляет ложный потолок (прогон da85632e записал
# 30/minute при стенде на 60000/minute).
BUDGET_FLAGS=(
  --budget "MCP_RATE_LIMIT_RPS=$MCP_RATE_LIMIT_RPS"
  --budget "MCP_RATE_LIMIT_BURST=$MCP_RATE_LIMIT_BURST"
  --budget "ABUSE_IP_RPS=$ABUSE_IP_RPS"
  --budget "ABUSE_IP_BURST=$ABUSE_IP_BURST"
  --budget "ABUSE_MAX_USER_TURNS=$ABUSE_MAX_USER_TURNS"
  --budget "CHAT_RATE_LIMIT=$CHAT_RATE_LIMIT"
)
exec uv run --package agent-db agent-db-stress run services/agent-db/agent_db/stress/profiles/mcp-tool-call-l1.json \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2,stress-3,stress-4 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 80,160,320,640,1280 \
  --duration-s 120 --warmup-s 30 \
  --t-budget-ms 50 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  "${BUDGET_FLAGS[@]}" \
  --api-workers "${API_WORKERS:-1}" \
  "$@"
