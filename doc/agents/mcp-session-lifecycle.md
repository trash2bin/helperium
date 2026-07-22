# MCP Session Lifecycle

```
GET /mcp (клиент)
  │
  │ 1. sseHandler():
  │    - MaxSessions = 1000
  │    - sseSession{sessionID, writer, flusher, tenantIDs}
  │    - `event: endpoint\ndata: http://.../mcp/message?sessionId={id}`
  │    - idle-таймер (SessionIdleTimeout = 5m)
  │
  │ 2. POST /mcp/message?sessionId={id}
  │    - mcpPostHandler():
  │      a. Загружает SSE-сессию из sync.Map
  │      b. session.ensureCompositeServer(tenantIDs)
  │         → lazy init или reuse
  │      c. mcpServer.HandleMessage(ctx, rawMessage) — JSON-RPC
  │      d. Response → SSE `event: message\ndata: {...}`
  │      e. POST возвращает 202 Accepted
  │
  │ idle → session.isExpired() → удаление из sync.Map
  │ ctx.Done() → defer delete
```

**Ключевые файлы:** `mcp-gateway/cmd/main.go` — `sseHandler()`, `mcpPostHandler()`, `createCompositeServer()`

## Tool Registry & Strategy Integration

При создании composite-сервера (`createCompositeServer()`) инициализируется реестр инструментов, который получает манифест от data-service по `GET /mcp/manifest`.

### Генерация MCP-тулов

Манифест генерируется через `configgen.GenerateMCPTools()`. Для entity, у которых есть strategy-эндпоинты (`endpoints[].strategy`):
- **Вместо** `find_*` / `list_*` генерируется единый `search_*` инструмент
- Имя, описание и параметры определяют сами стратегии через `Strategy.ToolName()`, `Strategy.ToolDescription()`, `Strategy.ToolParams()`
- Стратегии: `grep` (multi-token AND, regex, multi-field), `filter` (field `__gt`/`__like`/`__in`), `simple` (backward compat), `search` (grep+filter комбо)
- Для entity без стратегии сохраняются legacy-тулы (`find_*`, `list_*`)
- Custom query relationship-тулы (`products_by_brand`) не генерируются, если entity входит в strategy

Подробнее о стратегиях: [search-strategies.md](search-strategies.md)
