# HTTP Client Layer — Как сервисы общаются

> Детальные описания сервисов и их API — в соответствующих README:
> - [`mcp-gateway/README.md`](../../mcp-gateway/README.md) — MCP-шлюз, SSE-сессии
> - [`data-service/README.md`](../../data-service/README.md) — CRUD, query builder
> - [`api-service/README.md`](../../api-service/README.md) — оркестратор, LLM, виджет
> - [`demo/web/README.md`](../../demo/web/README.md) — reverse-proxy для разработки

## mcp-gateway → data-service

`mcp-gateway/internal/httpclient/client.go`:
- `FetchConfigWithTenant(tenantID)` → GET `http://data-service:8084/mcp/manifest?tenant={id}`
- `FetchSchemaWithTenant(tenantID)` → GET `http://data-service:8084/mcp/schema`
- `Call(ctx, endpoint, params)` → GET `http://data-service:8084/{endpoint}?{params}` с `X-Tenant-ID`
- Stateless `http.Client`. 30s TTL-кэш на manifest. Ошибка → JSON `{"error": "..."}`

**Strategy endpoints** (search strategies):
- МCP manifest (`/mcp/manifest`) теперь генерирует `search_*`/`grep_*`/`filter_*` тулы через `configgen.GenerateMCPTools()`.
- Каждая strategy-тула в манифесте содержит поле `Endpoint` с путём вроде `/{entity}/search`, `/{entity}/grep`, `/{entity}/filter`.
- mcp-gateway при выполнении тула вызывает `Call(ctx, endpoint=tool.Endpoint, params=...)` — это идёт в тот же `httpClient.GetData()`.
- Параметры для strategy-тулов (required, types, описания) генерируют сами стратегии через `Strategy.ToolParams()` — не нужно вручную описывать `mcp_tools[]` в конфиге.

## api-service (MCPClient) → mcp-gateway

`api-service/src/api_service/agent/mcp_client.py`:
- Один persistent SSE-сеанс на tenant (GET /mcp + очередь POST)
- `mcp.client.sse.sse_client()` из официального Python MCP SDK
- `asyncio.Lock` на сессию, `LOCK_ACQUIRE_TIMEOUT = 10s`, `TOOL_EXECUTION_TIMEOUT = 15s`, `sse_read_timeout = 30 min`
- При ошибке — переоткрытие сессии

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
