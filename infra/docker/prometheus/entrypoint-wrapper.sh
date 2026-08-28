#!/bin/sh
# Writes the bearer token for scraping api-service /metrics into a
# credentials file, then hands off to the official Prometheus entrypoint.
# The token never appears in the compose file, config or repo.
set -eu

# /metrics on api-service is bearer-protected and fails closed: without a
# token the endpoint answers 503 and every api-service scrape fails. Refuse
# to start monitoring with a silently dead api-service target.
if [ -z "${API_BEARER_TOKEN:-}" ]; then
  echo "FATAL: API_BEARER_TOKEN must be set for the monitoring profile: api-service /metrics requires bearer credentials." >&2
  exit 1
fi

printf '%s' "$API_BEARER_TOKEN" > /etc/prometheus/api_bearer_token
chmod 600 /etc/prometheus/api_bearer_token

exec /bin/prometheus "$@"
