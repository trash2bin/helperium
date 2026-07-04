#!/usr/bin/env bash
# =============================================================================
# dev.sh — нативный запуск всех сервисов (Mac / без Docker)
# =============================================================================
# Usage:
#   ./scripts/dev.sh start         — поднять все сервисы (data + rag + mcp + api + web)
#   ./scripts/dev.sh stop          — погасить все
#   ./scripts/dev.sh status        — healthcheck каждого
#   ./scripts/dev.sh logs [svc]    — tail -f лога (svc: rag|mcp|api|web|data|all)
#   ./scripts/dev.sh restart       — stop + start
#
# Сценарии data-service (фабрика тестовых БД):
#   ./scripts/dev.sh db list                       — список сценариев + метаданные
#   ./scripts/dev.sh db materialize <name> [--force] — создать БД из config.json+seed.json
#   ./scripts/dev.sh db serve <name>               — запустить data-service на сценарии (fg)
#   ./scripts/dev.sh db test [all|<name>]          — Go-тесты на сценариях
#   ./scripts/dev.sh db drop <name>                — удалить материализованную БД
#   ./scripts/dev.sh db help                       — подробная справка
#
# Дефолты из .env (если есть), иначе встроенные.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/.data/logs"
PID_DIR="$PROJECT_ROOT/.data/pids"

SERVICES=("data" "rag" "mcp" "api" "web")
declare -A SERVICE_CMD=(
  [data]="LOG_LEVEL=info $PROJECT_ROOT/data-service/bin/data-service ${DS_CONFIG:+--config $DS_CONFIG}"
  [rag]="uv run --package rag python -m rag.service"
  [mcp]="$PROJECT_ROOT/mcp-gateway/mcp-gateway"
  # Legacy (Python): раскомментировать для отладки

  [api]="uv run --package demo-api python -m demo.api.server"
  [web]="uv run --package demo-web python -m demo.web.server"
)

# Дефолтные порты (перебиваются из .env)
DATA_PORT=${DATA_PORT:-8084}
RAG_PORT=${RAG_PORT:-8082}
MCP_PORT=${MCP_PORT:-8083}
API_PORT=${API_PORT:-8081}
WEB_PORT=${WEB_PORT:-8080}

declare -A SERVICE_PORT=(
  [data]=$DATA_PORT
  [rag]=$RAG_PORT
  [mcp]=$MCP_PORT
  [api]=$API_PORT
  [web]=$WEB_PORT
)

