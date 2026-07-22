# AGENTS.md — Технический паспорт проекта

Краткий архитектурный контекст. Детали — в `doc/agents/*`.

## 🎯 1. О проекте

B2B SaaS: клиент подключает свою БД → платформа интроспектирует схему → автоматически генерирует REST API + MCP-инструменты → AI-агент отвечает на вопросы над данными.

### 🔄 Data flow: Запрос данных (админка → data-service)

```
Admin Dashboard (:8085) → GET /admin/tenants/{id}/data/{entity}
  → data-service (:8084) — chi router → tenantStore.resolveTenant()
    → generic handler (get_by_id / find / list / custom_query / grep / filter)
      → Query Engine (Expression AST → SQL, placeholder адаптация под СУБД)
        → Adapter.Conn.QueryContext → Client DB (SQLite/PG)
```

### 🔄 Data flow: LLM Chat (SSE stream)

```
Embed Widget (браузер) — <script src="/embed/embed.js" data-agent="shop-assistant">
  → POST /api/agents/{name}/chat [X-Tenant-ID]
    → api-service (:8081)
      → chat_agent_handler() → get_agent_store().get_agent()
        → resolve tenant_ids из конфига агента
        → _check_abuse() → guard check
        → orchestrator.stream_events()
          → load history → LLM call (LiteLLM)
            → tool_call → MCPClient.call_tool() (если LLM вернул)
              → mcp-gateway (:8083) → data-service → DB
            → tool_result → следующий LLM call → final ответ
          → yield AgentEvent(type="token"|"tool_call"|"tool_result"|"final"|...)
        → SSE → Widget (Shadow DOM, token-by-token)
```

**Альтернативный entry point (админка):**
```
Admin Dashboard (:8085) → proxyToApiService()
  → api-service (:8081) → ... тот же chat_agent_handler()
```

**Важно:** `demo/web` (:8080) — это **рудимент MVP**, используется только для локальной разработки и тестов. В production/widget-сценарии не участвует. Виджет ходит напрямую в api-service.

**Типы SSE-событий:** `token`, `tool_call`, `tool_result`, `final`, `error`, `done`, `audio` (для TTS).

**⚠️ Важно про data-service:** Не semantic search. Поиск строится через Expression AST (`data-service/internal/query/`) — Condition + Operator дерево превращается в SQL через `Engine.Build()`. Новые search strategies (`data-service/internal/search/`):
- **grep** — multi-token AND, multi-field OR, regex, ignore_case, invert (аналог GNU grep)
- **filter** — field-based c компараторами (`field__gt`, `field__like`, `field__in`)
- **simple** — backward compat для старых find/list

Подробно: [doc/agents/search-strategies.md](doc/agents/search-strategies.md)

Не строит JOIN'ы на лету — только то, что описано в конфиге tenant'а. LLM сама решает, какой инструмент вызвать.

**Архитектура api-service/agent/ (Pipeline + Protocol-based DI):**
```
LLMAgent (orchestrator) — тонкий координатор (~268 строк)
  └── Pipeline (pipeline.py)
        ├── Фаза 1 — цикл (stages):
        │     GuardInputStage → ToolDiscoveryStage
        │   → LLMStage → ToolExecutionStage (повтор)
        └── Фаза 2 — финализация (finalizer_stages):
              FallbackStage → GuardOutputStage → SaveHistoryStage
        каждое событие проходит через Middleware:
          SpendingMiddleware → BacklogMiddleware → TokenBudgetMiddleware
```
**Protocol'ы (contracts):** `agent/protocols.py` — LLMProvider, ConversationStore, SpendingTracker, BacklogWriter (sync), GuardChecker, MCPToolProvider.
**PipelineContext** — типизирован через Protocol'ы (store, spending, backlog, guard_checker), кроме mcp_session (внутренний _SessionProxy).
**Адаптеры:** `LiteLLMProvider` (чистый LLM вызов), `ProviderPool` (health check + failover), `legacy_adapters.py` (backward compat).

