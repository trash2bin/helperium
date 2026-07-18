# AGENTS.md — Технический паспорт проекта

Краткий архитектурный контекст. Детали — в `doc/agents/*`.

## 🎯 1. О проекте

B2B SaaS: клиент подключает свою БД → платформа интроспектирует схему → автоматически генерирует REST API + MCP-инструменты → AI-агент отвечает на вопросы над данными.

### 🔄 Data flow: Запрос данных

```
Browser → GET /api/data/students [X-Tenant-ID: tenant-a]
  → demo-web (:8080) — прокси с X-Tenant-ID
    → data-service (:8084) — chi router → tenantStore.resolveTenant()
      → generic handler (get_by_id / find / list / custom_query)
        → QueryBuilder (без ORM, prepared statements, placeholder адаптация под СУБД)
          → Adapter.Conn.QueryContext → Client DB (SQLite/PG)
```

### 🔄 Data flow: LLM Chat (SSE stream)

```
Browser → POST /api/chat [X-Tenant-ID]
  → web (:8080) — SSE proxy побайтово
    → api-service (:8081)
      → chat_handler() → orchestrator.stream_events()
        → guard check → load history → LLM call (LiteLLM)
          → tool_call → MCPClient.call_tool() (если LLM вернул)
            → mcp-gateway (:8083) → data-service → DB
          → tool_result → следующий LLM call → final ответ
        → yield AgentEvent(type="token"|"tool_call"|"final"|...) → SSE → Browser
```

**Типы SSE-событий:** `token`, `tool_call`, `tool_result`, `final`, `error`, `done`.
**Ключевые файлы:** `api-service/src/api_service/agent/` — `orchestrator.py`, `event_stream.py`, `types.py`, `mcp_client.py`.

## 🏗️ 2a. MCP — Архитектура

1. **SSE-сессия** (GET /mcp): клиент открывает долгий SSE-стрим, получает `event: endpoint` с URL для POST
2. **POST /mcp/message?sessionId=...**: JSON-RPC → `mcpPostHandler()` → `mcpServer.HandleMessage()`
3. **Создание MCP-сервера**: `FetchConfigWithTenant()` → GET к data-service `/mcp/manifest` → `tools.NewRegistry(cfg)`
4. **Каждый инструмент** — closure с `httpClient.GetData()` к data-service

**Composite Mode** (один агент — N tenant'ов): заголовок `X-Tenant-ID: tenant-a,tenant-b` → `createCompositeServer()` → инструменты с префиксом `{tenantID}__find_catalog_product`. При 1 tenant — legacy-режим без префикса.

**Детали MCP Session Lifecycle:** [doc/agents/mcp-session-lifecycle.md](doc/agents/mcp-session-lifecycle.md)

## 🏗️ 2b. Tenant Lifecycle

**Создание:** admin API POST /admin/tenants + config.json, или bootstrap при старте.
**Rewrite:** POST /admin/config/rewrite → интроспекция БД → генерация конфига.
**Persistence:** `.data/tenants/{id}.json` — восстанавливаются при старте.
**Удаление:** DELETE /admin/tenants/{id} → graceful drain.

**Детали:** [doc/agents/tenant-lifecycle.md](doc/agents/tenant-lifecycle.md)

## 🏗️ 2c. Config — что генерируется/редактируется

**Авто:** entities[], endpoints[] (GET /{entity}/{id}, GET /{entity}), mcp_tools[], stats.counters[], read_only: true
**Вручную:** custom_queries{}, метод POST/PUT/DELETE, auth{}, mcp_tools[].description/display_name, introspection{}, approved_tools[], readonly_dsn

Структура: `helperium-go/config/types.go:Config`

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
| **data-service** (Go) | :8084 | Generic CRUD/Query, интроспекция БД, rewrite |
| **mcp-gateway** (Go) | :8083 | MCP SSE/JSON-RPC, composite инструменты |
| **admin-dashboard** (Go) | :8085 | Web UI для администрирования (Alpine.js) |
| **api-service** (Python) | :8081 | Оркестратор агента, LiteLLM, SSE-chat |
| **rag-service** (Python) | :8082 | Поиск по документам (ChromaDB) |
| **web** (Python) | :8080 | UI + reverse-proxy |
| **sdk** (Python) | — | Pydantic-модели и клиенты |
| **helperium-go** (Go) | — | Shared Go-модели |

### 🌐 Web Service Multi-Tenancy

**Детали:** [doc/agents/web-service.md](doc/agents/web-service.md)

## 🚀 3. Эксплуатация и разработка

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

1. [ ] `make ci` — зелёный
2. [ ] Pre-commit hooks — все Passed
3. [ ] `uv run pytest tests/e2e/ -v` — 44 e2e теста
4. [ ] Mutation score не упал (опционально)

## 📝 10. CLA

Проект использует CLA ([`CLA.md`](CLA.md)) — защита коммерческой модели. При первом PR бот CLA Assistant запрашивает подпись.

## 📊 11. Monitoring & Observability

**Детали:** [doc/agents/monitoring.md](doc/agents/monitoring.md)

## ⚠️ Важные правила

- **Никакого SQL в Python** — только HTTP к data-service
- **Generic-подход** — не хардкодить сущности в коде
- **Stateless** — сервисы не хранят сессию локально (кроме SQLite-кэша)
