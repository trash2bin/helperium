#!/bin/bash
# Mutation Testing — Helperium Nightly Run
# Python (mutmut) + Go (go-mutesting Avito fork)
# Запуск: nohup bash .nightly_mutmut.sh > .data/reports/nightly_stdout.log 2>&1 &

set -o pipefail
cd "$(dirname "$0")" || exit 1
mkdir -p .data/reports

REPORT=".data/reports/mutation_report_$(date +%Y%m%d_%H%M).txt"

echo "============================================" | tee "$REPORT"
echo " Mutation Testing — Helperium Nightly Run" | tee -a "$REPORT"
echo " Started: $(date)" | tee -a "$REPORT"
echo " Timeout: 7h total" | tee -a "$REPORT"
echo " Host: $(hostname), Free disk: $(df -h / | awk 'NR==2{print $4}')" | tee -a "$REPORT"
echo "============================================" | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

CLEANUP_DIRS="mutants mutmut.sqlite"

# ── 1. Go: data-service/configgen ────────────────────────
echo "━━━ [1/3] Go: data-service/configgen" | tee -a "$REPORT"
cd data-service
~/go/bin/go-mutesting ./internal/configgen/... 2>&1 | tee -a "$REPORT"
cd ..
echo "" | tee -a "$REPORT"

# ── 2. Go: data-service/runtime ──────────────────────────
echo "━━━ [2/3] Go: data-service/runtime" | tee -a "$REPORT"
cd data-service
~/go/bin/go-mutesting ./internal/runtime/... 2>&1 | tee -a "$REPORT"
cd ..
echo "" | tee -a "$REPORT"

# ── 3. Go: mcp-gateway ────────────────────────────────────
echo "━━━ [3/3] Go: mcp-gateway" | tee -a "$REPORT"
cd mcp-gateway
~/go/bin/go-mutesting ./... 2>&1 | tee -a "$REPORT"
cd ..
echo "" | tee -a "$REPORT"

# ── Python (if mutmut compatible with install) ────────────
# Note: editable installs (uv) make mutmut skip mutants
# because imports go to originals, not mutants/ dir
# For now, Python is covered by `make ci-test-py`
echo "" | tee -a "$REPORT"
echo "━━━ [4/3] Python test suite (baseline coverage)" | tee -a "$REPORT"
uv run pytest api-service/src/api_service/tests demo/web/tests rag/tests/unit \
  --ignore=tests/e2e --ignore=rag/tests/integration \
  -q --tb=short --timeout=30 2>&1 | tail -5 | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

# ── Summary ──────────────────────────────────────────────
echo "============================================" | tee -a "$REPORT"
echo " FINISHED: $(date)" | tee -a "$REPORT"
echo " Report: $REPORT" | tee -a "$REPORT"
echo "============================================" | tee -a "$REPORT"
