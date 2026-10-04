#!/usr/bin/env bash
# L1-relieved: тот же потолок, что ищет run-l1-high.sh, но с разгруженным
# генератором — вариант, когда run-l1-high упирается в gen CPU >= 80%.
#
# Два отличия от run-l1-high.sh (оба — параметры харнесса, не код стенда):
#   --pool-margin-s 0.05  вместо дефолтного 1.0. Пул воркеров считается как
#                         rps*(T+margin); при 160 rps дефолт даёт 168 потоков,
#                         из которых каждый busy-wait'ит хвост своего тика —
#                         на 672 воркерах хвосты одни съедали 117% ядра
#                         (runner.py, замер 2026-09-27). Пул 0.05s — это
#                         минимум, нужный, чтобы кормить лестницу при
#                         сервис-тайме T: 16 потоков на 160 rps вместо 168.
#   --max-pool-expansions 0  без пул-даблинга: даблинг удваивает число
#                         busy-wait хвостов и убивает генератор раньше платформы
#                         (тот же замер: 160 rps -> даблинг -> 672 воркера).
#
# Риск (честно, до прогона): тугой пул хуже переживает деградацию платформы —
# если сервис-тайм вырастет выше T+margin, генератор сам начнёт ронять тики,
# и стадия станет invalid по лагу. Это осознанный обмен: мы хотим найти knee
# платформы, а не knee пула. Если стадии выше 320 rps из-за этого валидны по
# лагу — подними margin, а не приноси gen CPU обратно.
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
  --pool-margin-s 0.05 \
  --max-pool-expansions 0 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics
