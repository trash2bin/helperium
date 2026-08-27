#!/usr/bin/env bash
# Run the Helperium Docker Compose project from any current directory.
# The compose file lives in infra/, while the project .env lives at the root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_PROJECT_DIR="$PROJECT_ROOT/infra"
COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env"

# The test profile must always exercise the secure MCP and API control-plane
# contracts. Docker Compose gives process environment precedence over --env-file,
# so force test-only values here instead of inheriting local development settings.
# Callers may override values through MCP_TEST_* / API_TEST_BEARER_TOKEN, while
# api-service, gateway, and the E2E caller always receive matching credentials.
test_profile=false
previous_arg=""
for arg in "$@"; do
  if { [[ "$previous_arg" == "--profile" && "$arg" == "test" ]]; } || [[ "$arg" == "--profile=test" ]]; then
    test_profile=true
    break
  fi
  previous_arg="$arg"
done
if [[ "$test_profile" == true ]]; then
  export MCP_DEV=false
  export MCP_REQUIRE_AUTH=true
  export MCP_API_KEY="${MCP_TEST_API_KEY:-ci-mcp-token}"
  export MCP_CLIENT_API_KEY="$MCP_API_KEY"
  export MCP_ALLOWED_ORIGINS="${MCP_TEST_ALLOWED_ORIGINS:-http://localhost:8080}"
  export MCP_RATE_LIMIT_RPS="${MCP_TEST_RATE_LIMIT_RPS:-1000}"
  export MCP_RATE_LIMIT_BURST="${MCP_TEST_RATE_LIMIT_BURST:-1000}"
  export API_BEARER_TOKEN="${API_TEST_BEARER_TOKEN:-ci-api-control-token}"
fi

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