# Какие сервисы ждать по health перед стартом следующих
declare -A SERVICE_DEPS=(
  [data]=""
  [rag]=""
  [mcp]="data rag"
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
    DATA_PORT=${DATA_PORT:-8084}
    MCP_PORT=${MCP_PORT:-8083}
    API_PORT=${API_PORT:-8081}
    WEB_PORT=${WEB_PORT:-8080}
    SERVICE_PORT=([data]=$DATA_PORT [rag]=$RAG_PORT [mcp]=$MCP_PORT [api]=$API_PORT [web]=$WEB_PORT)

    # Если DATABASE_URL задана, а DS_CONFIG нет — авто-выбор PostgreSQL конфига
    if [ -n "${DATABASE_URL:-}" ] && [ -z "${DS_CONFIG:-}" ]; then
      DS_CONFIG="$PROJECT_ROOT/specs/config.postgres.json"
    fi
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
    data) echo "http://127.0.0.1:$DATA_PORT/health" ;;
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
  MCP_DEV=true
  echo "🧪 Dev mode enabled — MCP Playground at http://127.0.0.1:${MCP_PORT:-8083}/debug"

  load_env
  ensure_dirs

  # Если DATABASE_URL задана — переопределяем data-service на PG-конфиг
  if [ -n "${DATABASE_URL:-}" ]; then
    SERVICE_CMD[data]="LOG_LEVEL=info $PROJECT_ROOT/data-service/bin/data-service --config $PROJECT_ROOT/specs/config.postgres.json"
  fi

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
    (cd "$PROJECT_ROOT" && uv sync --group dev && uv pip install -e agent-tutor-sdk -e rag -e demo/api -e demo/web -e fixtures)
    
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

  # Собираем Go-бинарники
  echo "  🔨 Building data-service..."
  mkdir -p "$PROJECT_ROOT/data-service/bin"
  (cd "$PROJECT_ROOT/data-service" && go build -o bin/data-service ./cmd/server/) || {
    echo "  ❌ Failed to build data-service"
    exit 1
  }
  echo "  ✅ data-service built"

  echo "  🔨 Building mcp-gateway..."
  (cd "$PROJECT_ROOT/mcp-gateway" && go build -o mcp-gateway ./cmd/) || {
    echo "  ❌ Failed to build mcp-gateway"
    exit 1
  }
  echo "  ✅ mcp-gateway built"

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
      data)
        # ADMIN_TOKEN — если задан в .env, прокидываем в data-service для /admin/* эндпоинтов
        if [ -n "${ADMIN_TOKEN:-}" ]; then
          extra_env="ADMIN_TOKEN=$ADMIN_TOKEN"
        fi
        ;;
      mcp)
        extra_env="DATA_SERVICE_URL=http://127.0.0.1:$DATA_PORT LOG_LEVEL=info"
        if [ "$MCP_DEV" = "true" ]; then
          extra_env="MCP_DEV=true $extra_env"
        fi
        ;;
      api) extra_env="MCP_SERVICE_URL=http://127.0.0.1:$MCP_PORT" ;;
      web) extra_env="DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=$API_PORT" ;;
    esac

    echo "  🚀 Starting $svc..."
    cd "$PROJECT_ROOT"
    # shellcheck disable=SC2086
    # Detach the child so it survives after this shell exits and loses its tty.
    nohup env $extra_env ${SERVICE_CMD[$svc]} >> "$(logfile "$svc")" 2>&1 < /dev/null &
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
  echo "  DATA   http://127.0.0.1:$DATA_PORT    logs: $(logfile data)"
  echo "  RAG    http://127.0.0.1:$RAG_PORT    logs: $(logfile rag)"
  echo "  MCP    http://127.0.0.1:$MCP_PORT    logs: $(logfile mcp)"
  echo "  API    http://127.0.0.1:$API_PORT    logs: $(logfile api)"
  echo "  WEB    http://127.0.0.1:$WEB_PORT    logs: $(logfile web)"
  echo ""
  echo "  Commands:"
  echo "    ./scripts/dev.sh status          — healthcheck"
  echo "    ./scripts/dev.sh logs api        — tail -f лога"
  echo "    ./scripts/dev.sh stop            — остановить все"
  echo "    ./scripts/dev.sh start          — dev-режим (MCP Playground ��строен)"
  if [ -n "${ADMIN_TOKEN:-}" ]; then
    echo ""
    echo "  🔐 ADMIN_TOKEN задан — admin-эндпоинты data-service активны:"
    echo "    curl -H 'Authorization: Bearer \$ADMIN_TOKEN' http://127.0.0.1:$DATA_PORT/admin/tenants"
    echo "    POST   /admin/tenants         — добавить tenant"
    echo "    GET    /admin/tenants         — список tenant'ов"
    echo "    DELETE /admin/tenants/{id}    — удалить tenant"
    echo "    GET    /admin/config          — текущий конфиг"
    echo "    POST   /admin/config/reload   — перечитать конфиг с диска"
  else
    echo ""
    echo "  ⚠️  ADMIN_TOKEN не задан — /admin/* эндпоинты data-service недоступны (401)."
    echo "      Задай в .env: ADMIN_TOKEN=my-secret-token"
  fi
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
    data) pattern="data-service/bin/data-service" ;;
    rag) pattern="python -m rag.service" ;;
    mcp) pattern="mcp-gateway" ;; # Go-бинарник
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
# db — управление сценариями data-service (config.json + seed.json)
# =============================================================================
# Создание / пересоздание / сброс тестовых БД и запуск data-service
# на конкретном сценарии. Не трогает остальные сервисы (rag/mcp/api/web).
#
# Архитектура: data-service/README.md § "Сценарии — фабрика тестовых БД"
# =============================================================================

SCENARIOS_DIR="$PROJECT_ROOT/scenarios"
CONFIG_SCHEMA="${CONFIG_SCHEMA:-$PROJECT_ROOT/specs/config.schema.json}"

# Delegate db commands to agent-db CLI
# Set AGENT_TUTOR_ROOT so agent-db finds the project root regardless of cwd
export AGENT_TUTOR_ROOT="$PROJECT_ROOT"

# db subcommand: ./scripts/dev.sh db <list|materialize|serve|test|drop>
cmd_db() {
  load_env
  local op="${1:-help}"
  shift || true

  case "$op" in
    list)       uv run agent-db scenarios "$@" ;;
    materialize) uv run agent-db materialize "$@" ;;
    serve)      uv run agent-db serve "$@" ;;
    test)       uv run agent-db test "$@" ;;
    drop)       uv run agent-db drop "$@" ;;
    help|--help|-h) uv run agent-db --help ;;
    *)
      echo "❌ Unknown db subcommand: $op"
      uv run agent-db --help
      exit 1
      ;;
  esac
}

