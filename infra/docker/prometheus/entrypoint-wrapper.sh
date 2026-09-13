#!/bin/sh
# Writes the bearer tokens for scraping bearer-protected /metrics endpoints
# into credentials files, then hands off to the official Prometheus entrypoint.
# Tokens never appear in the compose file, config or repo.
set -eu

# Every service /metrics is bearer-protected and fails closed (pentest M1):
# without a token the endpoint answers 401/403 and every scrape of that target
# fails. Refuse to start monitoring with silently dead targets.
write_token() {
  # $1 = env var name (for error messages), $2 = credentials file path, $3 = value
  name="$1"
  value="${3:-}"
  if [ -z "$value" ]; then
    echo "FATAL: $name must be set for the monitoring profile: bearer-protected /metrics requires credentials." >&2
    exit 1
  fi
  printf '%s' "$value" > "$2"
  chmod 600 "$2"
}

write_token API_BEARER_TOKEN /etc/prometheus/api_bearer_token "${API_BEARER_TOKEN:-}"
write_token ADMIN_TOKEN /etc/prometheus/admin_token "${ADMIN_TOKEN:-}"
write_token MCP_API_KEY /etc/prometheus/mcp_api_key "${MCP_API_KEY:-}"
write_token ADMIN_API_TOKEN /etc/prometheus/admin_api_token "${ADMIN_API_TOKEN:-}"

exec /bin/prometheus "$@"
