#!/usr/bin/env bash
# Build: typecheck → esbuild → internal/server/static/dist/app.js
set -euo pipefail

cd "$(dirname "$0")"

# html-validate 11.x uses fs.globSync, which exists only since Node 22; on Node 20
# the CLI dies with a cryptic "TypeError: fs.globSync is not a function". Fail with
# the requirement instead. CI pins Node 22 for the same reason.
node_major=$(node -p "process.versions.node.split('.')[0]")
if [ "$node_major" -lt 22 ]; then
  echo "Node >= 22 required, found $(node -v) (html-validate needs fs.globSync)" >&2
  exit 1
fi

mkdir -p internal/server/static/dist

echo "=== npm install (if needed) ==="
if [ ! -d node_modules ]; then
  npm install
fi

echo "=== TypeScript: typecheck ==="
npx tsc --noEmit

echo "=== Assemble HTML: partials → internal/server/static/index.html ==="
cat \
  partials/head.html \
  partials/login.html \
  partials/app-open.html \
  partials/pages/dashboard.html \
  partials/pages/tenants.html \
  partials/pages/config.html \
  partials/pages/tools.html \
  partials/pages/rag.html \
  partials/pages/agents.html \
  partials/pages/abuse.html \
  partials/pages/voice.html \
  partials/pages/llm.html \
  partials/pages/audit.html \
  partials/pages/reports.html \
  partials/app-close.html \
  partials/modals.html \
  partials/tail.html \
  > internal/server/static/index.html
echo "  index.html  $(wc -c < internal/server/static/index.html) bytes"

echo "=== Lint HTML: html-validate index.html ==="
npx html-validate internal/server/static/index.html

echo "=== Generate: openapi.json (Go) ==="
# Generate runtime + build-time OpenAPI spec
mkdir -p internal/server/static
go run ./cmd/gen-openapi/
echo "  openapi.json  $(wc -c < internal/server/static/openapi.json) bytes"

echo "=== Bundle: esbuild → internal/server/static/dist/app.js ==="
npx esbuild src/index.ts \
  --bundle \
  --format=iife \
  --target=es2018 \
  --minify \
  --sourcemap \
  --outfile=internal/server/static/dist/app.js

echo "  dist/app.js  $(wc -c < internal/server/static/dist/app.js) bytes"
echo "=== Done ==="