# Резолвит имя сценария → абсолютный путь директории.
# Допускает:
#   - встроенное имя (sqlite-testseed, postgres-testseed, shop)
#   - относительный путь (./my-scenario, testdata/scenarios/...)
#   - абсолютный путь
_resolve_scenario() {
  local name="$1"
  if [ -z "$name" ]; then
    echo "❌ Scenario name required" >&2
    return 1
  fi
  if [[ "$name" == /* ]]; then
    [ -d "$name" ] || { echo "❌ Scenario dir not found: $name" >&2; return 1; }
    echo "$name"; return 0
  fi
  if [ -d "$SCENARIOS_DIR/$name" ]; then
    echo "$SCENARIOS_DIR/$name"; return 0
  fi
  if [ -d "$name" ]; then
    (cd "$name" && pwd); return 0
  fi
  echo "❌ Scenario not found: $name (looked in $SCENARIOS_DIR and as-is)" >&2
  return 1
}

# Печатает info о сценарии: driver, dsn, размер БД, размер seed.json.
_scenario_info() {
  local dir="$1" cfg="$dir/config.json"
  dir="${dir%/}"
  cfg="$dir/config.json"
  if [ ! -f "$cfg" ]; then
    echo "  ⚠️  no config.json"
    return
  fi
  local driver dsn
  driver=$(jq -r '.data_source.driver // "n/a"' "$cfg")
  dsn=$(jq -r '.data_source.dsn  // "n/a"' "$cfg")
  local entities endpoints cq
  entities=$(jq  -r '.entities | length'                "$cfg")
  endpoints=$(jq -r '.endpoints | length'               "$cfg")
  cq=$(jq       -r '.custom_queries | keys | length'     "$cfg" 2>/dev/null || echo "0")
  printf "  driver=%-9s entities=%-2s endpoints=%-2s cq=%-2s\n" "$driver" "$entities" "$endpoints" "$cq"
  printf "  dsn=%s\n" "$dsn"

  local db_path=""
  if [ "$driver" = "sqlite" ] && [[ "$dsn" != *"/"* ]]; then
    db_path="$dir/$dsn"
  fi
  if [ -n "$db_path" ] && [ -f "$db_path" ]; then
    local size; size=$(du -h "$db_path" | cut -f1)
    printf "  db=%s (%s)\n" "$db_path" "$size"
  elif [ "$driver" = "postgres" ]; then
    printf "  db=<PostgreSQL via DATABASE_URL>\n"
  fi

  local seed="$dir/seed.json"
  seed="${seed%/}"
  if [ -f "$seed" ]; then
    local rows_total
    rows_total=$(jq -r '
      [ .groups, .students, .teachers, .disciplines, .grades, .schedule ]
      | map(if type=="array" then length else 0 end)
      | add // 0
    ' "$seed" 2>/dev/null || echo "?")
    printf "  seed=%s (%s entities)\n" "$seed" "$rows_total"
  fi
}

cmd_db_list() {
  echo "📂 Available scenarios in $SCENARIOS_DIR:"
  echo ""
  if [ ! -d "$SCENARIOS_DIR" ]; then
    echo "  (directory not found)"; return
  fi
  local found=0
  for dir in "$SCENARIOS_DIR"/*/; do
    [ -d "$dir" ] || continue
    found=1
    local name; name=$(basename "$dir")
    echo "• $name"
    _scenario_info "$dir"
    echo ""
  done
  if [ "$found" -eq 0 ]; then
    echo "  (no scenarios found)"
  fi
  return 0
}

# Создать/пересоздать БД из сценария.
#   ./scripts/dev.sh db materialize <name>          — создать
#   ./scripts/dev.sh db materialize <name> --force — удалить и пересоздать
cmd_db_materialize() {
  local name="${1:-}"
  local force=""
  [ "${2:-}" = "--force" ] && force="--force"

  local dir; dir=$(_resolve_scenario "$name") || exit 1
  if [ ! -f "$dir/config.json" ]; then
    echo "❌ config.json not found in $dir"; exit 1
  fi

  # ---- Bootstrap (для сценариев без seed.json) ----
  # Если seed.json нет и в директории сценария лежит bootstrap.sh —
  # запускаем его для генерации data.db (или другой исходной БД).
  # Идея: bootstrap.sh — это "как получить БД когда готовой нет".
  # Только для сценариев где конфиг знает endpoint'ы но seed для них не описан
  # (например, demo-сценарий 'shop' с любой сторонней БД).
  local bootstrap_ran=0
  if [ ! -f "$dir/seed.json" ] && [ -f "$dir/bootstrap.sh" ]; then
    local need_bootstrap=0
    if [ -n "$force" ]; then
      need_bootstrap=1
    elif [ ! -f "$dir/data.db" ]; then
      need_bootstrap=1
    fi
    if [ "$need_bootstrap" = "1" ]; then
      echo "🏗️  Bootstrap: сценарий '$name' без seed.json, запускаю $dir/bootstrap.sh"
      bash "$dir/bootstrap.sh" || {
        echo "❌ Bootstrap-скрипт завершился с ошибкой"; exit 1
      }
      echo "✅ Bootstrap done"
      bootstrap_ran=1
    fi
  elif [ ! -f "$dir/seed.json" ]; then
    echo "⚠️  seed.json not found — schema will be created but no data loaded."
  fi

  echo "🔨 Materializing scenario: $name"
  echo "   dir: $dir"
  echo "   config-schema: $CONFIG_SCHEMA"
  # Если bootstrap только что создал свежий data.db — НЕ передаём --force
  # в data-service, иначе он удалит результат bootstrap'а (PostgreSQL
  # data.db — а для SQLite materialize всё равно применит CREATE TABLE
  # IF NOT EXISTS, что безопасно поверх наполненной таблицы).
  if [ "$bootstrap_ran" = "1" ]; then
    if [ -n "$force" ]; then
      echo "   (--force передан, но bootstrap уже подготовил data.db; --force не передаём data-service)"
    fi
    force=""
  fi
  [ -n "$force" ] && echo "   force: enabled (SQLite file will be removed first)"

  (cd "$PROJECT_ROOT/data-service" && \
    CONFIG_SCHEMA="$CONFIG_SCHEMA" \
    go run ./cmd/server/ --materialize "$dir" $force)
}

# Запустить data-service с указанным сценарием (только data, без rag/mcp/api/web).
# Удобно для ad-hoc проверок одного сценария.
# Если все сервисы уже подняты и порт занят — переопредели DATA_PORT.
cmd_db_serve() {
  local name="${1:-}"
  local dir; dir=$(_resolve_scenario "$name") || exit 1
  load_env; ensure_dirs

  echo "🔨 Building data-service..."
  (cd "$PROJECT_ROOT/data-service" && \
    go build -o bin/data-service ./cmd/server/) || {
      echo "❌ Failed to build data-service"; exit 1
  }

  echo "🚀 Serving scenario '$name' on :$DATA_PORT"
  echo "   (logs: $(logfile data) if started via start; foreground here)"
  echo ""

  cd "$PROJECT_ROOT/data-service"
  exec env CONFIG_SCHEMA="$CONFIG_SCHEMA" PORT="$DATA_PORT" \
    "$PROJECT_ROOT/data-service/bin/data-service" \
    --config "$dir/config.json"
}

# Прогоняет scenario-driven Go-тесты.
#   ./scripts/dev.sh db test            — все сценарии (default)
#   ./scripts/dev.sh db test <name>     — один сценарий
cmd_db_test() {
  local name="${1:-all}"
  if [ "$name" = "all" ]; then
    echo "🧪 Running scenario tests for ALL scenarios..."
    (cd "$PROJECT_ROOT/data-service" && \
      CONFIG_SCHEMA="$CONFIG_SCHEMA" \
      go test ./internal/server/... -run TestScenario -v)
    return
  fi

  local dir; dir=$(_resolve_scenario "$name") || exit 1
  local scenario_name; scenario_name=$(basename "$dir")

  # Сопоставление имя директории → имя Go-функции.
  local func
  case "$scenario_name" in
    sqlite-testseed)   func="TestScenario_SqliteTestseed" ;;
    shop)              func="TestScenario_Shop" ;;
    big-testseed)
      # big-testseed покрывают несколько тестов: TestScenario_Big, TestEdgeCases_*,
      # TestCustomQueries_*, TestConcurrency_*, TestCrossDriver_*. Запускаем все.
      func="TestScenario_BigTestseed|TestEdgeCases|TestCustomQueries|TestConcurrency|TestCrossDriver"
      ;;
    postgres-testseed)
      echo "ℹ️  PostgreSQL-сценарий покрывается только интеграционным тестом:"
      echo "    uv run python data-service/tests/integration/test_with_faker.py"
      echo "   (нужен docker compose up -d db)"
      exit 0
      ;;
    *)
      echo "⚠️  No dedicated test for '$scenario_name'; running all TestScenario_*"
      func="TestScenario"
      ;;
  esac

  echo "🧪 Running scenario tests for: $scenario_name ($func)"
  (cd "$PROJECT_ROOT/data-service" && \
    CONFIG_SCHEMA="$CONFIG_SCHEMA" \
    go test ./internal/server/... -run "$func" -v)
}

