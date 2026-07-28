# AGENTS.md — Технический паспорт проекта

> Правила работы с документацией, граф знаний, скиллы, Cookbook — в [.pi/APPEND_SYSTEM.md](.pi/APPEND_SYSTEM.md)

## 🎯 1. О проекте

B2B self-hosting SaaS: клиент подключает свою БД → интроспекция схемы → генерация REST API + MCP-инструменты → AI-агент отвечает на вопросы над данными.

### Data flows

**Запрос данных (админка):** `Admin Dashboard (:8085) → data-service (:8084) → Query Engine (Expression AST → SQL) → Adapter.Conn → Client DB`

**LLM Chat (SSE stream):** `Embed Widget → api-service (:8081) → orchestrator → LLM (LiteLLM) → tool_call → MCPClient → mcp-gateway (:8083) → data-service → DB → SSE → Widget`

**Альтернатива (админка):** `Admin Dashboard → proxyToApiService() → api-service` (тот же `chat_agent_handler`). `demo/web` (:8080) — dev-only.

**Типы SSE-событий:** `token`, `tool_call`, `tool_result`, `final`, `error`, `done`, `audio`.

### ⚠️ data-service — не semantic search

Search strategies (`data-service/internal/search/`): **grep** (multi-token AND, regex, ignore_case, invert) · **filter** (field-based: `field__gt`, `__like`, `__in`) · **schema** (discovery: distinct, min/max/avg, total). Детали: [search-strategies.md](doc/agents/search-strategies.md), [adapter-pattern.md](doc/agents/adapter-pattern.md).

### Agent pipeline (Protocol-based DI)

```
LLMAgent (orchestrator)
  └── Pipeline: GuardInput → ToolDiscovery → LLMStage → ToolExecution (цикл)
                → Fallback → GuardOutput → SaveHistory
      каждое событие → SpendingMiddleware → BacklogMiddleware → TokenBudgetMiddleware
```

Protocol'ы: LLMProvider, ConversationStore, SpendingTracker, BacklogWriter, GuardChecker, MCPToolProvider (`agent/protocols.py`).

## 🏗️ 2. Архитектура

### 2a. MCP
1. SSE-сессия (GET /mcp) → `event: endpoint`
2. POST /mcp/message?sessionId= → JSON-RPC → `HandleMessage()`
3. `FetchConfigWithTenant()` → GET data-service `/mcp/manifest` (кэшируется **30s TTL**, `InvalidateManifestCache()` для сброса) → `tools.NewRegistry(cfg)`
4. Генерация инструментов: `configgen.GenerateMCPTools()`. Strategy-тулы — параметры от `Strategy.ToolParams()`
5. Тулы: `grep_{entity}`, `get_{entity}`, `filter_{entity}`, `count_{entity}`, `distinct_{entity}`, `schema_{entity}`
6. **Composite Mode:** `X-Tenant-ID: a,b` → префикс `{tenantID}__grep_product`

### 2b. Tenant Lifecycle
POST /admin/tenants → bootstrap; POST /admin/config/rewrite → интроспекция; `.data/tenants/{id}.json`.

### 2c. Config
**Авто:** entities[], endpoints[], mcp_tools[], read_only: `true`. **Вручную:** custom_queries{}, auth{}, mcp_tools[].description, introspection{}, approved_tools[].
**Strategy-эндпоинты:** `endpoints[].strategy = grep | filter | schema`.
**Схема:** `helperium-go/config/types.go:Config`. [specs/config.schema.md](specs/config.schema.md), [config-migration.md](doc/agents/config-migration.md).

### 2d. Adapter Pattern
`datasource.Adapter` (Driver, Connect, Introspect, TranslatePlaceholder, QuoteIdentifier). [adapter-pattern.md](doc/agents/adapter-pattern.md).

### 2e. HTTP Client Layer
- **mcp-gateway → data-service:** `FetchConfigWithTenant()`, `GetData()` — stateless http.Client
- **api-service → mcp-gateway:** MCPClient — SSE-сеанс на tenant, `asyncio.Lock`, 30s timeout
- **demo-web → все:** `httpx.AsyncClient`, SSE streaming

HTTP-матрица (11 каналов): [doc/api-flow.md](doc/api-flow.md). Детали: [http-clients.md](doc/agents/http-clients.md).

### 2f. Tenant Isolation — database-level · tool-level (`tenant-a__grep_products`) · session-level
Tenant_id недоступен LLM как field__op; field whitelist по `findColumn()`; `exclude_from_search` для PII. [security-isolation.md](doc/agents/security-isolation.md).

### 2h. Write-Tool Approval
Default `read_only: true`. Активация: `false` в конфиге, PUT /admin/tenants/{id}/config, POST /admin/tools/{toolName}/approve.

### 2j/2k. Anti-Abuse
3 уровня: JSON Schema (MCP gateway) → Server-side guard (limits, ReDoS, 30s timeout) → Empty Hints. [anti-abuse.md](doc/agents/anti-abuse.md), [tool-call-safety-layers.md](doc/agents/tool-call-safety-layers.md).

## 🛠️ Карта сервисов

| Сервис | Порт | Роль | README |
|---|---|---|---|
| **api-service** (Python) | :8081 | Embed-виджет, оркестратор, LiteLLM | [README](api-service/README.md) |
| **data-service** (Go) | :8084 | Expression AST → SQL, search strategies | [README](data-service/README.md) |
| **mcp-gateway** (Go) | :8083 | MCP SSE/JSON-RPC, composite, кэш манифеста | [README](mcp-gateway/README.md) |
| **admin-dashboard** (Go) | :8085 | Admin Web UI (Alpine.js) | [README](admin-dashboard/README.md) |
| **rag-service** (Python) | :8082 | ChromaDB, опционально | [README](rag/README.md) |
| **demo/web** (Python) | :8080 | Dev-only reverse proxy | [README](demo/web/README.md) |
| **agent-db** (Python) | — | Seedgen, materialize, e2e | [README](agent-db/README.md) |
| **helperium-go** (Go) | — | Config types, validation | [configgen/README.md](data-service/internal/configgen/README.md) |

**Web Service Multi-Tenancy:** [web-service.md](doc/agents/web-service.md)

## 🚀 Эксплуатация · 🧪 Тесты · 📊 Monitoring · ✅ CI/CD

`./scripts/dev.sh restart` — пересборка всех сервисов. [operations.md](doc/agents/operations.md) · [testing-guide.md](doc/agents/testing-guide.md) · [monitoring.md](doc/agents/monitoring.md) · [ci-cd.md](doc/agents/ci-cd.md)

## 🧬 Verification

```
Last verified: 2026-07-28 (commit a12e54c96fb1b751902329133786daf8bab8e971)
Следущая плановая: 2026-09-01 или после изменения config типов.
После любой правки документа — обновить дату и хеш коммита здесь.
```

> **Knowledge graph workflow, Cookbook, skills, agent rules, decision tree** — смотри [.pi/APPEND_SYSTEM.md](.pi/APPEND_SYSTEM.md)
