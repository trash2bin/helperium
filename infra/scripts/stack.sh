#!/usr/bin/env bash
# =============================================================================
# stack.sh — поднять/проверить весь Helperium стек: сервисы + инфраструктура
# =============================================================================
# Usage:
#   ./scripts/stack.sh up          — поднять всё (dev.sh start + Docker monitoring)
#   ./scripts/stack.sh down        — погасить всё
#   ./scripts/stack.sh status      — проверить все сервисы
#   ./scripts/stack.sh logs        — логи Docker стеков (последние 20 строк)
#   ./scripts/stack.sh check       — healthcheck + трейсы в Tempo
# =============================================================================
#set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

DOCKER_COMPOSE="docker compose"
if ! docker compose version &>/dev/null; then
  DOCKER_COMPOSE="docker-compose"
fi

check_docker() {
  if ! docker ps &>/dev/null; then
    echo -e "${RED}Docker daemon not running. Run: colima start${NC}"
    return 1
  fi
}

# ─────── up ───────
up() {
  echo -e "${YELLOW}[1/2] Поднимаю нативные сервисы (dev.sh start)...${NC}"
  "$PROJECT_ROOT/scripts/dev.sh" start || {
    echo -e "${RED}dev.sh start failed${NC}"
    exit 1
  }

  echo -e "${YELLOW}[2/2] Поднимаю Docker инфраструктуру (monitoring + tracing + logging)...${NC}"
  check_docker || exit 1
  cd "$PROJECT_ROOT"
  $DOCKER_COMPOSE up -d prometheus grafana tempo otel-collector loki promtail

  echo -e "${GREEN}✓ Стек поднят. Проверка: ./scripts/stack.sh status${NC}"
}

# ─────── down ───────
down() {
  echo -e "${YELLOW}Останавливаю нативные сервисы...${NC}"
  "$PROJECT_ROOT/scripts/dev.sh" stop || true

  echo -e "${YELLOW}Останавливаю Docker стек...${NC}"
  check_docker || true
  cd "$PROJECT_ROOT"
  $DOCKER_COMPOSE down --volumes 2>/dev/null || $DOCKER_COMPOSE down 2>/dev/null || true

  echo -e "${GREEN}✓ Всё остановлено${NC}"
}

# ─────── status ───────
status() {
  echo -e "${YELLOW}══════ Нативные сервисы ══════${NC}"
  local svcs=("8080:web" "8081:api" "8082:rag" "8083:mcp" "8084:data" "8085:admin")
  local ok=0; local fail=0
  for pair in "${svcs[@]}"; do
    port="${pair%%:*}"
    name="${pair##*:}"
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${port}/health" --max-time 2 2>/dev/null | grep -q 200 2>/dev/null; then
      echo -e "  ${GREEN}✓${NC} ${name} (:${port})"
      ((ok++))
    else
      echo -e "  ${RED}✗${NC} ${name} (:${port})"
      ((fail++))
    fi
  done

  echo -e "\n${YELLOW}══════ Docker инфраструктура ══════${NC}"
  if docker ps &>/dev/null; then
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -E "prometheus|grafana|tempo|otel|loki|promtail" || true
    local containers
    containers=$(docker ps --format "{{.Names}}" 2>/dev/null | grep -cE "prometheus|grafana|tempo|otel|loki|promtail" || true)
    echo -e "\n  Docker контейнеров: ${GREEN}${containers}${NC}"
    ((ok+=containers))
  else
    echo -e "  ${RED}Docker не запущен${NC}"
    ((fail++))
  fi

  echo -e "\n${GREEN}✓${NC} Working: ${ok} | ${RED}✗${NC} Failed: ${fail}"
}

# ─────── logs ───────
logs() {
  check_docker 2>/dev/null || { echo "Docker не запущен"; exit 1; }
  echo -e "${YELLOW}══════ Docker логи (последние 20 строк) ══════${NC}"
  for svc in prometheus grafana tempo otel-collector loki promtail; do
    echo -e "\n${YELLOW}--- $svc ---${NC}"
    $DOCKER_COMPOSE logs --tail=5 "$svc" 2>/dev/null | grep -v "^$" | tail -5 || echo "(нет логов)"
  done
}

# ─────── check ───────
check() {
  status
  echo -e "\n${YELLOW}══════ Tempo трейсы ══════${NC}"
  local traces
  traces=$(curl -s "http://127.0.0.1:3200/api/search?q=%7B%7D&limit=50" 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    svcs={}
    for t in d.get('traces',[]):
        svcs[t.get('rootServiceName','?')] = svcs.get(t.get('rootServiceName','?'),0)+1
    for s,c in sorted(svcs.items(),key=lambda x:-x[1]):
        print(f'{c:3d}x {s}')
    print(f'Total: {len(d.get(\"traces\",[]))}')
except: print('(Tempo не отвечает)')
" 2>/dev/null) || echo "(Tempo не отвечает)"
  echo "$traces"

  echo -e "\n${YELLOW}══════ Prometheus цели ══════${NC}"
  curl -s "http://127.0.0.1:9090/api/v1/targets" 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    for t in d.get('data',{}).get('activeTargets',[]):
        health = '✓' if t['health']=='up' else '✗'
        print(f'  {health} {t[\"labels\"].get(\"job\",\"?\")}')
except: print('(Prometheus не отвечает)')
" 2>/dev/null
}

# ─────── main ───────
case "${1:-help}" in
  up)      up ;;
  down)    down ;;
  status)  status ;;
  logs)    logs ;;
  check)   check ;;
  help|*)
    echo "Usage: $0 {up|down|status|logs|check}"
    grep "^# " "$0" | head -6
    ;;
esac
