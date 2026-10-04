#!/usr/bin/env bash
# Стаб LLM для стенда (services/agent-db/agent_db/stress/stub_llm.py).
#
# start-stub.sh restart — (пере)запускает стаб со значениями из stand.env;
# stop                     — гасит текущий стаб.
#
# Latency-профиль стаба — единственная разница между chat-zero-latency-l2
# (наносекунды, дефолт stand.env) и chat-slow-l2 (секунды): см.
# run-l2-realistic.sh. Агент и остальные сервисы при смене профиля стаба
# перезапускать не нужно — агент ходит в стаб по api_base из своей конфигурации.
set -euo pipefail
cd "$(dirname "$0")/../.."

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=stand.env
source "$SCRIPT_DIR/stand.env"

STUB_PID_FILE=.data/pids/stub.pid
STUB_LOG=${STUB_LOG:-.data/logs/stub.log}
# Тайминги стаба — это §7 доказательная база: платформенный оверхед харнесс
# считает как t_complete минус суммарное время стаба по этому журналу.
STUB_TIMINGS_LOG=${STUB_TIMINGS_LOG:-stub-timings.jsonl}

stop_stub() {
  if [[ -f "$STUB_PID_FILE" ]] && kill -0 "$(cat "$STUB_PID_FILE")" 2>/dev/null; then
    kill "$(cat "$STUB_PID_FILE")" 2>/dev/null || true
    sleep 0.5
  fi
  # nohup-обёртка живёт дольше, чем pid-файл: добиваем по порту, если он ещё занят.
  if lsof -ti :9099 >/dev/null 2>&1; then
    kill "$(lsof -ti :9099)" 2>/dev/null || true
    sleep 0.5
  fi
  rm -f "$STUB_PID_FILE"
}

start_stub() {
  mkdir -p .data/pids
  nohup uv run --package agent-db agent-db-stress stub \
    --p50-ms "$STUB_P50_MS" --p95-ms "$STUB_P95_MS" \
    --prompt-ms-per-token "${STUB_PROMPT_MS_PER_TOKEN:-0}" \
    --concurrency "$STUB_CONCURRENCY" \
    --response-chunks "$STUB_RESPONSE_CHUNKS" \
    --chunk-delay-ms "$STUB_CHUNK_DELAY_MS" \
    --script services/agent-db/agent_db/stress/fixtures/stub-script.jsonl \
    --log "$STUB_TIMINGS_LOG" \
    --host 127.0.0.1 --port 9099 \
    > "$STUB_LOG" 2>&1 &
  echo $! > "$STUB_PID_FILE"
}

case "${1:-restart}" in
  restart)
    stop_stub
    start_stub
    echo "stub: p50=${STUB_P50_MS}ms p95=${STUB_P95_MS}ms chunks=${STUB_RESPONSE_CHUNKS} log=$STUB_TIMINGS_LOG"
    ;;
  stop)
    stop_stub
    echo "stub stopped"
    ;;
  *)
    echo "usage: $0 [restart|stop]" >&2
    exit 2
    ;;
esac
