#!/usr/bin/env bash
set -euo pipefail

readonly out_dir="/tmp/helperium-api-security-probe"
readonly admin_token="ci-admin-token"
readonly viewer_token="ci-viewer-token"
mkdir -p "$out_dir"

probe() {
  local name="$1"
  shift
  local headers="$out_dir/${name}.headers"
  local body="$out_dir/${name}.body"
  local status
  status=$(curl -sS --max-time 10 -X "$1" "$2" -D "$headers" -o "$body" -w '%{http_code}' "${@:3}")
  printf '%-34s %s\n' "$name" "$status"
  printf '%s\n' "$status" >"$out_dir/${name}.status"
}

printf '%s\n' '== Public health and API admin barriers =='
probe data_health GET http://127.0.0.1:8084/health
probe api_health GET http://127.0.0.1:8081/health
probe admin_health GET http://127.0.0.1:8085/health
probe data_admin_missing POST http://127.0.0.1:8084/admin/tenants -H 'Content-Type: application/json' --data '{}'
probe data_admin_malformed_auth POST http://127.0.0.1:8084/admin/tenants -H 'Authorization: Token ci-admin-token' -H 'Content-Type: application/json' --data '{}'
probe data_admin_wrong_token POST http://127.0.0.1:8084/admin/tenants -H 'Authorization: Bearer local-security-probe-wrong' -H 'Content-Type: application/json' --data '{}'
probe data_admin_viewer_token POST http://127.0.0.1:8084/admin/tenants -H "Authorization: Bearer $viewer_token" -H 'Content-Type: application/json' --data '{}'
probe admin_dashboard_missing GET http://127.0.0.1:8085/api/dashboard
probe admin_dashboard_wrong_token GET http://127.0.0.1:8085/api/dashboard -H 'Authorization: Bearer local-security-probe-wrong'
probe admin_dashboard_viewer_get GET http://127.0.0.1:8085/api/dashboard -H "Authorization: Bearer $viewer_token"
probe admin_dashboard_admin_get GET http://127.0.0.1:8085/api/dashboard -H "Authorization: Bearer $admin_token"
probe admin_viewer_write_denied POST http://127.0.0.1:8085/api/tenants -H "Authorization: Bearer $viewer_token" -H 'Content-Type: application/json' --data '{}'

printf '%s\n' '== Browser-origin handling =='
probe api_cors_disallowed OPTIONS http://127.0.0.1:8081/api/chat -H 'Origin: https://attacker.invalid' -H 'Access-Control-Request-Method: POST'
probe api_cors_local OPTIONS http://127.0.0.1:8081/api/chat -H 'Origin: http://localhost:8080' -H 'Access-Control-Request-Method: POST'

printf '%s\n' '== Safe response scan =='
if grep -RIEq 'ci-admin-token|ci-viewer-token|helperium-e2e-mcp-token|/workspace|Traceback' "$out_dir"/*.body; then
  echo 'SENSITIVE_MARKER_FOUND'
  exit 2
fi
echo 'No test-token, workspace-path, or traceback marker in captured API bodies.'
