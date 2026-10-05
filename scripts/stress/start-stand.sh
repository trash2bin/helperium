#!/usr/bin/env bash
# Нативный стенд для doc/agents/api-service-decomposition-plan.md (§2.1).
#
# Поднимает четыре процесса на loopback: data-service, mcp-gateway,
# api-service и стаб LLM — тот же состав, что в замерах 2026-10-03.
# Останавливается ./scripts/stress/stop-stand.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=stand.env
source "$SCRIPT_DIR/stand.env"

# ---- shared env (self-contained, no .env) ----
export ADMIN_TOKEN API_BEARER_TOKEN MCP_API_KEY MCP_CLIENT_API_KEY
export MCP_ALLOWED_ORIGINS MCP_RATE_LIMIT_RPS MCP_RATE_LIMIT_BURST
export ABUSE_IP_RPS ABUSE_IP_BURST CHAT_RATE_LIMIT BACKLOG_MODE
export GUARDRAIL_ENABLED HEALTH_CHECK_SKIP_LLM MCP_DEV MCP_REQUIRE_AUTH
export AGENT_MAX_TURN_TOKENS AGENT_MAX_ITERATIONS API_WORKERS
export SESSION_STORAGE_URI LLM_PROVIDER_TRANSPORT
ENCRYPTION_KEY=$(cat .data/dev_encryption_key)
export ENCRYPTION_KEY
LOG_LEVEL=info
export LOG_LEVEL
mkdir -p .data/sessions .data/tenants .data/logs .data/pids

echo "=== 1. data-service ==="
TENANTS_DIR=$(pwd)/.data/tenants DATA_PORT=8084 LOG_LEVEL=info \
  nohup services/data-service/bin/data-service > .data/logs/data.log 2>&1 &
echo $! > .data/pids/data.pid
for i in $(seq 1 10); do
  curl -sf http://127.0.0.1:8084/health >/dev/null 2>&1 && break
  sleep 0.5
done
echo "  data-service: $(curl -s http://127.0.0.1:8084/health)"

echo "=== 2. mcp-gateway ==="
MCP_PORT=8083 MCP_HOST=127.0.0.1 DATA_SERVICE_URL=http://127.0.0.1:8084 LOG_LEVEL=info \
  nohup services/mcp-gateway/mcp-gateway > .data/logs/mcp.log 2>&1 &
echo $! > .data/pids/mcp.pid
for i in $(seq 1 20); do
  curl -sf http://127.0.0.1:8083/health >/dev/null 2>&1 && break
  sleep 0.5
done
echo "  mcp-gateway: $(curl -s http://127.0.0.1:8083/health)"

echo "=== 3. api-service ==="
DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=8081 \
  MCP_GATEWAY_URL=http://127.0.0.1:8083 \
  MCP_STREAMABLE_HTTP_URL=http://127.0.0.1:8083/mcp \
  nohup uv run --package api-service python -m api_service.server > .data/logs/api.log 2>&1 &
echo $! > .data/pids/api.pid
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8081/health >/dev/null 2>&1 && break
  sleep 1
done
echo "  api-service: $(curl -s http://127.0.0.1:8081/health | head -c 200)"

echo "=== 4. stub LLM (p50=${STUB_P50_MS}ms p95=${STUB_P95_MS}ms) ==="
"$SCRIPT_DIR/start-stub.sh" restart
sleep 2
echo "  stub: $(curl -sf http://127.0.0.1:9099/v1/models | head -c 100 || echo 'not ready yet')"

echo "=== 5. seed tenants ==="
# L1-профиль рассчитан на 4 tenant'а (tenants.count=4), L2 — на 2; сидим все
# четыре, чтобы стенд не зависел от tenants, оставшихся от прошлой сессии.
for i in 1 2 3 4; do
  uv run --package agent-db agent-db register "stress-$i" sqlite-testseed
done

echo "=== 6. create agent stress-agent ==="
curl -s -X POST http://127.0.0.1:8081/api/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_BEARER_TOKEN" \
  -d '{
    "name": "stress-agent",
    "description": "stress test agent",
    "tenant_ids": ["stress-1", "stress-2"],
    "llm_config": {
      "provider": "openai",
      "model": "stub",
      "api_base": "http://127.0.0.1:9099/v1",
      "api_key": "sk-stub"
    }
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('name','NO_NAME'), d.get('tenant_ids','NO_TENANTS'))"

echo ""
echo "=== 7. preflight ==="
uv run --package agent-db agent-db-stress check \
  services/agent-db/agent_db/stress/profiles/chat-zero-latency-l2.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --tenants stress-1,stress-2 \
  --budget chat_rate_limit="$CHAT_RATE_LIMIT" \
  --budget mcp_rate_limit_rps="$MCP_RATE_LIMIT_RPS"

echo ""
echo "=== DONE: stand is up ==="
