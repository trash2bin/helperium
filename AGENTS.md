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
      каждое событие → SpendingMiddleware → TokenBudgetMiddleware
```

**Аудит api-service (2026-07-30):** Исправлены 12 проблем pipeline.
- ✅ Exception-safe history — `SaveHistoryStage.force_save()` вызывается в finally при ошибках
- ✅ Token budget pre-check — проверка ДО LLM call (LLMStage), а не после
- ✅ classify_error — type-based (isinstance) вместо substring matching, устранены false positives
- ✅ ErrorContext — все 5 stage'ов используют `with_stage()`, исправлен баг с immutable builder в ToolExecutionStage
- ✅ LiteLLM cost — извлекается из `response.usage.cost` / `_hidden_params`, SpendingMiddleware теперь работает
- ✅ Safety net — структурная проверка JSON вместо тупого `name+arguments in string`, устранены false positives
- ✅ Spending persistence — JSON-файл с atomic write, перезапуск не теряет данные
- ✅ Composite tenant spending — bad tenant не блокирует good tenant в multi-tenant запросе
- ✅ Unicode homoglyph guard — таблица кириллических/украинских омоглифов, guard не bypass-ится

**MCPClient audit (2026-07-30):**
- ✅ Circuit breaker — 3+ consecutive failures → skip reconnect, half-open после 30s cooldown
- ✅ TTL garbage collection — фоновый task закрывает SSE сессии idle > 10 мин
- ✅ ProviderPool TOCTOU — `remove_worker()` async с `_lock`, корректировка `_rr_index`

Protocol'ы: LLMProvider, ConversationStore, SpendingTracker, BacklogWriter, GuardChecker, MCPToolProvider (`agent/protocols.py`).

> Мёртвый `AntiAbuseChecker` protocol удалён. `BacklogWriter` — sync (не async) — осознанно (local file writes).

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
**Авто:** entities[], endpoints[], mcp_tools[], read_only: `true`. **Вручную:** custom_queries{}, auth{}, mcp_tools[].description, introspection{}.
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
Last verified: 2026-08-02 (HEAD `3aa1cdbc`; выпилка write-tool approval: удалены ApprovedTools/approve/pending из кода, UI и доков; 18/18 Go-пакетов, golangci-lint 0 issues, vitest 72/72, живой smoke-тест)
Следущая плановая: 2026-09-01 или после изменения config типов.
После любой правки документа — обновить дату и хеш коммита здесь.

OpenAPI-контракты admin-dashboard (2026-08-03, после HEAD `3aa1cdbc`): Gap A/B тесты + фикс DELETE — [admin-dashboard/README.md](admin-dashboard/README.md#openapi-контракты-и-прокси-2026-08-03).

Аудит-проход 2026-08-03 (после OpenAPI-контрактов): убран dead `replace data-service` из `admin-dashboard/go.mod`; доки синхронизированы: `openapigen` → `helperium-go/openapigen` (specs/README, data-service/README), config version 3→4 (data-service/README), ApprovedTool-упоминания вычищены (config-migration.md), configgen версия 2→4 в таблице, RAG admin-токен: `ADMIN_API_TOKEN` должен совпадать с `ADMIN_TOKEN` (docker-compose + .env.example + rag/README + api-flow), dead `RAG_ADMIN_TOKEN` убран из monitoring.md.

Аудит (doc/agents/data-service-refactor-audit.md): рой из 4 reviewer'ов + ручная верификация.
Исправлено (TDD, 28 новых тестов, 734 passed под -race):
- C1 CRITICAL deadlock: вложенный RLock в ServeHTTP → inst в контексте (tenantInstanceKey),
  /mcp/schema и /openapi.json читают из контекста, fallback на resolveTenant для прямых вызовов.
- C2 HIGH PG placeholder offset: tenantFilter с корректным existingArgCount во всех ветках strategy_handler.
- C3 HIGH format=count + tenant: AND к внутреннему WHERE вместо обёртки агрегата в подзапрос.
- C4 HIGH tenant после ORDER BY: insertTenantBeforeLimit вставляет перед первой из ORDER/LIMIT/OFFSET
  + перенумерация $N для PG.
- C5 HIGH legacy find/list удалены: CurrentConfigVersion 3→4, конфиги с find/list падают в Validate.
- C6 HIGH /stats fail-soft: битый counter логируется и пропускается, остальные считаются;
  Validate проверяет RowFilter.Where (isValidFilterExpression) и entity.
- M1 grep invert: Де Морган (AND↔OR) для multi-token/multi-field.
- M2 BuildFilter: ESCAPE '\\' добавлена.
- M3 searchable/filterable FieldRules enforced в runtime (grep stringFields, filter ParseRequest).
- M4 BuildCustomQuery: JSONB ?| ?& ? 'key', комментарии -- /* */, dollar-строки не трогаются.
- M5 sqlite: Exec-fallback прагм пропускается при явном _pragma= в DSN.
- M6 normalizeDateTime: миллисекунды и таймзона парсятся.
- M7 FieldRule.ID (стабильный) вместо Reason-prefix; миграция Reason→ID в normalizeV3ToV4;
  resolveFieldRules идемпотентен (custom-дефолты не дублируются).
LOW-фиксы (воркеры с toolBudget-block на read): L1 offset cap 100k, L2 tenant_id не течёт в ответ,
L3 SaveTenantSchema атомарный (temp+rename), L5 QuoteIdentifier контракт, L6 ReadOnlyDB Deprecated,
L7 DSN с ? документирован. Ручные фиксы: L8 PersistTenantConfig →
RegenerateAndPersistTenantConfig (честное имя, только тесты), L9 OpenAPI query-params (grep/filter/distinct),
L10 filter __like \ + ESCAPE задокументирован (filter.go + search-strategies.md).
Остался: L4 (HealthCheck closed conn — приемлемо).
Итого: 754 (data-service) + 114 (helperium-go) тестов, -race чистый.
Финальный review-рой (2026-08-01) нашёл 2 CRITICAL (sqlite regexp не зарегистрирован,
mode=ro + WAL ломает readonly_dsn), HIGH (OpenAPI sort_dir ghost), MEDIUM (admin-хендлеры
без ts.mu — race с ReloadTenant; ReloadTenant игнорирует DSN; M7 custom-дедупликация)
+ LOW doc-фиксы. Все исправлены TDD (R1-R9 в data-service-refactor-audit.md).
```

> **Knowledge graph workflow, Cookbook, skills, agent rules, decision tree** — смотри [.pi/APPEND_SYSTEM.md](.pi/APPEND_SYSTEM.md)
