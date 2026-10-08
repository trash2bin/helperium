#!/usr/bin/env bash
# L3 (product-shape): чат через api-service, стаб с продуктовой латентностью
# (p50 800 мс, p95 2.5 с). Вопрос §3: сколько одновременных чатов держит
# платформа. Критерий — platform_overhead p95 (ход минус доля модели, §7),
# join по маркеру из лога стаба (§4): --stub-timings обязателен.
# rates 5..40, бюджет 500 мс (L3), repeats=1 — ограниченный прогон.
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

# The instrument must match the profile: the stub runs at the product latency,
# not the stand's ~0 ms (§3: L3 is measured with a controlled latency). This
# also rotates nothing: the log grows by append, the join is marker-keyed, so
# stale rows never collide with a fresh run's markers.
STUB_P50_MS=800 STUB_P95_MS=2500 STUB_RESPONSE_CHUNKS=100 \
  ./scripts/stress/start-stub.sh restart

exec uv run --package agent-db agent-db-stress run services/agent-db/agent_db/stress/profiles/chat-product-l3.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 5,10,20,40 \
  --duration-s 90 --warmup-s 20 \
  --t-budget-ms 500 \
  --repeats 1 \
  --stub-timings stub-timings.jsonl \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  "${BUDGET_FLAGS[@]}" \
  --api-workers "${API_WORKERS:-1}" \
  "$@"