**Ключевые файлы:** `api-service/src/api_service/` — `server.py`, `agent/orchestrator.py`, `agent/pipeline.py`, `agent/stages.py`, `agent/middlewares.py`, `agent/protocols.py`, `agent/models.py`, `agent/event_stream.py`, `agent/types.py`, `agent/mcp_client.py`
**Embed-виджет:** `api-service/embed/src/` — `index.ts`, `dom.ts`, `sse.ts`, `voice.ts`, `storage.ts`, `types.ts`

## 🏗️ 2a. MCP — Архитектура

1. **SSE-сессия** (GET /mcp): клиент открывает долгий SSE-стрим, получает `event: endpoint` с URL для POST
2. **POST /mcp/message?sessionId=...**: JSON-RPC → `mcpPostHandler()` → `mcpServer.HandleMessage()`
3. **Создание MCP-сервера**: `FetchConfigWithTenant()` → GET к data-service `/mcp/manifest` → `tools.NewRegistry(cfg)`
4. **Генерация инструментов**: манифест возвращает `mcp_tools[]`, сгенерированный через `configgen.GenerateMCPTools()`. Для strategy-эндпоинтов (grep, filter) параметры и описания генерируют сами стратегии через `Strategy.ToolParams()/ToolDescription()` — см. [doc/agents/search-strategies.md](doc/agents/search-strategies.md).
5. **Каждый инструмент** — closure с `httpClient.GetData()` к data-service

**Как выглядят тулы для LLM:**
```
grep_products   — поиск по тексту: token search, regex, multi-field, ignore_case, format
get_products    — по ID
filter_orders   — field__gt/lt/lte/gte/like/in + пагинация
count_products  — количество с фильтрами
```

