#!/usr/bin/env bash
# Run the Helperium Docker Compose project from any current directory.
# The compose file lives in infra/, while the project .env lives at the root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_PROJECT_DIR="$PROJECT_ROOT/infra"
COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  COMPOSE=(docker-compose)
fi

ARGS=(
  --project-directory "$COMPOSE_PROJECT_DIR"
  --file "$COMPOSE_FILE"
)
if [[ -f "$ENV_FILE" ]]; then
  ARGS+=(--env-file "$ENV_FILE")
fi

exec "${COMPOSE[@]}" "${ARGS[@]}" "$@"
