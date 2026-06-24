#!/usr/bin/env bash
# =============================================================================
# dev.sh — нативный запуск всех сервисов (Mac / без Docker)
# =============================================================================
# Usage:
#   ./scripts/dev.sh start         — поднять все сервисы
#   ./scripts/dev.sh stop          — погасить все
#   ./scripts/dev.sh status        — healthcheck каждого
#   ./scripts/dev.sh logs [svc]    — tail -f лога (svc: rag|mcp|api|web|all)
#   ./scripts/dev.sh restart       — stop + start
#
# Дефолты из .env (если есть), иначе встроенные.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/.data/logs"
PID_DIR="$PROJECT_ROOT/.data/pids"

SERVICES=("rag" "mcp" "api" "web")
declare -A SERVICE_CMD=(
  [rag]="uv run --package rag python -m rag.service"
  [mcp]="uv run --package mcp_server python -m mcp_server.server"
  [api]="uv run --package demo-api python -m demo.api.server"
  [web]="uv run --package demo-web python -m demo.web.server"
)

# Дефолтные порты (перебиваются из .env)
RAG_PORT=${RAG_PORT:-8082}
MCP_PORT=${MCP_PORT:-8083}
API_PORT=${API_PORT:-8081}
WEB_PORT=${WEB_PORT:-8080}

declare -A SERVICE_PORT=(
  [rag]=$RAG_PORT
  [mcp]=$MCP_PORT
  [api]=$API_PORT
  [web]=$WEB_PORT
)

# Какие сервисы ждать по health перед стартом следующих
declare -A SERVICE_DEPS=(
  [rag]=""
  [mcp]="rag"
  [api]="mcp"
  [web]="api"
)

# =============================================================================
# Utils
# =============================================================================

load_env() {
  if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_ROOT/.env"
    set +a
    # перечитываем порты из env после source
    RAG_PORT=${RAG_PORT:-8082}
    MCP_PORT=${MCP_PORT:-8083}
    API_PORT=${API_PORT:-8081}
    WEB_PORT=${WEB_PORT:-8080}
    SERVICE_PORT=([rag]=$RAG_PORT [mcp]=$MCP_PORT [api]=$API_PORT [web]=$WEB_PORT)
  fi
}

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$PID_DIR"
}

pidfile() { echo "$PID_DIR/$1.pid"; }
logfile() { echo "$LOG_DIR/$1.log"; }

is_running() {
  local svc="$1"
  local pid
  pid=$(cat "$(pidfile "$svc")" 2>/dev/null || echo "")
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

health_url() {
  local svc="$1"
  case "$svc" in
    rag) echo "http://127.0.0.1:$RAG_PORT/health" ;;
    mcp) echo "http://127.0.0.1:$MCP_PORT/health" ;;
    api) echo "http://127.0.0.1:$API_PORT/health" ;;
    web) echo "http://127.0.0.1:$WEB_PORT/" ;;
  esac
}

# =============================================================================
# Commands
# =============================================================================

cmd_start() {
  load_env
  ensure_dirs

  # Проверка uv
  if ! command -v uv &>/dev/null; then
    echo "❌ uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi

  # Проверка что .venv синхронизирован
  if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo "⚠️  .venv not found, running uv sync..."
    # uv sync ставит dev-зависимости, uv pip install -e — workspace members
    # (чтобы их транзитивные зависимости тоже установились)
    (cd "$PROJECT_ROOT" && uv sync --group dev && uv pip install -e agent-tutor-sdk -e mcp_server -e rag -e demo/api -e demo/web -e fixtures)
    
    # .pth для editable install: hatchling кладёт папку пакета на sys.path вместо корня проекта
    local pyver=$("$PROJECT_ROOT/.venv/bin/python3" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo "$PROJECT_ROOT" > "$PROJECT_ROOT/.venv/lib/python$pyver/site-packages/_project_root.pth"
  fi

  # Напоминание про PostgreSQL, если задан DATABASE_URL
  if [ -n "${DATABASE_URL:-}" ]; then
    echo "ℹ️  Режим PostgreSQL (DATABASE_URL задана)."
    echo "   Убедись что БД запущена: docker compose up -d db"
    echo ""
  fi

  echo "🔄 Starting services..."

  for svc in "${SERVICES[@]}"; do
    if is_running "$svc"; then
      echo "  ⏭️  $svc already running (pid $(cat "$(pidfile "$svc")"))"
      continue
    fi

    # Ждём зависимости
    local dep="${SERVICE_DEPS[$svc]}"
    if [ -n "$dep" ]; then
      echo "  ⏳ Waiting for $dep before starting $svc..."
      if ! wait_healthy "$dep" 60; then
        echo "  ❌ $dep not ready, skipping $svc"
        continue
      fi
    fi

    # Доп. env для сервиса
    local extra_env=""
    case "$svc" in
      mcp) extra_env="RAG_SERVICE_URL=http://127.0.0.1:$RAG_PORT" ;;
      api) extra_env="MCP_SERVICE_URL=http://127.0.0.1:$MCP_PORT/mcp" ;;
      web) extra_env="DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=$API_PORT" ;;
    esac

    echo "  🚀 Starting $svc..."
    cd "$PROJECT_ROOT"
    # shellcheck disable=SC2086
    env $extra_env ${SERVICE_CMD[$svc]} >> "$(logfile "$svc")" 2>&1 &
    local pid=$!
    echo "$pid" > "$(pidfile "$svc")"

    if wait_healthy "$svc" 30; then
      echo "  ✅ $svc ready (pid $pid, :${SERVICE_PORT[$svc]})"
    else
      echo "  ⚠️  $svc started but not healthy yet (check logs: tail -f $(logfile "$svc"))"
    fi
  done

  echo ""
  echo "🎉 All services launched!"
  echo ""
  echo "  RAG    http://127.0.0.1:$RAG_PORT    logs: $(logfile rag)"
  echo "  MCP    http://127.0.0.1:$MCP_PORT    logs: $(logfile mcp)"
  echo "  API    http://127.0.0.1:$API_PORT    logs: $(logfile api)"
  echo "  WEB    http://127.0.0.1:$WEB_PORT    logs: $(logfile web)"
  echo ""
  echo "  Commands:"
  echo "    ./scripts/dev.sh status     — healthcheck"
  echo "    ./scripts/dev.sh logs api   — tail -f лога"
  echo "    ./scripts/dev.sh stop       — остановить все"
}

