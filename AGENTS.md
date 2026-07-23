# 🧠 ПРАВИЛО РАБОТЫ С ДОКУМЕНТАЦИЕЙ

**Перед любым ответом, который требует деталей (архитектура, поиск, конфиг, адаптеры, тестирование, CI/CD и т.п.) ОБЯЗАТЕЛЬНО прочитай соответствующий файл из `doc/agents/`, используя инструмент `read_file`.**
Никогда не полагайся только на краткое описание в этом файле или на код – детали всегда в отдельных документах.

# AGENTS.md — Технический паспорт проекта

## 🎯 1. О проекте

B2B self-hosting SaaS: клиент подключает свою БД → платформа интроспектирует схему → автоматически генерирует REST API + MCP-инструменты → AI-агент отвечает на вопросы над данными.

### 🔄 Data flow: Запрос данных (админка → data-service)

```
Admin Dashboard (:8085) → GET /admin/tenants/{id}/data/{entity}
  → data-service (:8084) — chi router → tenantStore.resolveTenant()
    → generic handler (get_by_id / custom_query / grep / filter)
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

**⚠️ Важно про data-service:** Не semantic search. Поиск строится через Expression AST (`data-service/internal/query/`) — Condition + Operator дерево превращается в SQL через `Engine.Build()`. Search strategies (`data-service/internal/search/`):
- **grep** — multi-token AND, multi-field OR, regex, ignore_case, invert (аналог GNU grep)
- **filter** — field-based c компараторами (`field__gt`, `field__like`, `field__in`)
- **schema** — discovery: мета-информация о сущности (distinct values, min/max, count)

Подробно: [doc/agents/search-strategies.md](doc/agents/search-strategies.md)

Не строит JOIN'ы на лету — только то, что описано в конфиге tenant'а. LLM сама решает, какой инструмент вызвать.

**DataSource слой:** `data-service/internal/datasource/` — абстракция над источником данных. Позволяет подключать не-SQL бэкенды (CRM, NoSQL, API) через единый `DataSource` interface. Сейчас реализован `SQLDataSource` через query.Engine. Детали: [doc/agents/adapter-pattern.md](doc/agents/adapter-pattern.md)

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
grep_products     — поиск по тексту: token search, regex, multi-field, ignore_case, format
get_products      — по ID
filter_orders     — field__gt/lt/lte/gte/like/in + пагинация
count_products    — количество с фильтрами
distinct_brands   — уникальные значения колонки
schema_products   — discovery: мета-информация о сущности
```

**Рекомендуемый workflow для LLM:**
```
Шаг 1: schema_{entity}() → узнать что есть в БД (1 вызов)
Шаг 2: distinct_{entity}(column="brand") → узнать значения
Шаг 3: grep_{entity}(pattern="Brembo", limit=10) → поиск
[При пустом результате] → empty_hint с подсказкой
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

**Strategy-эндпоинты** (поле `endpoints[].strategy`): `grep`, `filter`, `schema`. MCP-параметры для них генерирует сама стратегия — не нужно вручную описывать `mcp_tools[]`.

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
2. **Tool-level:** префикс `tenant-a__grep_products` привязывает инструмент к tenant'у
3. **Session-level:** `tenant_ids` передаётся web → orchestrator → MCPClient

**Дополнительно:**
- `tenant_id` **недоступен LLM** как field__op параметр — заблокирован на уровне ParseRequest
- TenantID инжектится сервером из контекста аутентификации, не из LLM-аргументов
- Field whitelist: каждое поле проходит через `findColumn()`, незнакомые имена скипаются
- PII/excluded поля: `exclude_from_search` — не участвуют ни в одном туле

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

**Важно:** E2E-тесты с LLM (`tests/e2e/llm/`) — дорогие по токенам (~50K за прогон).
Запускать только перед коммитом/PR, не в CI на каждый push.
Сначала unit + integration, потом e2e без LLM, и только в конце — LLM-тесты.

## 🧭 4a. LLM Tool Workflow (search strategies)

**Как LLM должна искать данные:**

```mermaid
flowchart TB
    A[User query] --> B{schema_{entity}()}
    B --> |discovery| C[distinct_{entity}\ncolumn='brand']
    C --> D[grep_{entity}\npattern='Brembo'\nlimit=10]
    D --> |total>0| E[Return results]
    D --> |total=0| F[empty_hint\n→ schema]
    F --> B
