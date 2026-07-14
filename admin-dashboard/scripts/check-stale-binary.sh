#!/bin/bash
# Pre-commit hook: check that admin-dashboard binary is not stale
BIN=admin-dashboard/bin/admin-dashboard
SRC=admin-dashboard/internal/server/static/app.js

if [ ! -f "$BIN" ]; then
  echo "ERROR: admin-dashboard binary not found. Run:"
  echo "  cd admin-dashboard && go build -o bin/admin-dashboard ./cmd/server/"
  exit 1
fi

if [ "$SRC" -nt "$BIN" ]; then
  echo "ERROR: admin-dashboard binary is stale - static files changed. Run:"
  echo "  cd admin-dashboard && go build -o bin/admin-dashboard ./cmd/server/"
  exit 1
fi
