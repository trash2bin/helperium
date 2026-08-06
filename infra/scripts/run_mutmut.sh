#!/bin/bash
# run_mutmut.sh — Run Python mutation testing with mutmut on Helperium
# Usage:
#   ./scripts/run_mutmut.sh               # auto-detect
#   ./scripts/run_mutmut.sh --prep        # prepare flat src/ (for Docker build)
#   ./scripts/run_mutmut.sh --build       # build Docker image only
#   ./scripts/run_mutmut.sh --docker      # run in Docker (Linux, fork-safe)
#   ./scripts/run_mutmut.sh --native      # run locally (macOS, fork-crash-prone)
#   ./scripts/run_mutmut.sh --go          # Go mutation tests (go-mutesting)
#   ./scripts/run_mutmut.sh --clean       # remove src/ mutmut.sqlite mutants/
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="$PWD"
UNAME="$(uname -s)"
MODE="${1:-auto}"

# ── helpers ────────────────────────────────────────────────
info()  { echo -e "\033[36m==>\033[0m $*"; }
ok()    { echo -e "\033[32m OK\033[0m $*"; }
die()   { echo -e "\033[31mFAIL\033[0m $*" >&2; exit 1; }

# ── Prepare flat src/ for Docker build ─────────────────────
prep_src() {
    rm -rf src 2>/dev/null
    mkdir -p src

    info "Building flat src/..."
    rsync -a api-service/src/api_service/ src/api_service/ --exclude='__pycache__'
    rsync -a demo/ src/demo/ --exclude='__pycache__'
    rsync -a helperium-sdk/src/helperium_sdk/ src/helperium_sdk/ --exclude='__pycache__'
    rsync -a agent-db/agent_db/ src/agent_db/ --exclude='__pycache__'
    rsync -a api-service/src/api_service/tests/ src/api_service/tests/ --exclude='__pycache__'

    local count
    count=$(find src -name '*.py' | wc -l)
    echo "  $count Python files"

    local test_count
    test_count=$(find src/api_service/tests -name 'test_*.py' | wc -l)
    echo "  $test_count test files"

    ok "src/ ready for Docker build"
}

# ── Go mutation tests ──────────────────────────────────────
run_go() {
    info "Go mutation testing (go-mutesting)..."
    GOMUTEST="${GOMUTEST:-$HOME/go/bin/go-mutesting}"
    if ! command -v "$GOMUTEST" &>/dev/null; then
        info "Installing go-mutesting (Avito fork)..."
        go install github.com/avito-tech/go-mutesting/cmd/go-mutesting@latest
        GOMUTEST="$HOME/go/bin/go-mutesting"
    fi

    for dir in data-service mcp-gateway; do
        if [ -d "$dir" ]; then
            info "  $dir..."
            cd "$PROJECT/$dir"
            $GOMUTEST ./internal/... 2>&1 || true
            cd "$PROJECT"
        fi
    done
    ok "Go mutation tests complete"
}

# ── Docker mode (Linux, fork-safe) ─────────────────────────
run_docker() {
    local IMAGE="helperium-mutmut:latest"

    if [ ! -d src ] || [ ! -f src/api_service/__init__.py ]; then
        info "src/ not found. Running --prep first..."
        prep_src
    fi

    info "Building Docker image..."
    docker build -t "$IMAGE" -f Dockerfile.mutmut .

    echo ""
    info "Running mutation tests in Docker (Linux, fork-safe)..."
    echo "  This will take ~30-40 minutes for 12K mutants."
    echo "  Progress: docker logs -f helperium-mutmut-run"
    echo ""

    docker run --rm --name helperium-mutmut-run \
        helperium-mutmut
}