# Удалить материализованную БД.
#   ./scripts/dev.sh db drop <name>
cmd_db_drop() {
  local name="${1:-}"
  local dir; dir=$(_resolve_scenario "$name") || exit 1
  if [ ! -f "$dir/config.json" ]; then
    echo "❌ config.json not found in $dir"; exit 1
  fi

  local driver dsn
  driver=$(jq -r '.data_source.driver' "$dir/config.json")
  dsn=$(jq    -r '.data_source.dsn'    "$dir/config.json")

  case "$driver" in
    sqlite)
      local db_path
      if [[ "$dsn" == *"/"* ]]; then db_path="$dsn"
      else                            db_path="$dir/$dsn"
      fi
      if [ ! -f "$db_path" ]; then
        echo "ℹ️  No database file at $db_path — nothing to drop"; return
      fi
      echo "🗑️  Removing: $db_path"
      rm -f "$db_path" "$db_path-wal" "$db_path-shm"
      echo "✅ SQLite database dropped"
      echo "   Recreate with: ./scripts/dev.sh db materialize $name"
      ;;
    postgres)
      echo "⚠️  PostgreSQL: drop должен делаться вручную (защита от clobber)."
      echo ""
      echo "   Сбросить только схему public:"
      echo "     docker exec agent-tutor-db-1 psql -U tutor -d agent_tutor \\"
      echo "       -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public'"
      exit 1
      ;;
    *) echo "❌ Unknown driver: $driver"; exit 1 ;;
  esac
}

