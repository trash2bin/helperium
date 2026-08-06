#!/usr/bin/env bash
# Build script: concatenate CSS → bundle TS → output dist/
set -euo pipefail

cd "$(dirname "$0")"

echo "=== CSS: concatenate component files ==="
cat css/variables.css css/root.css css/trigger.css css/panel.css css/header.css \
    css/messages.css css/form.css css/tools.css css/animations.css \
    css/responsive.css > src/_bundle.css
echo "  src/_bundle.css  $(wc -c < src/_bundle.css) bytes"

echo "=== TypeScript: typecheck ==="
npx tsc --noEmit

echo "=== Bundle: esbuild → dist/embed.js ==="
npx esbuild src/index.ts \
  --bundle \
  --format=iife \
  --target=es2018 \
  --loader:.css=text \
  --loader:.svg=text \
  --minify \
  --sourcemap \
  --outfile=dist/embed.js

cp src/_bundle.css dist/embed.css
rm -f src/_bundle.css
echo "  dist/embed.js   $(wc -c < dist/embed.js) bytes"
echo "  dist/embed.css  $(wc -c < dist/embed.css) bytes"
echo "=== Done ==="