wait_healthy() {
  local svc="$1"
  local timeout="${2:-30}"
  local url
  url=$(health_url "$svc")
  [ -z "$url" ] && return 0

  echo -n "    polling $svc "
  for ((i = 0; i < timeout; i++)); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo ""
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  return 1
}

kill_pid_graceful() {
  local pid="$1"
  local name="$2"
  local timeout="${3:-5}"

  # SIGTERM — graceful shutdown
  kill "$pid" 2>/dev/null || return 0

  # Ждём завершения с таймаутом
  for ((i = 0; i < timeout; i++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done

  # Не завершился — SIGKILL
  echo "  ⚠️  $name (pid $pid) did not stop in ${timeout}s, sending SIGKILL..."
  kill -9 "$pid" 2>/dev/null || true
  sleep 1
  return 0
}

kill_pids_graceful() {
  local pids="$1"
  local name="$2"
  local timeout="${3:-5}"

  for pid in $pids; do
    # trim whitespace
    pid="${pid//[[:space:]]/}"
    [ -z "$pid" ] && continue
    kill_pid_graceful "$pid" "$name" "$timeout"
  done
}

_stop_svc_by_pidfile() {
  local svc="$1"
  local pid
  pid=$(cat "$(pidfile "$svc")" 2>/dev/null || echo "")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "  Stopping $svc (pid $pid)..."
    kill_pid_graceful "$pid" "$svc" 5
    rm -f "$(pidfile "$svc")"
    return 0
  fi
  return 1
}

_stop_svc_by_pgrep() {
  local svc="$1"
  local pattern
  case "$svc" in
    rag) pattern="python -m rag.service" ;;
    mcp) pattern="python -m mcp_server.server" ;;
    api) pattern="python -m demo.api.server" ;;
    web) pattern="python -m demo.web.server" ;;
  esac
  local pids
  pids=$(pgrep -f "$pattern" 2>/dev/null || echo "")
  if [ -n "$pids" ]; then
    echo "  Stopping $svc via pgrep (pids: $(echo "$pids" | tr '\n' ' '))..."
    kill_pids_graceful "$pids" "$svc" 5
    rm -f "$(pidfile "$svc")"
    return 0
  fi
  return 1
}

cmd_stop() {
  echo "🛑 Stopping services..."
  # Останавливаем в обратном порядке (web → api → mcp → rag)
  for ((idx = ${#SERVICES[@]} - 1; idx >= 0; idx--)); do
    local svc="${SERVICES[$idx]}"
    if ! _stop_svc_by_pidfile "$svc"; then
      _stop_svc_by_pgrep "$svc" || echo "  ⏭️  $svc not running"
    fi
  done
  rm -f "$PID_DIR"/*.pid 2>/dev/null || true
  echo "✅ All services stopped"
}

cmd_status() {
  load_env
  echo "📊 Service status:"
  echo ""
  local all_ok=true
  for svc in "${SERVICES[@]}"; do
    local url
    url=$(health_url "$svc")
    local port="${SERVICE_PORT[$svc]}"
    local pid_info=""
    if is_running "$svc"; then
      pid_info="(pid $(cat "$(pidfile "$svc")" 2>/dev/null))"
    fi

    if [ -n "$url" ] && curl -sf "$url" >/dev/null 2>&1; then
      echo "  ✅ $svc   :$port   healthy $pid_info"
    elif is_running "$svc"; then
      echo "  ⚠️  $svc   :$port   running but not responsive $pid_info"
      all_ok=false
    else
      echo "  ❌ $svc   :$port   not running"
      all_ok=false
    fi
  done
  echo ""
  [ "$all_ok" = true ] && echo "🎉 All services healthy!" || echo "⚠️  Some services have issues"
  [ "$all_ok" = true ]
}

cmd_logs() {
  local svc="${1:-all}"
  if [ "$svc" = "all" ]; then
    echo "📋 Tailing all logs (Ctrl+C to stop)..."
    tail -f "$LOG_DIR"/*.log 2>/dev/null || echo "No log files found"
  else
    local log_file="$LOG_DIR/$svc.log"
    if [ -f "$log_file" ]; then
      tail -f "$log_file"
    else
      echo "❌ Log file not found: $log_file"
      exit 1
    fi
  fi
}

# =============================================================================
# Main
# =============================================================================

case "${1:-help}" in
  start)
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  restart)
    cmd_stop
    sleep 1
    cmd_start
    ;;
  status)
    cmd_status
    ;;
  logs)
    cmd_logs "${2:-all}"
    ;;
  help|--help|-h)
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  start              — поднять все сервисы"
    echo "  stop               — погасить все"
    echo "  restart            — перезапустить"
    echo "  status             — healthcheck"
    echo "  logs [svc]         — tail -f логов (rag|mcp|api|web|all)"
    echo ""
    echo "Env:"
    echo "  .env в корне проекта — автоматически подгружается"
    exit 0
    ;;
  *)
    echo "❌ Unknown command: $1"
    echo "Usage: $0 start|stop|restart|status|logs"
    exit 1
    ;;
esac
