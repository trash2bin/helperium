#!/usr/bin/env bash
# =============================================================================
# check-admin-contract.sh — синхронизация фронта админки с Go-роутами
# -----------------------------------------------------------------------------
# Сравнивает (после нормализации path-параметров → *):
#   • эндпоинты admin-dashboard/internal/server/{server,abuse}.go  (chi router)
#   • эндпоинты admin-dashboard/internal/server/static/app.js     (все вызовы api())
#
# Вердикты:
#   FAIL  — фронт вызывает эндпоинт, которого нет в Go рутере  (кнопка мёртвая)
#   WARN  — Go декларирует эндпоинт, не вызываемый фронтом       (часто ок)
#
# Запуск: ./scripts/check-admin-contract.sh
#         ./scripts/check-admin-contract.sh --update-readme
#         ./scripts/check-admin-contract.sh --dump
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/admin-dashboard/internal/server"
APP="$SERVER_DIR/static/app.js"
SRC_DIR="$ROOT/admin-dashboard/src"
EXTRACTOR="$ROOT/scripts/extract-frontend-endpoints.js"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

GO_RAW="$TMP/go_raw.txt"
GO_NORM="$TMP/go_norm.txt"
JS_RAW="$TMP/js_raw.txt"
JS_NORM="$TMP/js_norm.txt"

# ---------------------------------------------------------------------------
# 1. Go роуты — chi r.Route("/api", func(r){...}) с жадным балансом { }
# ---------------------------------------------------------------------------
perl -0777 -ne '
  while (/\.Route\(\s*"\/api"\s*,\s*func\b[^{]*\{/sg) {
    my $start=pos(); my $depth=1; my $i=$start;
    while($depth>0 && $i<length($_)){
      my $c=substr($_,$i,1); $depth++ if $c eq "{"; $depth-- if $c eq "}"; $i++;
    }
    my $body=substr($_,$start,$i-$start);
    while($body=~ /\.(Get|Post|Put|Delete)\(\s*["`\x27]([^"`\x27]+)["`\x27]/g) {
      print uc($1), " /api$2\n";
    }
  }
' "$SERVER_DIR/server.go" "$SERVER_DIR/abuse.go" > "$GO_RAW" 2>/dev/null

[ -s "$GO_RAW" ] || { echo "❌ Go роуты не извлечены" >&2; exit 2; }

norm() { sed -E 's#\{[^}]+\}#*#g; s#\$\{[^}]+\}#*#g; s#\(#(#g'; }

sort -u "$GO_RAW" | norm | sort -u > "$GO_NORM"

# ---------------------------------------------------------------------------
# 2. Фронтовые вызовы — через JS-парсер extract-frontend-endpoints.js
#    Сканирует app.js + все js/domains/*.js
# ---------------------------------------------------------------------------
if [ ! -f "$EXTRACTOR" ]; then
  echo "❌ Скрипт $EXTRACTOR не найден" >&2
  exit 2
fi

DOMAINS_DIR="$SRC_DIR/domains"
JS_FILES=()
if [ -f "$APP" ]; then
  JS_FILES+=("$APP")
fi
if [ -d "$DOMAINS_DIR" ]; then
  for f in "$DOMAINS_DIR"/*.ts; do
    [ -f "$f" ] && JS_FILES+=("$f")
  done
fi

node "$EXTRACTOR" "${JS_FILES[@]}" > "$JS_RAW" 2>/dev/null
[ -s "$JS_RAW" ] || { echo "⚠️  фронтовые вызовы не извлечены" >&2; }

norm < "$JS_RAW" | sort -u > "$JS_NORM"

# ---------------------------------------------------------------------------
# 3. Diff
# ---------------------------------------------------------------------------
USED_ONLY=$(comm -23 "$JS_NORM" "$GO_NORM" | grep -v '^$' || true)
DECLARED_ONLY=$(comm -13 "$JS_NORM" "$GO_NORM" | grep -v '^$' || true)
MATCH=$(comm -12 "$JS_NORM" "$GO_NORM" | grep -v '^$' || true)

GO_N=$(wc -l < "$GO_NORM" 2>/dev/null | tr -d ' ')
JS_N=$(wc -l < "$JS_NORM" 2>/dev/null | tr -d ' ')
MATCH_N=$(printf '%s' "$MATCH" | grep -c . 2>/dev/null || :)
USED_N=$(printf '%s' "$USED_ONLY" | grep -c . 2>/dev/null || :)
DECL_N=$(printf '%s' "$DECLARED_ONLY" | grep -c . 2>/dev/null || :)

# Приводим к числам
MATCH_N=$(( MATCH_N + 0 ))
USED_N=$(( USED_N + 0 ))
DECL_N=$(( DECL_N + 0 ))

echo "──────────────────────────────────────────────────────────────"
echo "📦 Admin Dashboard Contract Check"
echo "──────────────────────────────────────────────────────────────"
printf "  Go роутов:             %s\n" "$GO_N"
printf "  Frontend запросов:     %s\n" "$JS_N"
printf "  Match:                 %s\n" "$MATCH_N"
printf "  ❌ Только фронт:       %s   (кнопка шлёт туда, где нет хендлера)\n" "$USED_N"
printf "  ⚠️  Только Go:         %s   (хендлер есть, но UI не вызывает)\n" "$DECL_N"
echo "──────────────────────────────────────────────────────────────"

if [ "$USED_N" -gt 0 ]; then
  echo ""
  echo "❌ КРИТИЧНО — фронт шлёт туда, где нет Go хендлера:"
  echo "$USED_ONLY" | sed 's/^/    /'
  echo ""
  echo "   → Вот почему 'нажимаю сохранить и нихуя'."
  echo "     Почини: либо добавь route в server.go, либо правь URL в app.js."
fi

if [ "$DECL_N" -gt 0 ]; then
  echo ""
  echo "⚠️  Go отдаёт, но UI не использует:"
  echo "$DECLARED_ONLY" | sed 's/^/    /'
  echo ""
  echo "   health/metrics — ок. Остальное: либо UI-фича удалена, либо забыли вызов."
fi

# ---------------------------------------------------------------------------
# 4. опции
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--dump" ]; then
  echo ""
  echo "=== GO RAW ==="; cat "$GO_RAW"
  echo ""; echo "=== JS RAW ==="; cat "$JS_RAW"
  echo "=== GO NORM ==="; cat "$GO_NORM"
  echo "=== JS NORM ==="; cat "$JS_NORM"
fi

if [ "${1:-}" = "--update-readme" ]; then
  echo ""
  echo "=== README fragment (autogen из server.go — $(date +%Y-%m-%d)) ==="
  echo ""
  echo "### API эндпоинты (автосгенерировано, не редактируй вручную)"
  echo ""
  echo "> Эта секция генерируется \`scripts/check-admin-contract.sh --update-readme\`."
  echo "> Все эндпоинты защищены \`Authorization: Bearer <ADMIN_TOKEN>\` (кроме \`/api/health\`)."
  echo ""
  echo "| Method | Path |"
  echo "|---|---|"
  sort -k2 "$GO_RAW" | awk '{print "| "$1" | `"$2"` |"}'
fi

echo ""
if [ "$USED_N" -gt 0 ]; then
  echo "🚪 FAILED"
  exit 1
else
  echo "✅ PASSED"
  exit 0
fi
