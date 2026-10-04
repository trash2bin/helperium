#!/usr/bin/env bash
# Останавливает нативный стенд, поднятый start-stand.sh.
#
# Стены-зависимости (infra_helperium-net, infra-rag-1, autoparts-store-*)
# не трогаются: стенд стартует отдельными процессами на loopback и сети не
# использует (AGENTS.md, Demo isolation).
set -euo pipefail
cd "$(dirname "$0")/../.."

for name in stub api mcp data; do
  pid_file=".data/pids/$name.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    kill "$(cat "$pid_file")" 2>/dev/null || true
    echo "$name: killed pid $(cat "$pid_file")"
  fi
  rm -f "$pid_file"
done

# Порт может принадлежать процессу, запущенному вне pid-файлов (например,
# предыдущей сессии): гасим и его — но только порты стенда, не чужие.
for port in 8081 8083 8084 9099; do
  if lsof -ti :"$port" >/dev/null 2>&1; then
    kill "$(lsof -ti :"$port")" 2>/dev/null || true
    echo "port $port: killed stale process"
  fi
done

echo "stand is down"
