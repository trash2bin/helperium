#!/usr/bin/env bash
set -euo pipefail

readonly api_url="http://127.0.0.1:18081/api/chat"
readonly events_file="/tmp/helperium-sse-gateway-outage.events"
readonly curl_stderr="/tmp/helperium-sse-gateway-outage.curl.stderr"
readonly gateway_container="infra-mcp-gateway-1"
readonly data_container="infra-data-service-1"

cleanup() {
  if [ "$(docker inspect -f '{{.State.Status}}' "$data_container" 2>/dev/null || true)" = "paused" ]; then
    docker unpause "$data_container" >/dev/null
  fi
  if docker inspect -f '{{.State.Running}}' "$gateway_container" 2>/dev/null | grep -qx true; then
    return
  fi
  docker start "$gateway_container" >/dev/null
  for _ in $(seq 1 20); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "$gateway_container" 2>/dev/null || true)" = "healthy" ]; then
      return
    fi
    sleep 1
  done
  echo "gateway did not recover to healthy state" >&2
  exit 1
}
trap cleanup EXIT

: >"$events_file"
: >"$curl_stderr"
curl -sS -N --max-time 45 \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'User-Agent: helperium-e2e-resilience/1.0' \
  --data '{"message":"Find the books category.","session_id":"sse-gateway-outage-regression","lang":"en"}' \
  "$api_url" >"$events_file" 2>"$curl_stderr" &
curl_pid=$!

for _ in $(seq 1 100); do
  if grep -Eq '"type"[[:space:]]*:[[:space:]]*"tool_call"' "$events_file"; then
    docker pause "$data_container" >/dev/null
    sleep 0.3
    docker kill "$gateway_container" >/dev/null
    break
  fi
  if ! kill -0 "$curl_pid" 2>/dev/null; then
    echo "SSE request ended before tool_call" >&2
    cat "$events_file" >&2
    cat "$curl_stderr" >&2
    exit 1
  fi
  sleep 0.05
done

if ! grep -Eq '"type"[[:space:]]*:[[:space:]]*"tool_call"' "$events_file"; then
  echo "Timed out waiting for tool_call" >&2
  cat "$events_file" >&2
  cat "$curl_stderr" >&2
  exit 1
fi
wait "$curl_pid" || true

if ! grep -Eq '"type"[[:space:]]*:[[:space:]]*"error"' "$events_file"; then
  echo "Missing terminal error event after active MCP gateway outage" >&2
  cat "$events_file" >&2
  cat "$curl_stderr" >&2
  exit 1
fi
if ! grep -Eq '"type"[[:space:]]*:[[:space:]]*"done"' "$events_file"; then
  echo "Missing terminal done event after active MCP gateway outage" >&2
  cat "$events_file" >&2
  cat "$curl_stderr" >&2
  exit 1
fi
if grep -Eqi 'traceback|mcp-gateway|connection refused|127\.0\.0\.1|exception' "$events_file"; then
  echo "Terminal SSE error leaked internal transport detail" >&2
  cat "$events_file" >&2
  exit 1
fi

printf '%s\n' "SSE gateway-outage regression passed"
cat "$events_file"
