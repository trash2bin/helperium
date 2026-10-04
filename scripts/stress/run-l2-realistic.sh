#!/usr/bin/env bash
# L2-realistic: чат через api-service с реалистичным стабом (профиль
# chat-slow-l2) — второй профиль Трека 0 плана api-service-decomposition-plan.md.
#
# Чем отличается от run-l2.sh (нано-L2):
#   - стаб отвечает с секундной задержкой (p50 2000 мс / p95 4000 мс), а не
#     за миллисекунды;
#   - тело ответа приходит 300 чанками с паузой 2 мс между ними (≈600 мс
#     доставки), а не одним JSON — чанки и есть стоимость, которую скрывает
#     нулевой стаб.
#
# Важно: api-service не читает LLM-стрим (litellm complete() шлёт
# не-стриминговый запрос), поэтому чанки — это HTTP-доставка того же JSON, а
# не токены модели. Мерится разбор тела ответа и жизнь соединения при
# секундных ходах, а не per-token streaming.
#
# Запуск: ./scripts/stress/run-l2-realistic.sh
# Ожидание: стенд поднят (start-stand.sh); стаб будет перезапущен внутри.
#
# Бюджет T=8500 мс — НЕ тот же T, что у нано-L2 (500 мс), и это осознанно:
# judged-метрика L2 = compensated turn latency, то есть в неё входит и время
# стаба (слой-правило: "stub is ~0-latency; seconds are not a criterion").
# При p50 2с ход занимает 2-3 раунда модели ≈ 5-8 с — при T=500 мс каждая
# стадия провалилась бы из-за модели, а не платформы (ровно то, чего §7
# запрещает). T=8500 = p95-модели (2 раунда x 4с) + чанки (0.6 с) + запас
# платформенного бюджета 500 мс. Итог knee на этом профиле читается как
# "потолок ёмкости при реалистичной нагрузке", а не как platform-overhead.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/stress/stand.env
export ADMIN_TOKEN MCP_API_KEY API_BEARER_TOKEN
export BACKLOG_MODE GUARDRAIL_ENABLED LOG_LEVEL AGENT_MAX_TURN_TOKENS
export AGENT_MAX_ITERATIONS=5

# Стаб реалистичного профиля — отдельный процесс со своими параметрами.
# Остальные сервисы не трогаем: агент ходит в стаб по api_base из конфига.
export STUB_P50_MS=2000 STUB_P95_MS=4000
export STUB_RESPONSE_CHUNKS=300 STUB_CHUNK_DELAY_MS=2

# Возвращаем стенд в дефолтное (нано-L2) состояние при любом исходе — в том
# числе когда лестница падает раньше времени (set -e убивает скрипт до конца).
restore_stub() {
  STUB_P50_MS=1 STUB_P95_MS=5 STUB_RESPONSE_CHUNKS=1 STUB_CHUNK_DELAY_MS=0 \
    scripts/stress/start-stub.sh restart
}
trap restore_stub EXIT

scripts/stress/start-stub.sh restart
sleep 2
curl -sf http://127.0.0.1:9099/health >/dev/null || {
  echo "stub did not come up" >&2
  exit 1
}

uv run --package agent-db agent-db-stress run \
  services/agent-db/agent_db/stress/profiles/chat-slow-l2.json \
  --chat-url http://127.0.0.1:8081 --agent stress-agent \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2 \
  --artifact-root test-results/stress \
  --repo-root . \
  --rates 5,10,20,40 \
  --duration-s 90 --warmup-s 20 \
  --t-budget-ms 8500 \
  --repeats 2 \
  --admin-token "$ADMIN_TOKEN" \
  --metrics-target data-service=http://127.0.0.1:8084/metrics
