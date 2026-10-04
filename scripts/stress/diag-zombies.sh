#!/usr/bin/env bash
# Диагностика зомби-ходов (Трек 1 плана, до фикса).
#
# Гипотеза из плана п.0.2: клиент отвалился (sse_abort), а серверный agent
# loop продолжает крутиться — дожигает вызовы тулов и следующий раунд модели.
# Прежде чем чинить, это надо подтвердить измерением, а не принять на веру.
#
# Что делает скрипт:
#   1. Базовая линия счётчиков.
#   2. Стадия 40 rps (та самая, где sse_abort), 60s + warmup 10s.
#   3. Сразу после остановки нагрузки 60s сэмплируем каждые 2s:
#      - llm_calls_total / llm_completion_attempts_total (api) — растут ли
#        вызовы модели без входящего трафика (сигнатура зомби);
#      - mcp_tool_calls_total / mcp_sessions_active (gateway) — долбятся ли
#        тулы без входящего трафика;
#      - CPU api-service (% от одного ядра).
#   4. Проба восстановления: короткая стадия 20 rps — p95 ~150ms значит
#      восстановился, ~1300ms значит hysteresis.
#
# Требует поднятого стенда (start-stand.sh) и нано-L2 стаба (run-l2.sh env).
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/stress/stand.env
export ADMIN_TOKEN MCP_API_KEY API_BEARER_TOKEN
export BACKLOG_MODE GUARDRAIL_ENABLED LOG_LEVEL AGENT_MAX_TURN_TOKENS
export AGENT_MAX_ITERATIONS=5

API_METRICS="http://127.0.0.1:8081/metrics"
GW_METRICS="http://127.0.0.1:8083/metrics"
API_PID=$(pgrep -f "[p]ython -m api_service.server" | head -1 || true)

sample() {
  # Один сэмпл: delta-счётчики и CPU. Позиционные аргументы — предыдущие
  # значения, чтобы delta считалась здесь же.
  local prev_llm="$1" prev_attempts="$2" prev_tools="$3"
  local llm attempts tools sessions cpu
  llm=$(curl -s -H "Authorization: Bearer $API_BEARER_TOKEN" "$API_METRICS" \
    | awk '/^llm_calls_total\{model="stub"/ {s+=$NF} END {printf "%.0f", s}')
  attempts=$(curl -s -H "Authorization: Bearer $API_BEARER_TOKEN" "$API_METRICS" \
    | awk '/^llm_completion_attempts_total\{model="stub"/ {s+=$NF} END {printf "%.0f", s}')
  tools=$(curl -s -H "Authorization: Bearer $MCP_API_KEY" "$GW_METRICS" \
    | awk '/^mcp_tool_calls_total\{/ {s+=$NF} END {printf "%.0f", s}')
  sessions=$(curl -s -H "Authorization: Bearer $MCP_API_KEY" "$GW_METRICS" \
    | awk '/^mcp_sessions_active\{/ {s+=$NF} END {printf "%.0f", s}')
  cpu=""
  if [[ -n "$API_PID" ]]; then
    cpu=$(ps -o %cpu= -p "$API_PID" | tr -d ' ')
  fi
  printf 't=%s llm=%s (d=%s) attempts=%s (d=%s) tools=%s (d=%s) sessions=%s cpu=%s%%\n' \
    "$(date +%H:%M:%S)" "$llm" "$((llm - prev_llm))" "$attempts" \
    "$((attempts - prev_attempts))" "$tools" "$((tools - prev_tools))" \
    "$sessions" "$cpu"
}

echo "=== 1. baseline (idle) ==="
prev_llm=$(curl -s -H "Authorization: Bearer $API_BEARER_TOKEN" "$API_METRICS" \
  | awk '/^llm_calls_total\{model="stub"/ {s+=$NF} END {printf "%.0f", s}')
prev_attempts=$(curl -s -H "Authorization: Bearer $API_BEARER_TOKEN" "$API_METRICS" \
  | awk '/^llm_completion_attempts_total\{model="stub"/ {s+=$NF} END {printf "%.0f", s}')
prev_tools=$(curl -s -H "Authorization: Bearer $MCP_API_KEY" "$GW_METRICS" \
  | awk '/^mcp_tool_calls_total\{/ {s+=$NF} END {printf "%.0f", s}')
echo "baseline llm=$prev_llm attempts=$prev_attempts tools=$prev_tools api_pid=$API_PID"

echo ""
echo "=== 2. overload: 40 rps x 60s (warmup 10s) ==="
uv run --package agent-db agent-db-stress run \
  services/agent-db/agent_db/stress/profiles/chat-zero-latency-l2.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 40 --duration-s 60 --warmup-s 10 \
  --t-budget-ms 500 \
  --admin-token "$ADMIN_TOKEN" --api-bearer-token "$API_BEARER_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  | grep -E "rps |sse|report " || true

echo ""
echo "=== 3. tail: 60s после остановки нагрузки (шаг 2s) ==="
for _ in $(seq 1 30); do
  out=$(sample "$prev_llm" "$prev_attempts" "$prev_tools")
  echo "$out"
  prev_llm=$(echo "$out" | sed -E 's/.*llm=([0-9]+).*/\1/')
  prev_attempts=$(echo "$out" | sed -E 's/.*attempts=([0-9]+).*/\1/')
  prev_tools=$(echo "$out" | sed -E 's/.*tools=([0-9]+).*/\1/')
  sleep 2
done

echo ""
echo "=== 4. recovery probe: 20 rps x 30s (warmup 5s) ==="
uv run --package agent-db agent-db-stress run \
  services/agent-db/agent_db/stress/profiles/chat-zero-latency-l2.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 20 --duration-s 30 --warmup-s 5 \
  --t-budget-ms 500 \
  --admin-token "$ADMIN_TOKEN" --api-bearer-token "$API_BEARER_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics \
  | grep -E "rps |report " || true

echo ""
echo "=== DONE ==="