cmd_db_help() {
  cat <<'EOF'
db — управление сценариями data-service (фабрика тестовых БД)

Использование:
  ./scripts/dev.sh db list                          — список сценариев с метаданными
  ./scripts/dev.sh db materialize <name> [--force]  — создать/пересоздать БД из сценария
  ./scripts/dev.sh db serve <name>                  — запустить data-service на сценарии (fg)
  ./scripts/dev.sh db test [all|<name>]             — прогнать Go-тесты на сценариях
  ./scripts/dev.sh db drop <name>                   — удалить материализованную БД

Где <name> — это:
  - встроенное имя:        sqlite-testseed, postgres-testseed, shop, big-testseed
  - относительный путь:    ./my-scenario или testdata/scenarios/my-scenario
  - абсолютный путь

Примеры:
  ./scripts/dev.sh db list
  ./scripts/dev.sh db materialize sqlite-testseed --force
  ./scripts/dev.sh db serve shop                    # только data-service на data.db
  ./scripts/dev.sh db test sqlite-testseed
  DATA_PORT=18084 ./scripts/dev.sh db serve postgres-testseed

Env:
  CONFIG_SCHEMA   JSON Schema конфига (default: $PROJECT_ROOT/specs/config.schema.json)
  DATA_PORT       порт для data-service (default: 8084)
  DATABASE_URL    PG-сценарий работает против этого URL

Готовые сценарии — data-service/testdata/scenarios/
EOF
}



# =============================================================================
# Main
# =============================================================================

case "${1:-help}" in
  start)
    shift
    cmd_start "$@"
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
  db)
    shift
    cmd_db "$@"
    ;;
  help|--help|-h)
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  start              — поднять все сервисы (data + rag + mcp + api + web)"
    echo "  stop               — погасить все"
    echo "  restart            — перезапустить"
    echo "  status             — healthcheck"
    echo "  logs [svc]         — tail -f логов (rag|mcp|api|web|data|all)"
    echo ""
    echo "Сценарии БД data-service (фабрика тестовых БД):"
    echo "  db list                — список сценариев с метаданными"
    echo "  db materialize <n>     — создать/пересоздать БД из сценария  (--force для SQLite)"
    echo "  db serve <n>           — запустить только data-service на сценарии (foreground)"
    echo "  db test [all|<n>]      — прогнать Go-тесты на сценариях"
    echo "  db drop <n>            — удалить материализованную БД сценария"
    echo "  db help                — подробная справка по db-subcommand"
    echo ""
    echo "Подробнее по db-подкомандам: $0 db help"
    echo ""
    echo "Env:"
    echo "  .env в корне проекта — автоматически подгружается"
    echo "  CONFIG_SCHEMA        — путь к JSON Schema конфига (по умолчанию specs/config.schema.json)"
    exit 0
    ;;
  *)
    echo "❌ Unknown command: $1"
    echo "Run '$0 help' for usage"
    exit 1
    ;;
esac
