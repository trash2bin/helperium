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
