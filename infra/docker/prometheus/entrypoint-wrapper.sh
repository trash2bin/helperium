#!/bin/sh
# Writes the bearer token for scraping api-service /metrics into a
# credentials file, then hands off to the official Prometheus entrypoint.
# The token never appears in the compose file, config or repo.
set -eu

if [ -n "${API_BEARER_TOKEN:-}" ]; then
  printf '%s' "$API_BEARER_TOKEN" > /etc/prometheus/api_bearer_token
  chmod 600 /etc/prometheus/api_bearer_token
fi

exec /bin/prometheus "$@"
