# MCP Session Lifecycle — Tool Registry & Security

## Архитектура

```
GET /mcp (клиент — api-service/widget)
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
  │         → validateArgs() — 3 уровня защиты
  │         → execute() — HTTP запрос к data-service /mcp/manifest
  │      d. Response → SSE `event: message\ndata: {...}`
  │      e. POST возвращает 202 Accepted
  │
  │ idle → session.isExpired() → удаление из sync.Map
  │ ctx.Done() → defer delete
```

**Ключевые файлы:** `mcp-gateway/cmd/main.go` — `sseHandler()`, `mcpPostHandler()`, `createCompositeServer()`

## Tool Registry

При создании composite-сервера (`createCompositeServer()`) инициализируется реестр
инструментов, который получает манифест от data-service по `GET /mcp/manifest`.

### Поток генерации манифеста

```
mcp-gateway: createCompositeServer()
    │
    ├── FetchConfigWithTenant() → GET /mcp/manifest (data-service)
    │
    ▼
data-service: configgen.GenerateMCPTools()
    │
    ├── Strategy-based:
    │   ├── grep_{entity}     — search.NewGrepStrategy().ToolName/Params/Description
    │   ├── filter_{entity}   — search.NewFilterStrategy()
    │   └── schema_{entity}   — search.NewSchemaStrategy()
    │
    ├── Direct (без стратегии):
    │   ├── get_{entity}
    │   ├── count_{entity}
    │   └── distinct_{entity}
    │
    ▼
mcp-gateway: buildTools() — строит toolDefs из cfg.MCPTools
    │
    └── registerOne() — регистрирует каждый tool с Required/InputSchema
```

### Какие тулы генерируются

| MCP Tool | Тип | Description source |
|---|---|---|
| `grep_{entity}` | Strategy | `GrepStrategy.ToolDescription()` |
| `filter_{entity}` | Strategy | `FilterStrategy.ToolDescription()` |
| `schema_{entity}` | Strategy | `SchemaStrategy.ToolDescription()` |
| `get_{entity}` | Direct | configgen inline |
| `count_{entity}` | Direct | configgen inline |
| `distinct_{entity}` | Direct | configgen inline |

**Больше не генерируются:** `search_*`, `simple_*`, `find_*`, `list_*`, relationship tools.

## Три уровня защиты от пустых/опасных вызовов

| Уровень | Где | Что проверяет |
|---|---|---|
| **1 — JSON Schema** | mcp-go `Required()` в `InputSchema.Required` | `registerOne()` tools.go — `Required: &t` → `mcp.Required()` |
| **2 — Server-side guard** | `validateArgs()` tools.go + data-service `ParseRequest()` | required поля, empty string, numeric bounds, pattern length |
| **3 — Prompt engineering** | `ToolDescription()` grep.go/filter.go/schema.go | Явные примеры: `pattern='oil'`, NEVER pass empty string |

### Пример валидации

```
LLM вызывает: search_product({})
  Уровень 1:  Required → pattern required → провал, isError
  (даже не доходит до data-service)

LLM вызывает: grep_product({pattern: "a"*300, regex: true})
  Уровень 2:  maxRegexLen=200 → "pattern too long" → isError

LLM вызывает: grep_product({pattern: "", regex: false})
  Уровень 1:  minLength=1 → провал, isError
```

## Composite Mode (multi-tenant)

```go
// X-Tenant-ID: tenant-a,tenant-b
// → createCompositeServer() создаёт инструменты с префиксом {tenantID}__
// Пример: tenant-a__grep_catalog_product

// Single tenant (backward compat):
// → инструменты без префикса
// grep_catalog_product, filter_catalog_product, ...
```

**Поведение:**
- Composite: тулы с префиксом (`tenant-a__grep_products`, `tenant-b__grep_products`)
- Single: тулы без префикса (`grep_products`)
- RAG-тулы: регистрируются один раз (не per-tenant)

## Session Lifecycle & Timeouts

| Параметр | Значение | Кем задаётся |
|---|---|---|
| SessionIdleTimeout | 5 минут | mcp-gateway main.go |
| SessionMaxLifetime | 30 минут | mcp-gateway main.go |
| api-service reconnect | 4 минуты (240s) | mcp_client.py |
| Per-query timeout | 30 секунд | data-service handlers.Context |

**Координация:** api-service реконнектится при 4 минутах idle, за 60с до
Go-таймаута в 5 минут. После реконнекта → новый SSE session → свежий `/mcp/manifest`
→ полный ребилд registry.

## Безопасность

### Tenant isolation

- `tenant_id` не доступен LLM как параметр (заблокирован на ParseRequest уровне)
- TenantID инжектится сервером из HTTP-заголовка в Condition
- Composite mode: префикс tenant'а в имени инструмента гарантирует изоляцию

### Field whitelist

- Каждое field-имя проходит через `findColumn()` / `entity.FindColumn()`
- Незнакомые имена: grep/filter — тихо скипают, distinct — 400 error
- PII/excluded поля не попадают ни в один инструмент

### Read-only

- ReadOnlyDB — только SELECT методы
- `cfg.DataSource.ReadOnly = true` по умолчанию
- Write-методы блокируются на уровне endpoint_builder

Подробнее о стратегиях поиска: [search-strategies.md](search-strategies.md)