```

**Инструменты (6 штук):**
1. `grep_{entity}(pattern, ...)` — текстовый поиск (multi-token AND)
2. `filter_{entity}({field}__op, ...)` — точная фильтрация
3. `get_{entity}(id)` — по ID
4. `count_{entity}({field}__op...)` — количество
5. `distinct_{entity}(column)` — уникальные значения
6. `schema_{entity}()` — мета-информация о сущности (1 запрос вместо N)

**Подробно:** [doc/agents/search-strategies.md](doc/agents/search-strategies.md)
**MCP интеграция:** [doc/agents/mcp-session-lifecycle.md](doc/agents/mcp-session-lifecycle.md)
**DataSource абстракция:** [doc/agents/adapter-pattern.md](doc/agents/adapter-pattern.md)

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
3. [ ] `uv run pytest src/api_service/tests/unit/agent/ -v` — 151 agent-тестов (58 pipeline unit + 93 legacy)
4. [ ] `go test ./data-service/... ./helperium-go/...` — 690 тестов, зелёные
5. [ ] **LLM E2E** (только перед PR, дорогие по токенам):
    ```bash
    uv run pytest tests/e2e/llm/test_search_e2e.py -v -s
    ```
    ~50K токенов за прогон (4 вопроса). Не гонять каждый push.

## 📊 10. Monitoring & Observability

На проекте используеться Grafana + Prometheus
**Детали:** [doc/agents/monitoring.md](doc/agents/monitoring.md)

## 🧹 2k. Tool Abuse Prevention — защита от пустых/жадных LLM вызовов

### Проблема
LLM склонна вызывать инструменты с пустыми аргументами: `grep_products({})`. Если не заблокировать — дамп всей таблицы, перерасход, abuse.

### Решения (3 уровня защиты)

**Уровень 1 — JSON Schema Validation (MCP Gateway)**
- `grep_*` и `filter_*` имеют `pattern` с `required: true` + `minLength: 1`
- MCP гейтвей **отклоняет pre-request** если `pattern` отсутствует или пустой → `isError: true`
- Реализация: `Strategy.ToolParams()` задаёт `Required: &t`

**Уровень 2 — Server-side guard (data-service)**
- `grep.ParseRequest()` проверяет `pattern != ""` и `len(pattern) >= 1`, возвращает 400 при нарушении
- `grep.go`: `maxPatternLen=500`, `maxRegexLen=200`, `maxTokens=10` — защита от ReDoS
- `filter.go`: `maxFilterValueLen=200`, `maxInValues=50`, `parseFilterLimit=10`
- Санитизация ошибок БД: детали в лог, клиенту generic "Query execution failed"
- Per-query timeout: 30s (конфигурируется через `QUERY_TIMEOUT_SECONDS`)

**Уровень 3 — Empty Hints (schema tool)**
- При total=0 grep/filter возвращают `empty_hint` со structured подсказкой:
  ```json
  {
    "suggested_action": "Try schema_products() to discover available values",
    "available_values": {"brand": ["Brembo", "Bosch"]}
  }
  ```
- LLM видит подсказку и вызывает `schema_{entity}()` вместо циклических пустых попыток

### Security limits per strategy
| Strategy | Limits |
|----------|--------|
| `grep` | `maxPatternLen=500`, `maxRegexLen=200`, `maxTokens=10`, `maxFields=20` |
| `filter` | `maxFilterValueLen=200`, `maxInValues=50`, `maxFilters=15` |
| DataSource | `maxLimit=100`, statement timeout=30s, ReadOnlyDB (только SELECT) |

### Logging
- `stages.py`: логгирует `name`, `arguments`, `iteration` до/после/при ошибке
- `mcp_client.py`: логгирует `[MCP] Calling tool X with args=Y`, результат `[MCP] Tool X completed: N blocks, M chars`
- `server.py` SSE events: `token`/`audio` только в DEBUG; `tool_call`/`tool_result`/`final`/`error`/`done` — INFO

**Детали:** `data-service/internal/search/`, `data-service/internal/datasource/`

## ⚠️ Важные правила

- **Никакого SQL в Проекте** — только HTTP к data-service (либо генерация тестовой бд разрешаеться)
- **Виджет — основной клиент.** embed/embed.js — единственный production-ready UI. demo/web — для тестов
- **Generic-подход** — не хардкодить сущности в коде
- **Не кешировать MCP manifest** — всегда регенерировать через `configgen.GenerateMCPTools(cfg.Endpoints, ...)` (см. `mcp_manifest.go`)
- **`grep_*` тулы всегда с `required=['pattern']`** — защита от пустых вызовов
- **Тулы LLM: grep, filter, get, count, distinct, schema** — никаких `search_*`, `simple_*`, `find_*`, `list_*`, relationship
- **Пустой результат → empty_hint** — не зацикливаться, вызвать schema_{entity}() для discovery
- **DataSource interface** — фундамент для не-SQL бэкендов (CRM, NoSQL, API)
