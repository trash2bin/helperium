# MCP Session Lifecycle — Tool Registry & Security

## Архитектура

`api-service` использует официальный Python `mcp` v2 и обращается к единственному standard Streamable HTTP endpoint `/mcp`.

| Transport | Flow | Состояние |
|---|---|---|
| **Streamable HTTP** | `/mcp` → `streamableTenantRegistry` → tenant-set-specific `mcp-go StreamableHTTPServer` → `createCompositeServer()` → tool handler → data-service | Единственный supported transport |

В Streamable HTTP gateway создаёт и кэширует отдельный stateful handler для уже переданного набора `X-Tenant-ID`. Поэтому manifest и closures остаются tenant-scoped; primary tenant инжектируется в request context только для single-tenant compatibility handlers. MCP session id никогда не используется как авторизационный источник.

```
POST /mcp (api-service v2 Client, authorized X-Tenant-ID + required production bearer)
  │
  ├─ streamableTenantRegistry.handlerFor(tenantIDs)
  │    └─ createCompositeServer(tenantIDs) on first scope use
  │         └─ registry.RegisterAll(mcpServer)
  │
  └─ StreamableHTTPServer
       └─ tools/list or tools/call → validateArgs() → data-service
```

Legacy GET-SSE/POST JSON-RPC transport intentionally removed. Rollback этой migration выполняется deploy предыдущего tested image, а не runtime transport switch.

**Ключевые файлы:** `services/mcp-gateway/cmd/main.go` — `streamableTenantRegistry`, `createCompositeServer()`; `services/api-service/src/api_service/agent/mcp_client.py` — Streamable HTTP client и per-tenant connection lifecycle.

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
    ├── Consolidated db_* (O(1), /q/*):
    │   ├── db_map       — GenerateSchemaForLLM (карта БД + hints)
    │   ├── db_describe  — search.NewSchemaStrategy()
    │   ├── db_search    — search.NewGrepStrategy()
    │   ├── db_get       — GetByIDHandler (id из результата поиска)
    │   └── db_related   — RelatedHandler (один FK-хоп, tenant+лимит)
    │
    ├── Per-entity filter_{entity} (живой REST-роут /{entity}/filter):
    │   └── filter_{entity} — search.NewFilterStrategy() (поля в схеме тула)
    │
    ├── Opt-in (config.LLMToolPolicy, default false):
    │   ├── get_{entity}
    │   ├── count_{entity}
    │   └── distinct_{entity}
    │
    ▼
mcp-gateway: buildTools() — строит toolDefs из cfg.MCPTools
    │
    └── registerOne() — регистрирует каждый tool с Required/InputSchema
```

### Какие тулы генерируются (Фаза 2/2.5 — N filter_* + 5 db_*)

| MCP Tool | Тип | Description source |
|---|---|---|
| `filter_{entity}` (N) | Per-entity strategy | `FilterStrategy.ToolDescription()` — поля в схеме тула |
| `db_map` | Consolidated (/q/map) | `GenerateSchemaForLLM` — карта БД + workflow hints |
| `db_describe` | Consolidated (/q/describe) | `SchemaStrategy.ToolDescription()` |
| `db_search` | Consolidated (/q/search) | `GrepStrategy.ToolDescription()` |
| `db_get` | Consolidated (/q/get) | GetByIDHandler (fail-closed tenant, id из поиска) |
| `db_related` | Consolidated (/q/related) | RelatedHandler (один FK-хоп, tenant+лимит) |
| `get_{entity}`/`count_{entity}`/`distinct_{entity}` | Opt-in (`LLMToolPolicy`, default false) | configgen inline |

## Три уровня защиты от пустых/опасных вызовов

| Уровень | Где | Что проверяет |
|---|---|---|
| **1 — JSON Schema** | mcp-go `Required()` в `InputSchema.Required` | `registerOne()` tools.go — `Required: &t` → `mcp.Required()` |
| **2 — Server-side guard** | `validateArgs()` tools.go + data-service `ParseRequest()` | required поля, empty string, numeric bounds, pattern length |
| **3 — Prompt engineering** | `ToolDescription()` grep.go/filter.go/schema.go | Явные примеры: `pattern='oil'`, NEVER pass empty string |

### Пример валидации

```
LLM вызывает: grep_product({})
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

// Single tenant:
// → инструменты без префикса
// grep_catalog_product, filter_catalog_product, ...
```

**Поведение:**
- Composite: тулы с префиксом (`tenant-a__grep_products`, `tenant-b__grep_products`)
- Single: тулы без префикса (`grep_products`)
- Scope сохраняет порядок, допускает не более `MCP_MAX_TENANTS_PER_SCOPE` unique IDs; duplicate или oversized header получает `400` до загрузки manifests.
- RAG-тулы: регистрируются один раз (не per-tenant).
  Защита: если RAG недоступен (`RagEnabled()` → false), `registerRagTools()`
  не регистрирует ни один RAG-тул, возвращаясь без ошибки.

## Session Lifecycle & Timeouts

| Параметр | Значение | Кем задаётся |
|---|---|---|
| Streamable HTTP session idle TTL | 5 минут | mcp-gateway main.go / mcp-go transport |
| api-service reconnect | 4 минуты (240s) | mcp_client.py |
| Per-query timeout | 30 секунд | data-service handlers.Context |

**Координация:** api-service реконнектится при 4 минутах idle. После реконнекта создаётся новая transport-managed Streamable HTTP session; tenant-scoped handler и registry остаются корректно привязаны к исходному tenant set.

## Безопасность

### Tenant authority and isolation

- Public direct `/api/chat` и direct voice chat всегда используют server-configured `[DEFAULT_TENANT_ID]` scope (fallback `default`) и игнорируют browser `X-Tenant-ID`.
- Только named-agent route берёт tenant IDs из persisted Agent Store; browser header не является авторизацией.
- `tenant_id` не доступен LLM как параметр (заблокирован на ParseRequest уровне).
- Gateway получает уже разрешённый scope только через `X-Tenant-ID`; query parameter не принимается.
- Composite mode: префикс tenant'а в имени инструмента и closure `tenantID` гарантируют изоляцию; session ID одного scope возвращает `404` при replay под другим scope.

### Transport ingress

- Production включает `MCP_REQUIRE_AUTH=true`; gateway не стартует без `MCP_API_KEY`, а api-service передаёт совпадающий `MCP_CLIENT_API_KEY`.
- Native service requests обычно не имеют `Origin`. Любой present browser Origin должен exactly match `MCP_ALLOWED_ORIGINS`, иначе gateway возвращает `403`.
- Sessions и tenant handler cache process-local в stateful mcp-go transport: держать один gateway instance, пока не настроены sticky sessions для scale-out.
- `/mcp/manifest`, `/mcp/tools/mapping` и `/mcp/schema` требуют ровно один validated tenant header; отсутствующий default fallback отключён.

### Field whitelist

- Каждое field-имя проходит через `findColumn()` / `entity.FindColumn()`
- Незнакомые имена: grep/filter — тихо скипают, distinct — 400 error
- PII/excluded поля не попадают ни в один инструмент

### Read-only

- ReadOnlyConn — только SELECT методы
- `cfg.DataSource.ReadOnly = true` по умолчанию
- Write-методы блокируются на уровне endpoint_builder

Подробнее о стратегиях поиска: [search-strategies.md](search-strategies.md)
---
**Last verified:** 2026-08-18 (working tree after `267974c`) — единственный Streamable HTTP `/mcp`, Python SDK v2, required-production auth, Origin policy, fixed direct-chat authority, tenant-scoped handlers, composite tools и cross-scope session rejection проверены deterministic E2E.
