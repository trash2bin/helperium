# SYSTEM APPEND — Agent rules, skills, docs & graph cookbook

**Дополнение к system prompt pi: как работать с этим проектом.**

---

## Связка файлов

| Файл | Роль |
|---|---|
| **AGENTS.md** | Техпаспорт: архитектура, data flow, сервисы, конфиг |
| **APPEND_SYSTEM.md** (этот) | Тулы, скиллы, граф знаний, decision tree |
| **doc/agents/*** | Deep dives по каждому аспекту |
| **service/README.md** | per-сервис: env vars, endpoints |

---

## Граф знаний — Cookbook

**Default project: `helperium`** — не указывать `project` в каждом вызове.

| Задача | Команды |
|---|---|
| Поиск символа | `codebase_memory_search_graph({ query: "MCPClient" })` |
| Трассировка вызовов | `codebase_memory_trace_path({ function_name: "qualified.name", direction: "both" })` |
| Что сломает изменение? | `codebase_memory_trace_path({ function_name: "qualified.Name", direction: "inbound" })` |
| Архитектура | `codebase_memory_get_architecture({ aspects: ["all"] })` |
| Обнаружение изменений | `codebase_memory_detect_changes({ scope: "." })` |
| Поиск по regex | `codebase_memory_search_code({ pattern: "tenant", file_pattern: "*.go" })` |
| Cypher (сложные запросы) | `codebase_memory_query_graph({ query: "MATCH (n)-[:HTTP_CALLS]->(m) RETURN n.name, m.name" })` |
| Чтение кода | `codebase_memory_get_code_snippet({ qualified_name: "..." })` |
| Индексация после правок | `codebase_memory_index_repository({ repo_path: ".", mode: "moderate" })` |

**Ограничения графа:** не видит .env, shell-скрипты, динамические HTTP-вызовы (SSE, runtime URL). Для HTTP-матрицы — `doc/api-flow.md`.

**Принцип:** граф → потом fallback на read. Большие выводы → ctx_execute/ctx_batch_execute.

---

## 📋 Скиллы

### codebase-memory
Индексированный граф (~5200 узлов, 24600 связей: вызовы, импорты, HTTP-каналы). Используй Cookbook выше.

### context-mode (ctx_*) — большие выводы
Любой вывод >1KB: `ctx_execute`, `ctx_execute_file`, `ctx_batch_execute`, `ctx_search`, `ctx_index`. Вместо read/bash.

### pi-subagents
**Делегировать когда:** >5 файлов / >10 тулов / нужен review / контекст >50% / параллельные задачи.
**Не делегировать:** 1 файл / быстрый lookup / <5 тулов / shared context.

| Ситуация | Шаблон |
|---|---|
| Требования неясны | `/gather-context-and-clarify` |
| Исследовать до реализации | `/parallel-research` |
| План есть, нужна реализация | `/parallel-handoff-plan` |
| Изменения готовы → верификация | `/parallel-review` |
| Fix-and-check loop | `/review-loop` |

**browser-debugger:** Firefox, ARIA snapshot, console/network — **не правит код**, fresh context.
**SSE сессии:** fresh context (не fork).

### git-commit
Только по явному запросу. Не пушит.

### api-service audit (2026-07-30)
Проведён глубокий аудит api-service через subagent'ы. Исправлено 12 проблем:

| # | Область | Фикс |
|---|---|---|
| 🔴 HIGH-1 | Pipeline exception safety | `SaveHistoryStage.force_save()` в finally при ошибке |
| 🔴 HIGH-3 | Dead protocol | Удалён мёртвый `AntiAbuseChecker` protocol из protocols.py |
| 🟡 MEDIUM | classify_error | Type-based (isinstance) вместо substring matching |
| 🟡 MEDIUM | ErrorContext coverage | Все 5 stage'ов + фикс immutable builder бага в ToolExecutionStage |
| 🟡 MEDIUM | Circuit breaker MCP | 3+ failures → skip reconnect, half-open после 30s |
| 🟡 MEDIUM | TTL GC MCP | Background task закрывает SSE сессии idle > 10 мин |
| 🟡 MEDIUM | ProviderPool TOCTOU | `remove_worker()` async + `_lock` + `_rr_index` correction |
| 🟡 MEDIUM | TokenBudget pre-check | Проверка ДО LLM call в LLMStage.run() |
| 🟡 MEDIUM | Spending persistence | JSON-файл с atomic write, перезапуск без потерь |
| 🟡 MEDIUM | Composite tenant DOS | Bad tenant не блокирует good tenant в multi-tenant запросе |
| 🟢 LOW | LiteLLM cost | Извлекается из response, SpendingMiddleware работает |
| 🟢 LOW | Safety net false positive | Структурная json.loads проверка, не тупой `in` |
| 🟢 LOW | Unicode homoglyph guard | Таблица омоглифов, guard не bypass-ится |

**Не воспроизводится:** HIGH-2 (backlog concurrent writes) — per-session lock стоит добавить при performance-тестах.

Все фиксы сделаны TDD: сначала падающие тесты, потом код. 60+ тестов покрывают эти кейсы.

### pi-intercom
Коммуникация между сессиями, передача контекста.

---

## 📚 Документация

**Принцип:** AGENTS.md (1 min) → service/README.md (1 min) → doc/agents/xxx.md (5 min)

### doc/agents/ (читать при работе по теме)

| Файл | Когда читать |
|---|---|
| `search-strategies.md` | Поиск, MCP-тулы, интроспекция |
| `mcp-session-lifecycle.md` | MCP-сессии рвутся, тулы не работают |
| `pipeline-architecture.md` | Pipeline api-service — Stage/Protocol/Middleware |
| `api-service-audit.md` | Аудит api-service: найденные и исправленные проблемы |
| `tenant-lifecycle.md` | Настройка/отладка tenant |
| `adapter-pattern.md` | Добавление нового типа БД |
| `http-clients.md` | Кросс-сервисные проблемы |
| `security-isolation.md` | Безопасность, tenant leaks |
| `anti-abuse.md` | Пустые/жадные LLM вызовы |
| `tool-call-safety-layers.md` | Утечка сырого JSON пользователю |
| `config-migration.md` | После изменения config типов |
| `web-service.md` | Web-роутинг |
| `operations.md` | Логи, дебаг, dev-скрипты |
| `testing-guide.md` | Написание/запуск тестов |
| `ci-cd.md` | CI/CD |
| `monitoring.md` | Grafana + Prometheus |
| `api-contracts.md` | Новые эндпоинты |

### Service README

| README | Когда читать |
|---|---|
| `api-service/README.md` | api-service (env, endpoints, troubleshooting) |
| `data-service/README.md` | data-service (search, skip rules, пакеты) |
| `mcp-gateway/README.md` | MCP (tools, composite, кэш манифеста, RAG) |
| `admin-dashboard/README.md` | Admin UI |
| `rag/README.md` | RAG/ChromaDB |
| `demo/web/README.md` | Dev-only reverse proxy |
| `agent-db/README.md` | Seedgen, e2e orchestration |
| `embed/README.md` | Widget API, Shadow DOM, CSP |
| `configgen/README.md` | Config generation, mcp tools |
| `specs/README.md` | Config schema |

### Decision tree: api-service — симптомы

| Симптом | Читать / Что проверять |
|---|---|
| История сессии не сохраняется при ошибке | `orchestrator.py` — `finally` блок должен вызвать `SaveHistoryStage.force_save()` |
| Spending не учитывается / всегда 0 | `litellm_provider.py` — `cost` извлекается из `_hidden_params['cost']` или `usage.cost` |
| Safety net блокирует легитимные JSON-ответы | `stages/llm.py::_looks_like_raw_json_tool_calls()` — структурная проверка |
| Guard не блокирует injection с омоглифами | `guardrails.py` — `HOMOGLYPH_MAP` применяется до regex |
| classify_error даёт неверную категорию | `error_messages.py` — type-based (isinstance) проверяет тип исключения до substring |
| ErrorContext.stage не заполнен | Stage должен вызвать `ctx.error_context = ctx.error_context.with_stage('name')` |
| MCP reconnect бесконечный при падении gateway | `mcp_client.py` — circuit breaker: 3+ failures → skip, half-open через 30s |
| SSE сессии не закрываются | `mcp_client.py` — `_cleanup_stale_connections()` + `start_gc()` фоновый cleaner |
| ProviderPool race / stale rr_index | `provider_pool.py` — `remove_worker()` async с `_lock`, корректировка `_rr_index` |

### Decision tree: симптом → что читать

| Симптом | Читать |
|---|---|
| 500 на /api/chat | api-service/README.md troubleshooting + web-service.md |
| SSE обрывается | mcp-session-lifecycle.md + mcp-gateway/README.md |
| LLM не вызывает тулы | search-strategies.md + system prompt |
| Все тулы пустые | anti-abuse.md §Empty Hints |
| Tenant не регистрируется | tenant-lifecycle.md + data-service/README.md |
| Неправильные данные | search-strategies.md + data-service/internal/query/ |
| Config rewrite — старые тулы | `InvalidateManifestCache()` в mcp-gateway |
| 403 / tenant isolation | security-isolation.md |
| Кросс-сервисная проблема | doc/api-flow.md |
| Новый тип БД | adapter-pattern.md |
| Изменение config типов | config-migration.md |
| Где логи смотреть? | operations.md → `./scripts/dev.sh logs` |
| Performance / metrics | monitoring.md + Grafana |

---

## 🧭 Community hubs — навигация по графу

| Community | Entry point | Что покрывает |
|---|---|---|
| API Proxy | `demo/web/server.py` | Reverse proxy routes |
| MCP Client | `mcp_client.py` | SSE → mcp-gateway |
| MCP Gateway Composite | `createCompositeServer()` | Tenant routing, composite |
| MCP Server Core | `main.go`, `sseSession` | SSE lifecycle, JSON-RPC |
| CRUD Handlers | `handlers/default.go` | List/find/get/custom_query |
| Middleware | `server/middleware.go` | TenantID, BodyLimit, Throttle |
| Agent Store | `agent_store.py` | SQLite agent registry |
| Tenant Storage | `tenant.go` | Lifecycle, hot-reload |
| SSE Formatting | `event_stream.py` | AgentEvent → SSE |
| MCP Registry | `tools/tools.go` | Tool registration |
| HTTP Client | `httpclient/client.go` | mcp-gateway → data-service |
| RAG DTOs/Endpoints/Cache | `rag/` | ChromaDB |
| API Flow | `doc/api-flow.md` | HTTP matrix (11 каналов) |
| AGENTS.md | `AGENTS.md` | Tech passport |
| doc/agents/ | `doc/agents/*` | Deep dives |

---

## ⚠️ Важные правила

- **Не грепать/глоббить классы** — codebase-memory
- **Не кешировать прочитанное** — перечитывать doc/agents/ по теме при каждом запросе
- **Не использовать raw Bash для >1KB** — ctx_execute / ctx_batch_execute
- **После правок доков — обновить AGENTS.md §Verification** (дата + хеш)
- **После изменения config типов — проверить config-migration.md**
- **Запрещено: SQL в коде** — только HTTP к data-service (тестовые БД — ок)
- **Тулы LLM: только grep, filter, get, count, distinct, schema** — не find/list/search/simple
