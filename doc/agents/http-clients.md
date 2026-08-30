# HTTP Client Layer — Как сервисы общаются

> Детальные описания сервисов и их API — в соответствующих README:
> - [`services/mcp-gateway/README.md`](../../services/mcp-gateway/README.md) — MCP-шлюз, Streamable HTTP transport
> - [`services/data-service/README.md`](../../services/data-service/README.md) — CRUD, query builder
> - [`services/api-service/README.md`](../../services/api-service/README.md) — оркестратор, LLM, виджет
> - [`demo/web/README.md`](../../demo/web/README.md) — reverse-proxy для разработки

## mcp-gateway → data-service

`services/mcp-gateway/internal/httpclient/client.go`:
- `FetchConfigWithTenant(tenantID)` → GET `http://data-service:8084/mcp/manifest` с `X-Tenant-ID: {id}` header (query-параметр tenant не используется — authority только в header)
- `FetchSchemaWithTenant(tenantID)` → GET `http://data-service:8084/mcp/schema`
- `Call(ctx, endpoint, params)` → GET `http://data-service:8084/{endpoint}?{params}` с `X-Tenant-ID`
- Stateless `http.Client`. 30s TTL-кэш на manifest. Ошибка → JSON `{"error": "..."}`

**Strategy endpoints** (search strategies):
- МCP manifest (`/mcp/manifest`) теперь генерирует `search_*`/`grep_*`/`filter_*` тулы через `configgen.GenerateMCPTools()`.
- Каждая strategy-тула в манифесте содержит поле `Endpoint` с путём вроде `/{entity}/search`, `/{entity}/grep`, `/{entity}/filter`.
- mcp-gateway при выполнении тула вызывает `Call(ctx, endpoint=tool.Endpoint, params=...)` — это идёт в тот же `httpClient.GetData()`.
- Параметры для strategy-тулов (required, types, описания) генерируют сами стратегии через `Strategy.ToolParams()` — не нужно вручную описывать `mcp_tools[]` в конфиге.

## api-service (MCPClient) → mcp-gateway

`services/api-service/src/api_service/agent/mcp_client.py`:
- Один persistent Streamable HTTP v2 connection на tenant scope через единый endpoint `POST/GET/DELETE /mcp`
- `mcp.client.streamable_http.streamable_http_client()` и `Client(transport, mode="legacy")` из официального Python MCP SDK v2; mode сохраняет standard `initialize` handshake, пока mcp-go не поддерживает auto-mode `server/discover` negotiation
- `asyncio.Lock` на connection, `MCP_LOCK_ACQUIRE_TIMEOUT = 10s`, `MCP_TOOL_EXECUTION_TIMEOUT = 15s`, `MCP_HTTP_READ_TIMEOUT = 30 min`
- При ошибке — переоткрытие Streamable HTTP connection; idle connections закрываются фоновой очисткой

## demo-web → все сервисы (для разработки/демо)

`demo/web/server.py`:
- `httpx.AsyncClient` с `timeout=WEB_PROXY_TIMEOUT` (default 30s)
- `_proxy_to_api()` — SSE streaming побайтово в api-service
- `_proxy_to_data_service()` — GET-запросы JSON в data-service
- `_proxy_to_rag()` — запросы в rag-service (с поддержкой разных HTTP-методов)
- Прокидывает `X-Tenant-ID`, `X-Correlation-ID` (uuid4), `Forwarded`, `User-Agent`, `Accept-Language`, `Accept-Encoding`

**Важно:** demo-web — это reverse-proxy для разработки/демонстрации, а не продакшен entry point.
Основной клиент — embed-виджет, который ходит напрямую в api-service (:8081).
Админка (admin-dashboard) ходит напрямую в свои бэкенды, минуя demo-web.
---
**Last verified:** 2026-08-20 (commit `0337712`) — структура клиентов, Streamable HTTP lifecycle, explicit SDK negotiation mode и таймауты сверены с кодом и live native MCP turns.
