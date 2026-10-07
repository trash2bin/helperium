#!/usr/bin/env bash
# Нативный стенд для doc/agents/api-service-decomposition-plan.md (§2.1).
#
# Поднимает четыре процесса на loopback: data-service, mcp-gateway,
# api-service и стаб LLM — тот же состав, что в замерах 2026-10-03.
# Останавливается ./scripts/stress/stop-stand.sh.
#
# Скрипт идемпотентен и рассчитан на чистую машину: повторный запуск гасит
# прошлый стенд, сборка Go-бинарников и ключ шифрования создаются только если
# их нет, а провал префлайта завершает скрипт ненулевым кодом. Раньше всё это
# приходилось добирать руками, и каждый пропущенный шаг всплывал как «стенд
# поднялся, но лестница не идёт».
set -euo pipefail
cd "$(dirname "$0")/../.."

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=stand.env
source "$SCRIPT_DIR/stand.env"

log() { printf '\n=== %s\n' "$*"; }
ok() { printf '  ok: %s\n' "$*"; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH$2"; }

# uv ставится пользовательским инсталлятором в ~/.local/bin, которого нет в
# неинтерактивном PATH по SSH: без этого `uv run` не находится.
export PATH="$HOME/.local/bin:$PATH"

need uv " (curl -LsSf https://astral.sh/uv/install.sh | sh)"
need curl

log "0. stop any previous stand"
"$SCRIPT_DIR/stop-stand.sh" >/dev/null 2>&1 || true
# Убить процесс и освободить порт - не одно и то же: uvicorn с --workers держит
# сокет всеми воркерами и закрывает его асинхронно (MCP-сессии, health-чеки,
# экспорт трейсов). Новый стенд стартовал раньше, чем порт освободился, не мог
# забиндиться и падал без единой строки в логе - на живой машине это выглядело
# как «стенд поднялся, но health не отвечает».
for port in 8081 8083 8084 9099; do
  for _ in $(seq 1 60); do
    lsof -ti :"$port" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if lsof -ti :"$port" >/dev/null 2>&1; then
    die "port $port is still held after the previous stand was stopped"
  fi
done
ok "previous stand stopped and ports released (if any)"

# ---- shared env (self-contained, no .env) ----
export ADMIN_TOKEN API_BEARER_TOKEN MCP_API_KEY MCP_CLIENT_API_KEY
export MCP_ALLOWED_ORIGINS MCP_RATE_LIMIT_RPS MCP_RATE_LIMIT_BURST
export ABUSE_IP_RPS ABUSE_IP_BURST ABUSE_MAX_USER_TURNS CHAT_RATE_LIMIT BACKLOG_MODE
export GUARDRAIL_ENABLED HEALTH_CHECK_SKIP_LLM MCP_DEV MCP_REQUIRE_AUTH
export AGENT_MAX_TURN_TOKENS AGENT_MAX_ITERATIONS API_WORKERS
export SESSION_STORAGE_URI LLM_PROVIDER_TRANSPORT
# Стенд — измерительный инструмент: трейсер, ретраящий protobuf-батчи в
# отсутствующий коллектор, меряет сам себя, а не платформу (py-spy 40 rps:
# urlopen/putheader/_encode_span). Значение ставит stand.env; export здесь
# пробрасывает его в процессы стенда.
export OTEL_SDK_DISABLED
# Rate-limit / abuse buckets: пусто = in-memory (откат). Внешнее хранилище
# задаётся переменной окружения, а не stand.env, чтобы стенд не зависел от
# порядка запуска E2E (см. комментарий в stand.env).
export RATE_LIMIT_STORAGE_URI="${RATE_LIMIT_STORAGE_URI:-}"
export ABUSE_STORAGE_URI="${ABUSE_STORAGE_URI:-}"
LOG_LEVEL=info
export LOG_LEVEL
mkdir -p .data/sessions .data/tenants .data/logs .data/pids

# ---- prerequisites, с которыми скрипт падал молча ----

log "1. encryption key"
# api-service fail-fast'ит, если в agents.sqlite есть строки с llm_config, а
# ENCRYPTION_KEY не задан. Ключ жил в .data и генерировался только dev.sh,
# поэтому чистый стенд падал на `cat .data/dev_encryption_key`.
if [[ ! -s .data/dev_encryption_key ]]; then
  uv run -- python -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    > .data/dev_encryption_key
  chmod 600 .data/dev_encryption_key
  ok "generated .data/dev_encryption_key"
else
  ok "existing .data/dev_encryption_key"
fi
ENCRYPTION_KEY=$(cat .data/dev_encryption_key)
export ENCRYPTION_KEY

log "2. redis (required by SESSION_STORAGE_URI / *_STORAGE_URI)"
# Стенд не проверял, что бэкенд, на который он переключил сессии и бакеты,
# вообще жив: провал всплывал уже префлайтом как SSE_ABORT.
if [[ -n "$SESSION_STORAGE_URI$RATE_LIMIT_STORAGE_URI$ABUSE_STORAGE_URI" ]]; then
  redis_ping() { redis-cli -u "$1" ping >/dev/null 2>&1; }
  for uri in "$SESSION_STORAGE_URI" "$RATE_LIMIT_STORAGE_URI" "$ABUSE_STORAGE_URI"; do
    [[ -z "$uri" ]] && continue
    if ! redis_ping "$uri"; then
      if command -v redis-server >/dev/null 2>&1; then
        redis-server --daemonize yes --port 6379 --bind 127.0.0.1 \
          --save "" --appendonly no >/dev/null 2>&1 || true
        sleep 1
      fi
      redis_ping "$uri" || die "$uri is unreachable (install redis or unset the *_STORAGE_URI flags)"
    fi
    ok "$uri"
  done
else
  ok "no external bucket backend configured (in-memory / SQLite)"
fi

log "3. drop stale state of the previous backend"
# Прошлый прогон мог оставить в Redis счётчики ходов и хеши сессий: с ними
# префлайт видел бы непустую историю, а лестница — чужие лимиты.
if [[ -n "$SESSION_STORAGE_URI" ]] && command -v redis-cli >/dev/null 2>&1; then
  redis-cli -u "$SESSION_STORAGE_URI" flushall >/dev/null 2>&1 || true
  ok "flushed $SESSION_STORAGE_URI"
fi

log "4. build go binaries (only if missing)"
need go " (pacman -S go / apt install golang)"
if [[ ! -x services/data-service/bin/data-service ]]; then
  (cd services/data-service && go build -o bin/data-service ./cmd/server/) \
    || die "data-service build failed"
  ok "built services/data-service/bin/data-service"
else
  ok "services/data-service/bin/data-service (existing)"
fi
if [[ ! -x services/mcp-gateway/mcp-gateway ]]; then
  (cd services/mcp-gateway && go build -o mcp-gateway ./cmd/...) \
    || die "mcp-gateway build failed"
  ok "built services/mcp-gateway/mcp-gateway"
else
  ok "services/mcp-gateway/mcp-gateway (existing)"
fi

log "5. sync python workspace"
uv sync --quiet || die "uv sync failed"
ok "uv sync"

wait_http() { # url, attempts, label, log
  local url=$1 attempts=$2 label=$3 lg=$4
  for _ in $(seq 1 "$attempts"); do
    curl -sf "$url" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  printf '\n--- last 20 lines of %s ---\n' "$lg" >&2
  tail -20 "$lg" >&2 || true
  die "$label did not become healthy at $url"
}

log "6. data-service"
TENANTS_DIR=$(pwd)/.data/tenants DATA_PORT=8084 LOG_LEVEL=info \
  nohup services/data-service/bin/data-service > .data/logs/data.log 2>&1 &
echo $! > .data/pids/data.pid
wait_http http://127.0.0.1:8084/health 20 data-service .data/logs/data.log
ok "$(curl -s http://127.0.0.1:8084/health)"

log "7. mcp-gateway"
MCP_PORT=8083 MCP_HOST=127.0.0.1 DATA_SERVICE_URL=http://127.0.0.1:8084 LOG_LEVEL=info \
  nohup services/mcp-gateway/mcp-gateway > .data/logs/mcp.log 2>&1 &
echo $! > .data/pids/mcp.pid
wait_http http://127.0.0.1:8083/health 40 mcp-gateway .data/logs/mcp.log
ok "$(curl -s http://127.0.0.1:8083/health)"

log "8. api-service"
# uvicorn с --workers живёт родителем, который плодит воркеры: pid-файл хранит
# родителя, и stop-stand гасит всё дерево через порт.
DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=8081 \
  MCP_GATEWAY_URL=http://127.0.0.1:8083 \
  MCP_STREAMABLE_HTTP_URL=http://127.0.0.1:8083/mcp \
  nohup uv run --package api-service python -m api_service.server \
  > .data/logs/api.log 2>&1 &
echo $! > .data/pids/api.pid
wait_http http://127.0.0.1:8081/health 90 api-service .data/logs/api.log
ok "$(curl -s http://127.0.0.1:8081/health | head -c 200)"

log "9. stub LLM (p50=${STUB_P50_MS}ms p95=${STUB_P95_MS}ms)"
"$SCRIPT_DIR/start-stub.sh" restart
# У стаба нет /v1/models (только POST /v1/chat/completions) - проверка здоровья
# идёт по /health, иначе скрипт считает живой стаб мёртвым.
wait_http http://127.0.0.1:9099/health 40 "stub LLM" .data/logs/stub.log
ok "stub up"

log "10. seed tenants"
for i in 1 2 3 4; do
  uv run --package agent-db agent-db register "stress-$i" sqlite-testseed >/dev/null \
    || die "tenant stress-$i registration failed"
done
ok "tenants stress-1..stress-4"

log "11. agent stress-agent (create or update)"
# Профили L2 объявлены со scope=separate: один тенант на scope. Композитный
# агент (два тенанта) заставляет gateway публиковать префиксные имена
# (stress-1__db_map, stress-2__db_map), и стаб отказывает шагу с логическим
# именем как неоднозначному — префлайт падал на SSE_ABORT. Держим агента
# однотенантным, как в замерах L2.
AGENT_NAME=stress-agent
AGENT_URL="http://127.0.0.1:8081/api/agents/$AGENT_NAME"
LLM_CONFIG='{"provider":"openai","model":"stub","api_base":"http://127.0.0.1:9099/v1","api_key":"sk-stub"}'
AUTH=(-H "Authorization: Bearer $API_BEARER_TOKEN" -H "Content-Type: application/json")
if curl -sf "$AGENT_URL" "${AUTH[@]}" >/dev/null 2>&1; then
  # Повторный запуск: POST вернул бы 409 и оставил старый агент (так и жил
  # композитный scope от прошлой сессии). PUT переписывает scope на месте.
  response=$(curl -sS -X PUT "$AGENT_URL" "${AUTH[@]}" \
    -d "{\"description\":\"stress test agent\",\"tenant_ids\":[\"stress-1\"],\"llm_config\":$LLM_CONFIG}")
  ok "updated existing $AGENT_NAME"
else
  response=$(curl -sS -X POST http://127.0.0.1:8081/api/agents "${AUTH[@]}" \
    -d "{\"name\":\"$AGENT_NAME\",\"description\":\"stress test agent\",\"tenant_ids\":[\"stress-1\"],\"llm_config\":$LLM_CONFIG}")
  ok "created $AGENT_NAME"
fi
printf '%s' "$response" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('  ', d.get('name','NO_NAME'), d.get('tenant_ids','NO_TENANTS'))" \
  || die "agent payload rejected: $response"

log "12. preflight"
# Префлайт — ворота: стенд считается поднятым только если он реально способен
# прогнать профиль. Раньше скрипт печатал ❌ и всё равно завершался нулём,
# поэтому «стенд поднят» ничего не гарантировало.
if ! uv run --package agent-db agent-db-stress check \
  services/agent-db/agent_db/stress/profiles/chat-zero-latency-l2.json \
  --chat-url http://127.0.0.1:8081 --agent "$AGENT_NAME" \
  --tenants stress-1,stress-2 \
  --budget chat_rate_limit="$CHAT_RATE_LIMIT" \
  --budget mcp_rate_limit_rps="$MCP_RATE_LIMIT_RPS"; then
  die "preflight refused: the stand is up but cannot run the profile (see the ❌ lines above)"
fi

printf '\n=== DONE: stand is up (workers=%s, transport=%s, sessions=%s)\n' \
  "$API_WORKERS" "$LLM_PROVIDER_TRANSPORT" "${SESSION_STORAGE_URI:-sqlite}"