# ── Native mode (macOS, fork-crash-prone) ─────────────────
run_native() {
    if [ "$UNAME" = "Darwin" ]; then
        echo "╔══════════════════════════════════════════════╗"
        echo "║  ⚠️  macOS: fork() + pytest_asyncio          ║"
        echo "║  typically crashes around 99% of mutants.    ║"
        echo "║  Use --docker for reliable results.          ║"
        echo "╚══════════════════════════════════════════════╝"
        echo ""
    fi

    local MUTMUT="$PROJECT/.venv/bin/python -m mutmut"
    local PTH_DIR=".venv/lib/python3.13/site-packages"
    local PTH_PKGS=(_editable_impl_api_service _editable_impl_rag _editable_impl_demo_web \
                    _editable_impl_helperium_sdk _editable_impl_agent_db)

    local CPUS
    CPUS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)
    [[ "$UNAME" = "Darwin" ]] && CPUS=1

    cleanup() {
        echo ""
        info "Cleanup..."
        for p in "${PTH_PKGS[@]}"; do
            [ -f "$PTH_DIR/${p}.pth.bak" ] && mv "$PTH_DIR/${p}.pth.bak" "$PTH_DIR/${p}.pth"
        done
        rm -rf "$PROJECT/src" 2>/dev/null
    }
    trap cleanup EXIT

    # ── 1. BAK .pth ───────────────────────────────────────
    info "Backing up .pth files..."
    for p in "${PTH_PKGS[@]}"; do
        [ -f "$PTH_DIR/${p}.pth" ] && mv "$PTH_DIR/${p}.pth" "$PTH_DIR/${p}.pth.bak"
    done

    # ── 2. Flat src/ ──────────────────────────────────────
    rm -rf src mutmut.sqlite mutants 2>/dev/null
    prep_src

    # ── 3. Run ────────────────────────────────────────────
    info "Running mutmut (max-children $CPUS)..."
    $MUTMUT run --max-children "$CPUS"
    echo "  mutmut exit code: $?"

    # ── 4. Results ────────────────────────────────────────
    echo ""
    info "Results..."
    if [ -f mutmut.sqlite ]; then
        .venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('mutmut.sqlite')
total = conn.execute('SELECT COUNT(*) FROM mutation').fetchone()[0]
by_outcome = conn.execute('SELECT outcome, COUNT(*) FROM mutation GROUP BY outcome ORDER BY outcome').fetchall()
print(f'Total mutants: {total}')
for o, c in by_outcome:
    print(f'  {o}: {c}')
k = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%KILLED%'\").fetchone()[0]
s = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%SURVIVED%'\").fetchone()[0]
t = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%TIMEOUT%'\").fetchone()[0]
sk = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%SUSPICIOUS%'\").fetchone()[0]
tested = k + s + t + sk
print(f'Score: {k/tested*100:.1f}% ({k}/{tested}) of tested; {total} total')
" 2>&1 || echo "  No mutation table — crashed before writing results"
    else
        echo "  mutmut.sqlite not found — process crashed (macOS fork issue?)"
    fi
    ok "Native run complete"
}

# ── main ───────────────────────────────────────────────────
case "$MODE" in
    --prep|-p)
        prep_src
        ;;
    --build|-b)
        if [ ! -d src ] || [ ! -f src/api_service/__init__.py ]; then
            prep_src
        fi
        bash scripts/build_mutmut_docker.sh
        ;;
    --docker|-d)
        run_docker
        ;;
    --native|-n)
        run_native
        ;;
    --go|-g)
        run_go
        ;;
    --clean|-c)
        rm -rf src mutmut.sqlite mutants 2>/dev/null
        ok "Cleaned"
        ;;
    auto)
        echo "Usage: $0 [option]"
        echo ""
        echo "  --prep    prepare flat src/ for Docker build"
        echo "  --build   build Docker image"
        echo "  --docker  run mutation tests in Docker (Linux, reliable)"
        echo "  --native  run locally (macOS fork-crash-prone)"
        echo "  --go      Go mutation tests"
        echo "  --clean   remove artifacts"
        echo ""
        echo "╔══════════════════════════════════════════════╗"
        echo "║  For clean results, use:                     ║"
        echo "║    ./scripts/run_mutmut.sh --docker           ║"
        echo "╚══════════════════════════════════════════════╝"
        ;;
    *)
        echo "Unknown mode: $MODE"
        exit 1
        ;;
esac
