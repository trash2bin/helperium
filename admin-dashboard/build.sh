#!/usr/bin/env bash
# Build: typecheck → esbuild → internal/server/static/dist/app.js
set -euo pipefail

cd "$(dirname "$0")"

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
  partials/app-close.html \
  partials/modals.html \
  partials/tail.html \
  > internal/server/static/index.html
echo "  index.html  $(wc -c < internal/server/static/index.html) bytes"

echo "=== Lint HTML: html-validate index.html ==="
npx html-validate internal/server/static/index.html

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
