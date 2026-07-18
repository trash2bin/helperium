# HTTP Client Layer — Как сервисы общаются

## mcp-gateway → data-service

`mcp-gateway/internal/tools/client.go`:
- `FetchConfigWithTenant(tenantID)` → GET `http://data-service:8084/mcp/manifest?tenant={id}`
- `GetData(tenantID, path, params)` → GET `http://data-service:8084/{path}?{params}` с `X-Tenant-ID`
- Stateless `http.Client`. Ошибка → JSON `{"error": "..."}`

## api-service (MCPClient) → mcp-gateway

`api-service/src/api_service/agent/mcp_client.py`:
- Один persistent SSE-сеанс на tenant (GET /mcp + очередь POST)
- `mcp.client.sse.sse_client()` из официального Python MCP SDK
- `asyncio.Lock` на сессию, `LOCK_ACQUIRE_TIMEOUT = 10s`, `TOOL_EXECUTION_TIMEOUT = 15s`, `sse_read_timeout = 30 min`
- При ошибке — переоткрытие сессии

## demo-web → все сервисы

`demo/web/server.py`:
- `httpx.AsyncClient` с `timeout=60s`
- `_proxy_to_api()` — SSE streaming побайтово
- `_proxy_to_data_service()` — JSON
- Прокидывает `X-Tenant-ID`, `X-Request-ID` (uuid4), `Forwarded`, `User-Agent`
