#!/bin/bash
# build_mutmut_docker.sh — build helperium-mutmut image in isolated context
# (avoids **/tests/ exclusion in root .dockerignore)
set -euo pipefail

cd "$(dirname "$0")/.."

rm -rf mutmut-build 2>/dev/null
mkdir -p mutmut-build

echo "==> Preparing build context..."

# ── 1. Copy all workspace member dirs ─────────────────────
for dir in api-service helperium-sdk agent-db embed demo specs; do
    [ -d "$dir" ] && cp -r "$dir" mutmut-build/
done

# rag/ deleted — create minimal stub with NO deps (avoids torch/scipy/chromadb)
mkdir -p mutmut-build/rag
cat > mutmut-build/rag/pyproject.toml << 'EOF'
[project]
name = "rag"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = []
EOF

# pyproject.toml (patched). uv.lock — empty, rag stub won't match
cp pyproject.toml mutmut-build/
touch mutmut-build/uv.lock
# Patch pyproject.toml: remove rag[cli], rag[pg] from dev deps
sed -i '' -e '/"rag\[cli"/d' -e '/"rag\[pg"/d' mutmut-build/pyproject.toml

# Dockerfile + entrypoint
cp Dockerfile.mutmut mutmut-build/
cp scripts/run_mutmut_docker.sh mutmut-build/

# ── 2. .dockerignore (NO **/tests/!) ──────────────────────
cat > mutmut-build/.dockerignore << 'EOF'
.venv/
.git/
__pycache__/
*.pyc
.data/
*.db
*.sqlite*
.env*
.pytest_cache/
.ruff_cache/
.idea/
.DS_Store
EOF

# ── 3. Verify tests ────────────────────────────────────────
echo "   Test files: $(find mutmut-build -name 'test_*.py' -not -path '*/__pycache__/*' | wc -l)"

# ── 4. Build ───────────────────────────────────────────────
echo "==> Building helperium-mutmut:latest..."
docker build -t helperium-mutmut:latest -f Dockerfile.mutmut mutmut-build/

rm -rf mutmut-build
echo "==> Done"
