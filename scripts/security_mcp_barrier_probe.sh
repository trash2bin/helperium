#!/usr/bin/env bash
set -euo pipefail

readonly out_dir="/tmp/helperium-mcp-security-probe"
readonly gateway="http://127.0.0.1:8083"
readonly token="helperium-e2e-mcp-token"
mkdir -p "$out_dir"

readonly initialize='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"local-security-probe","version":"1"}}}'

probe() {
  local name="$1"
  shift
  local headers="$out_dir/${name}.headers"
  local body="$out_dir/${name}.body"
  local status
  status=$(curl -sS --max-time 10 -X "$1" "$2" -D "$headers" -o "$body" -w '%{http_code}' "${@:3}")
  printf '%-40s %s\n' "$name" "$status"
  printf '%s\n' "$status" >"$out_dir/${name}.status"
}

printf '%s\n' '== Health and metadata auth =='
probe mcp_health_public GET "$gateway/health"
probe mapping_missing_auth GET "$gateway/mcp/tools/mapping"
probe mapping_wrong_auth GET "$gateway/mcp/tools/mapping" -H 'Authorization: Bearer local-security-probe-wrong'
probe mapping_valid_auth GET "$gateway/mcp/tools/mapping" -H "Authorization: Bearer $token"
probe schema_missing_auth GET "$gateway/mcp/schema"
probe schema_valid_auth GET "$gateway/mcp/schema" -H "Authorization: Bearer $token"

printf '%s\n' '== Streamable HTTP auth and origin =='
probe initialize_missing_auth POST "$gateway/mcp" -H 'Content-Type: application/json' --data "$initialize"
probe initialize_malformed_auth POST "$gateway/mcp" -H 'Authorization: Token helperium-e2e-mcp-token' -H 'Content-Type: application/json' --data "$initialize"
probe initialize_wrong_auth POST "$gateway/mcp" -H 'Authorization: Bearer local-security-probe-wrong' -H 'Content-Type: application/json' --data "$initialize"
probe initialize_bad_origin POST "$gateway/mcp" -H "Authorization: Bearer $token" -H 'Origin: https://attacker.invalid' -H 'Content-Type: application/json' --data "$initialize"
probe initialize_allowed_origin POST "$gateway/mcp" -H "Authorization: Bearer $token" -H 'Origin: http://localhost:8080' -H 'Content-Type: application/json' --data "$initialize"

printf '%s\n' '== Tenant-scope manipulation =='
probe query_scope_rejected POST "$gateway/mcp?tenant=other-tenant" -H "Authorization: Bearer $token" -H 'Origin: http://localhost:8080' -H 'Content-Type: application/json' --data "$initialize"
probe traversal_scope_rejected POST "$gateway/mcp" -H "Authorization: Bearer $token" -H 'Origin: http://localhost:8080' -H 'X-Tenant-ID: ../../etc' -H 'Content-Type: application/json' --data "$initialize"
probe oversized_composite_rejected POST "$gateway/mcp" -H "Authorization: Bearer $token" -H 'Origin: http://localhost:8080' -H 'X-Tenant-ID: t1,t2,t3,t4,t5,t6,t7,t8,t9' -H 'Content-Type: application/json' --data "$initialize"

printf '%s\n' '== Safe response scan =='
if grep -RIEq 'helperium-e2e-mcp-token|ci-admin-token|ci-viewer-token|/workspace|Traceback|panic:' "$out_dir"/*.body; then
  echo 'SENSITIVE_MARKER_FOUND'
  exit 2
fi
echo 'No test-token, workspace-path, traceback, or panic marker in captured MCP bodies.'