**Composite Mode** (один агент — N tenant'ов): заголовок `X-Tenant-ID: tenant-a,tenant-b` → `createCompositeServer()` → инструменты с префиксом `{tenantID}__grep_catalog_product`. При 1 tenant — legacy-режим без префикса.

**Детали MCP Session Lifecycle:** [doc/agents/mcp-session-lifecycle.md](doc/agents/mcp-session-lifecycle.md)

## 🏗️ 2b. Tenant Lifecycle

**Создание:** admin API POST /admin/tenants + config.json, или bootstrap при старте.
**Rewrite:** POST /admin/config/rewrite → интроспекция БД → генерация конфига.
**Persistence:** `.data/tenants/{id}.json` — восстанавливаются при старте.
**Удаление:** DELETE /admin/tenants/{id} → graceful drain.

**Детали:** [doc/agents/tenant-lifecycle.md](doc/agents/tenant-lifecycle.md)

## 🏗️ 2c. Config — что генерируется/редактируется

**Авто:** entities[], endpoints[] (GET /{entity}/{id}, GET /{entity}, GET /{entity}/grep, GET /{entity}/filter, GET /{entity}/count), mcp_tools[] (через стратегии), stats.counters[], read_only: true
**Вручную:** custom_queries{}, метод POST/PUT/DELETE, auth{}, mcp_tools[].description/display_name, introspection{}, approved_tools[], readonly_dsn

**Strategy-эндпоинты** (поле `endpoints[].strategy`): `grep`, `filter`, `simple`. MCP-параметры для них генерирует сама стратегия — не нужно вручную описывать `mcp_tools[]`.

**Схема:** `helperium-go/config/types.go:Config`. Версионируется через `Normalize()` — старые конфиги (v0, v1) апгрейдятся автоматически при загрузке.
**Детали схемы:** [specs/config.schema.md](specs/config.schema.md)
**Миграции:** [doc/agents/config-migration.md](doc/agents/config-migration.md)

## 🔌 2d. Adapter Pattern

Реализовать `datasource.Adapter` (Driver, Connect, Introspect, TranslatePlaceholder, QuoteIdentifier) → зарегистрировать в `NewDefaultRegistry()` → добавить const + Valid().

**Детали:** [doc/agents/adapter-pattern.md](doc/agents/adapter-pattern.md)

## 📦 2e. HTTP Client Layer

**mcp-gateway → data-service:** `FetchConfigWithTenant()`, `GetData()` — stateless http.Client
**api-service → mcp-gateway:** MCPClient — один SSE-сеанс на tenant, `asyncio.Lock`, 30s timeout
**demo-web → все:** `httpx.AsyncClient`, SSE streaming, проксирует X-Tenant-ID/X-Request-ID

**Детали:** [doc/agents/http-clients.md](doc/agents/http-clients.md)

## 🔐 2f. Tenant Isolation — Три уровня

1. **Database-level:** каждый tenant — отдельная БД (SQLite файл / PG схема)
2. **Tool-level:** префикс `tenant-a__find_...` привязывает инструмент к tenant'у
3. **Session-level:** `tenant_ids` передаётся web → orchestrator → MCPClient

**RBAC в admin dashboard:** admin (ADMIN_TOKEN) vs viewer (VIEWER_TOKEN).

**Детали:** [doc/agents/security-isolation.md](doc/agents/security-isolation.md)

## 📝 2h. Write-Tool Approval

По умолчанию `read_only: true`. Активация: ручное `"read_only": false` в конфиге, PUT /admin/tenants/{id}/config, или POST /admin/tools/{toolName}/approve.

## ~~2i. Config Schema Validation~~ — удалена. Валидация в `helperium-go/config/types.go`.

## ⚡ 2j. Rate Limiting & Anti-Abuse

**Детали:** [doc/agents/anti-abuse.md](doc/agents/anti-abuse.md)

## 🛠️ Карта сервисов

| Сервис | Порт | Ключевая роль |
|---|---|---|
| **api-service** (Python) | :8081 | **Мозг.** Embed-виджет (TS), оркестратор агента, LiteLLM, чат (SSE), agent CRUD, voice (STT/TTS), spending, guardrails, LLM provider store |
| **data-service** (Go) | :8084 | Generic CRUD + custom_queries (только SELECT) + **search strategies** (grep/filter). Config-driven — Expression AST → SQL, placeholder адаптация под СУБД. **Не semantic search** — точное совпадение + LIKE + regex по полям. Безопасная обёртка над БД. Детали поиска: [doc/agents/search-strategies.md](doc/agents/search-strategies.md) |
| **mcp-gateway** (Go) | :8083 | MCP SSE/JSON-RPC, composite инструменты, tenant-aware tool registry. MCP-тулы генерятся из data-service `/mcp/manifest` — strategy-тулы получают параметры от стратегий |
| **admin-dashboard** (Go) | :8085 | Web UI для администрирования (Alpine.js), proxy к api-service/data-service |
| **rag-service** (Python) | :8082 | Поиск по документам (ChromaDB), опционально |
| **demo/web** (Python) | :8080 | **Рудимент MVP.** Только для локальной разработки. Reverse-proxy ко всем сервисам |
| **sdk** (Python) | — | Pydantic-модели и клиенты |
| **helperium-go** (Go) | — | Shared Go-модели |

### 🌐 Web Service Multi-Tenancy

**Детали:** [doc/agents/web-service.md](doc/agents/web-service.md)

## 🚀 3. Эксплуатация и разработка

`./scripts/dev.sh restart` - основной способ перезапуска **всех** сервисов (полная пересборка в том числе фронта)
**Детали:** [doc/agents/operations.md](doc/agents/operations.md)

## 🧪 4. Регрессионное тестирование

**Детали:** [doc/agents/testing-guide.md](doc/agents/testing-guide.md)

## 🧠 5. Knowledge Graph (codebase-memory)

**Не читай код вслепую — используй граф.**
1. **Поиск:** `codebase_memory_search_graph({ query: "...", project: "helperium" })`
2. **Трассировка:** `codebase_memory_trace_path({ function_name: "...", project: "helperium", direction: "both", mode: "calls", depth: 3 })`
3. **Поиск по коду:** `codebase_memory_search_code({ pattern: "...", project: "helperium" })`
4. **Обновление после правок:** `codebase_memory_index_repository({ repo_path: ".", name: "helperium", mode: "moderate" })`

## ✅ 9. CI/CD и Quality Gates

**Детали:** [doc/agents/ci-cd.md](doc/agents/ci-cd.md)

### Критерий готовности перед коммитом

1. [ ] `make ci` — зелёный (Go + Python + agent pipeline unit tests)
2. [ ] Pre-commit hooks — все Passed
3. [ ] `uv run pytest src/api_service/tests/unit/agent/ -v` — 159 agent-тестов (58 pipeline unit + 101 legacy)

## 📊 10. Monitoring & Observability

На проекте используеться Grafana + Prometheus
**Детали:** [doc/agents/monitoring.md](doc/agents/monitoring.md)

## 🧹 2k. Tool Abuse Prevention — защита от пустых/жадных LLM вызовов

### Проблема
LLM склонна вызывать инструменты с пустыми аргументами: `search_auto_parts({})`. Если не заблокировать — дамп всей таблицы, перерасход, abuse.

### Решения (3 уровня защиты)

**Уровень 1 — JSON Schema Validation (MCP Gateway)**
- `search_*` тулы имеют `pattern` с `required: true` + `minLength: 1`
- MCP гейтвей **отклоняет pre-request** если `pattern` отсутствует или пустой → `isError: true`
- Реализация: `go`-стратегия через `Strategy.ToolParams()` задаёт `Required: &t`

**Уровень 2 — Server-side guard (data-service)**
- `search.go`: `ParseRequest()` проверяет `pattern != ""` и `len(pattern) >= 1`, возвращает 400 при нарушении
- `search.go`: `maxFilters=15`, `maxTotalConditions=25` — защита от ReDoS/token flood
- `filter.go`: `parseFilterLimit` default `10` (было 20)
- `Config.MCPTool` carries `Required: &t` — приходит через manifest в mcp-gateway и проверяется там
- См. `data-service/internal/search/search.go:ParseRequest`

**Уровень 3 — LLM Prompt Engineering**
- `llm.go`: hints описывают эффективный воркфлоу: `distinct → count → search`
- `llm.go`: explicit примеры `search_auto_parts(pattern='oil filter')`
- `_build_tool_result` (api-service): error message содержит конкретный пример вызова
- `llm.go` hints **не содержат** relationship tools (`products_by_category`) — они убраны из манифеста

### Filtering старых/relationship тулов
`mcp.go:GenerateMCPTools()`:
- Строит `hasStrategy` map — все entity, у которых есть `endpoint[].strategy != ""`
- **Skip** `config.OpFind` → если entity в `hasStrategy`, `find_*` не генерится
- **Skip** `config.OpList` → если entity в `hasStrategy`, `list_*` не генерится
- **Skip** `config.OpCustomQuery` → если entity в `hasStrategy`, `products_by_category` и прочие relationship тулы не генерится
- Вместо них: `search_*` (от strategy), `get_*`, `count_*`, `distinct_*`

### Security limits per strategy
| Strategy | Limits |
|----------|--------|
| `search` (search.go) | `maxFilters=15`, `maxTotalConditions=25`, `pattern minLength=1` |
| `grep` (grep.go) | `maxFilterValueLen`, `maxRegexLen` (default 200), `maxValues` for `field__in` (default 100) |
| `filter` (filter.go) | `parseFilterLimit` default 10 |
| `simple` (simple.go) | — |

### Logging
- `stages.py`: логгирует `name`, `arguments`, `iteration` до/после/при ошибке
- `mcp_client.py`: логгирует `[MCP] Calling tool X with args=Y`, результат `[MCP] Tool X completed: N blocks, M chars`
- `server.py` SSE events: `token`/`audio` только в DEBUG; `tool_call`/`tool_result`/`final`/`error`/`done` — INFO

**Детали:** `data-service/internal/search/`, `data-service/internal/configgen/mcp.go`

## ⚠️ Важные правила

- **Никакого SQL в Проекте** — только HTTP к data-service (либо генерация тестовой бд разрешаеться)
- **Виджет — основной клиент.** embed/embed.js — единственный production-ready UI. demo/web — для тестов
- **Generic-подход** — не хардкодить сущности в коде
- **Не кешировать MCP manifest** — всегда регенерировать через `configgen.GenerateMCPTools(cfg.Endpoints, ...)` (см. `mcp_manifest.go`)
- **`search_*` тулы всегда с `required=['pattern']`** — защита от пустых вызовов
