#!/usr/bin/env bash
# bootstrap.sh — генерация data.db для сценария 'shop' (нет seed.json).
#
# Запускается из scripts/dev.sh (db materialize shop) и agent-db CLI, когда
# в сценарии нет seed.json, а data.db отсутствует (или --force).
# Стратегия: утилита create_shop_db.py из testdata/scripts/, путь к итоговой
# БД передаётся через SHOP_DB. Подробности — data-service/README.md
# § "Сценарии — фабрика тестовых БД".

set -euo pipefail

# PWD = директория сценария (data-service/testdata/scenarios/shop)
S_DIR="$(cd "$(dirname "$0")" && pwd)"
DS_DIR="${DATA_SERVICE_DIR:-$(cd "$S_DIR/../../.." && pwd)}"
T_DB="${SHOP_DB:-$S_DIR/data.db}"

GENERATOR="$DS_DIR/testdata/scripts/create_shop_db.py"

if [ ! -f "$GENERATOR" ]; then
  echo "❌ bootstrap.sh: генератор не найден: $GENERATOR" >&2
  exit 1
fi

echo "  generating shop database → $T_DB"
P_ROOT="$(cd "$DS_DIR/.." && pwd)"
SHOP_DB="$T_DB" uv run --project "$P_ROOT" -- python "$GENERATOR"

# Финальная проверка: data.db должен существовать
if [ ! -f "$T_DB" ]; then
  echo "❌ bootstrap.sh: $T_DB не создан" >&2
  exit 1
fi

echo "  ✅ $T_DB создан"
