# AGENTS.md — Технический паспорт проекта для AI-агентов

Этот документ является основной точкой входа для AI-агента. Он содержит архитектурный контекст, карту навигации и операционные инструкции, необходимые для внесения изменений в код без потери целостности системы.

## 🎯 1. О проекте и видении
**Проект**: Платформа для развертывания AI-агентов над произвольными базами данных клиентов.
**Текущий вектор**: Трансформация из доменного решения (один вуз) в **Generic B2B SaaS**.

**Ключевая идея**: Клиент подключает свою БД $\rightarrow$ Платформа интроспектирует схему $\rightarrow$ Автоматически генерируется REST API и MCP-инструменты $\rightarrow$ AI-агент получает доступ к данным без написания кода под каждую БД.

### 🔄 Архитектурный Pipeline — Как это работает на практике

#### 1. Data flow: Запрос данных (GET /api/data/students)

```
Browser / Agent
  │
  │ GET /api/data/students
  │ X-Tenant-ID: tenant-a
  ▼
demo-web (Python, :8080)
  │
  │ httpx.AsyncClient → проксирует запрос в data-service
  │ headers: X-Tenant-ID, X-Request-ID, Forwarded
  │ Извлекает X-Tenant-ID из запроса (может быть из заголовка или из URL /api/tenant/{id}/...)
  ▼
data-service (Go, :8084)
  │
  │ 1. chi Router → middleware извлекает tenantID:
  │     - Из X-Tenant-ID заголовка
  │     - Или из ?tenant= query-параметра
  │    Без tenantID → 404 tenant_not_found (strict mode)
  │
  │ 2. TenantStore.resolveTenant(tenantID)
  │     → находит TenantInstance{Config, Conn, Router} в мапе
  │
  │ 3. Handler (generic — нет захардкоженной бизнес-логики):
  │     - get_by_id:    GET /{entity}/{id}
  │     - find:         GET /{entity}?search=... (поиск по name/title полю)
  │     - list:         GET /{entity}
  │     - custom_query: GET /{entity}/{id}/custom (whitelist SQL)
  │
  │ 4. Generic Query Builder:
  │     - Без ORM — все запросы собираются из строк через Builder
  │     - Placeholder'ы адаптируются под СУБД (SQLite='?', PG='$1')
  │     - Идентификаторы квотируются через QuoteIdentifier
  │     - Prepared Statements (без raw SQL concatenation — injection safe)
  │
  │ 5. Adapter.Conn.QueryContext → SQL → Client DB
  ▼
Client Database (SQLite / PostgreSQL / будущие адаптеры)
```

#### 2. Data flow: LLM Chat (как агент отвечает пользователю)

```
Browser (EventSource)
  │
  │ POST /api/chat  (SSE stream)
  │ Body: {"message": "покажи товары Bosch", "session_id": "abc"}
  │ X-Tenant-ID: tenant-a
  ▼
demo-web (:8080)
  │
  │ async def proxy_chat() → проксирует на api-service
  │ httpx.AsyncClient → stream=True → SSE проксируется побайтово
  ▼
api-service (Python, :8081)
  │
  │ 1. chat_handler():
  │     - Парсит X-Tenant-ID в tenant_ids: list[str]
  │     - Создаёт effective_session_id = f"direct:{session_id}"
  │     - Возвращает StreamingResponse(events(), media_type="text/event-stream")
  │
  │ 2. events() → async for event in agent.stream_events(...):
  │
  │ 3. LLMAgent.stream_events():
  │     - Открывает lock сессии (conversation_manager)
  │     - Вызывает _run_turn():
  │       a. Guard → проверка prompt injection
  │       b. Load history → conversation_manager.load_history()
  │       c. Build system prompt → Persona + RAG context (если есть)
  │       d. LLM call → через LiteLLM (OpenAI, Claude, локальные модели)
  │       e. Если LLM вернул tool_call → MCPClient.call_tool()
  │       f. Tool result → следующий LLM call (цикл до финального ответа)
  │       g. Save history
  │       h. yield AgentEvent(type="token" | "tool_call" | "tool_result" | "final" | ...)
  │
  │ 4. MCPClient.call_tool(tool_name, args):
  │     - Ищет/создаёт SSE сессию к mcp-gateway (одну на tenant)
  │     - Отправляет JSON-RPC через MCP Python SDK (sse_client)
  │     - Получает результат → возвращает LLM
  ▼
mcp-gateway (Go, :8083)
  │
  │ 1. POST /mcp/message?sessionId=...
  │     - mcpPostHandler находит SSE сессию
  │     - Или создаёт временный MCP-сервер для stateless вызова
  │
  │ 2. mcpServer.HandleMessage(rawMessage)
  │     - Маршрутизирует по имени инструмента
  │     - Для composite: {tenantID}__find_catalog_product → вызывает closure
  │       с X-Tenant-ID: {tenantID} в http-клиенте
  │
  │ 3. Tool handler → httpClient.GetData() → GET /catalog_product?search=Bosch
  │     с X-Tenant-ID из closure
  ▼
data-service (:8084)
  │
  │ → resolveTenant → TenantStore → Query Builder → SQL → DB
  │ ← JSON response
  ▼
  Client Database
```

#### 3. SSE Streaming — Детальный протокол

**Как ответ LLM доходит до браузера:**

1. **Web UI** → открывает `EventSource('/api/chat')` (на самом деле POST с stream)
2. **api-service** → `StreamingResponse(events(), media_type="text/event-stream")`
3. **Оркестратор** (`_run_turn()`) → yield `AgentEvent`:
   - `type="token"` → `data: {"type": "token", "text": "текст"}`
   - `type="tool_call"` → `data: {"type": "tool_call", "tool": "find_catalog_product", ...}`
   - `type="tool_result"` → `data: {"type": "tool_result", ...}`
   - `type="final"` → `data: {"type": "final", "content": "..."}`
   - `type="error"` → `data: {"type": "error", "text": "..."}`
   - Финальный: `data: {"type": "done"}`
4. **Web UI** → парсит SSE и апдейтит DOM

**Ключевые файлы:**
- `api-service/src/api_service/server.py` — `chat_handler()`, `_sse()`, `_event_payload()`
- `api-service/src/api_service/agent/orchestrator.py` — `stream_events()`, `_run_turn()`
- `api-service/src/api_service/agent/event_stream.py` — `format_sse_event()`, `unstreamed_suffix()`
- `api-service/src/api_service/agent/types.py` — `AgentEvent`, `AgentEventType`
- `demo/web/server.py` — `_proxy_to_api()` (SSE проксирование)

---

## 🏗️ 2a. MCP — Архитектура Model Context Protocol

### Как mcp-gateway генерирует инструменты

1. **SSE-сессия** (GET /mcp): клиент открывает долгий SSE-стрим, получает `event: endpoint` с URL для POST
2. **POST-сообщение**: клиент шлёт JSON-RPC на `/mcp/message?sessionId=...`
3. **mcpPostHandler()**:
   - Загружает/создаёт MCP-сервер для данного tenant/session
   - Вызывает `mcpServer.HandleMessage(rawMessage)`
4. **Создание MCP-сервера** (`createServerForTenant` / `createCompositeServer`):
   - `httpClient.FetchConfigWithTenant(tenantID)` → GET к data-service `/mcp/manifest`
   - `tools.NewRegistry(cfg)` → конвертирует `mcp_tools[]` из конфига в MCP-инструменты
   - `registry.RegisterAll(mcpServer)` → регистрирует хендлеры
5. **Каждый инструмент** — closure, который при вызове делает `httpClient.GetData()` к data-service

### Composite Mode (один агент — N tenant'ов)

- MCPClient (Python) открывает **одну SSE-сессию** с `X-Tenant-ID: tenant-a,tenant-b`
- mcp-gateway (`resolveTenantIDs()`) парсит через запятую
- Если tenant'ов > 1 → `createCompositeServer()`:
  - Для каждого tenant'а загружает конфиг и создаёт Registry с префиксом `{tenantID}__`
  - Инструменты регистрируются как `"tenant-a__find_catalog_product"`, `"tenant-b__find_products"`
  - Хендлер имеет closure с tenantID → каждый вызов идёт в data-service с этим X-Tenant-ID
  - Если tenant 1 → legacy-режим (без префикса)

**Ключевые файлы:**
- `mcp-gateway/cmd/main.go` — `sseHandler()`, `mcpPostHandler()`, `createCompositeServer()`, `resolveTenantIDs()`
- `mcp-gateway/internal/tools/client.go` — маппинг MCP-инструментов
- `mcp-gateway/internal/tools/tools.go` — `NewRegistry()`, `NewPrefixedRegistry()`, `RegisterAll()`
- `api-service/src/api_service/agent/mcp_client.py` — `MCPClient._open_connection()`

---

## 🏗️ 2b. Tenant Lifecycle — Полный цикл

### Создание tenant'а (три способа)

**Способ 1: Через admin API**
```bash
POST /admin/tenants
Authorization: Bearer $ADMIN_TOKEN
{
  "id": "autoparts",
  "config": {
    "version": 1,
    "data_source": {
      "driver": "postgres",
      "dsn": "postgres://user:pass@host:5434/db?sslmode=disable"
    },
    "entities": [],
    "endpoints": []
  }
}
```
→ `adminAddTenantHandler()` → `AddTenant()` (коннект к БД + создание роутера) → `SaveTenantConfig()` (пишет `.data/tenants/{id}.json`)

**Способ 2: Bootstrap при старте**
- Из `--config` или `$DS_CONFIG` → загружается как tenant `"default"`
- Из `$TENANTS_DIR` (.data/tenants/) → все .json файлы восстанавливаются как tenant'ы

**Способ 3: Через agent-db CLI**
```bash
uv run agent-db register autoparts sqlite-testseed
```
→ POST /admin/tenants с config.json из scenario

**Способ 4: Через e2e helpers** (рекомендуется для CI/тестов)
```python
from tests.e2e.helpers import register_tenant, seed_database

seed_database(db_path, seed_path, project_root)
result = register_tenant("autoparts", config)
```

### Rewrite — Автогенерация конфига из БД
```bash
POST /admin/config/rewrite
X-Tenant-ID: autoparts
Authorization: Bearer $ADMIN_TOKEN
```
→ `adminRewriteHandler()`:
1. `adapter.Connect(ctx, cfg.DSN)` → коннект к БД tenant'a
2. `adapter.Introspect(ctx, conn)` → читает схему (таблицы, колонки, PK, FK)
3. `configgen.Generate(schema, dsConfig, nil)` → генерирует Config с entities, endpoints, MCP tools
4. `SaveTenantConfig()` → пишет `.data/tenants/{id}.json`
5. `ReloadTenant(ctx, id, path)` → разрыв соединения → пересоздание роутера (без даунтайма)

**Что configgen генерирует:**
```
Для каждой таблицы:
  → entity {name, table, fields[], id_column}
  → endpoint GET /{entity}/{id}  (get_by_id)  [если есть PK]
  → endpoint GET /{entity}        (find)       [если есть name/name/title поле]
  → counter для /stats
Для всех:
  → endpoint GET /health  (builtin_health)
  → endpoint GET /stats   (builtin_stats)
  → mcp_tools[] из endpoints (get_{entity}, find_{entity})
  → data_source.read_only = true  (write-доступ по умолчанию выключен)
```

### Persistence — Tenant переживает рестарт
```
.data/tenants/
├── autoparts.json    # сохранён через SaveTenantConfig()
├── default.json      # bootstrap tenant
└── shop.json         # добавлен через admin API

При старте:
1. os.ReadDir(.data/tenants/) → загружает каждый .json
2. config.Load() → валидация JSON Schema
3. store.AddTenant() → коннект к БД + создание роутера
```

### Удаление tenant'а
```bash
DELETE /admin/tenants/{id}
Authorization: Bearer $ADMIN_TOKEN
→ graceful drain: закрыть пул, удалить из мапы, стереть config с диска
```

---

## 🏗️ 2c. Config — что генерируется, что редактируется вручную

### Автоматически генерируется (configgen.Generate)

| Поле | Описание | Совет |
|---|---|---|
| `entities[]` | Все таблицы, PK, FK, колонки | Оставить как есть |
| `endpoints[]` | GET /{entity}/{id} + GET /{entity} для каждой | Можно добавить вручную DELETE/POST/PUT |
| `mcp_tools[]` | get_{entity}, find_{entity} для LLM | Оставить — описания для модели |
| `stats.counters[]` | Счётчики для /stats | Оставить |
| `data_source.read_only` | Всегда true | Менять на false через admin API |

### Можно/нужно дописать вручную

| Что | Как |
|---|---|
| `custom_queries{}` | Запросы с JOIN, агрегаты, отчёты. Пишется SELECT вручную под бизнес-логику |
| `endpoints[].method: POST/PUT/DELETE` | Write-операции. Нужны для мутации данных |
| `auth{}` | Row-level isolation (multi-tenant в одной БД) |
| `mcp_tools[].description` | Уточнить описание для LLM (на английском, контекстно) |
| `mcp_tools[].display_name` | Человекочитаемое имя для UI (русский). Пусто → fallback к `name`. Настраивается в админке |
| `introspection{}` | IncludeSchemas, ExcludeTables для фильтрации при rewrite |
| `approved_tools[]` | Список write-эндпоинтов, разрешённых даже в read_only-режиме |
| `data_source.readonly_dsn` | Database-level read-only (отдельный PG юзер) |

### Чего нет в автогенерации
- **Relations** (связи между entities) — приходится писать custom_queries или дописывать вручную
- **exclude_tables** — current session не скипает django_*, pg_* таблицы (нужно передавать skipPrefixes в Generate)
- **Collapsed entities** — 2 таблицы → 1 entity (например, orders + order_items как один nested entity)

**Структура конфига:** `helperium-go/config/types.go:Config` — 256 строк, всё документировано.

---

## 🔌 2d. Adapter Pattern — Добавление новой СУБД (MySQL, MSSQL и т.д.)

Чтобы добавить поддержку MySQL, нужно реализовать интерфейс `datasource.Adapter`:

### Шаг 1: Написать адаптер

```go
// Файл: data-service/internal/datasource/mysql_adapter.go
package datasource

import (
    "context"
    "database/sql"
    "fmt"
    _ "github.com/go-sql-driver/mysql"  // MySQL driver
    "strings"
)

type MySQLAdapter struct{}

func (MySQLAdapter) Driver() string { return "mysql" }

func (MySQLAdapter) Connect(ctx context.Context, dsn string) (Conn, error) {
    // 1. sql.Open("mysql", dsn)
    // 2. conn.SetMaxOpenConns, SetMaxIdleConns, SetConnMaxLifetime
    // 3. PingContext
    // 4. return &MySQLConn{conn}, nil
}

// MySQL: ? (positional, same as SQLite)
func (MySQLAdapter) TranslatePlaceholder(index int) string { return "?" }

// MySQL: `backtick` quoting
func (MySQLAdapter) QuoteIdentifier(name string) string {
    if strings.Contains(name, ".") {
        parts := strings.Split(name, ".")
        for i, p := range parts {
            parts[i] = "`" + p + "`"
        }
        return strings.Join(parts, ".")
    }
    return "`" + name + "`"
}

func (MySQLAdapter) Introspect(ctx context.Context, database Conn) (*Schema, error) {
    // 1. SHOW TABLES — список таблиц
    // 2. SHOW COLUMNS FROM {table} — колонки
    // 3. SHOW CREATE TABLE — PK, FK
    // 4. Маппинг типов: VARCHAR→TypeString, INT→TypeInt, DATETIME→TypeDatetime
    // (или через information_schema для консистентности с Postgres)
}
```

### Шаг 2: Зарегистрировать в NewDefaultRegistry

```go
// Файл: data-service/internal/datasource/registry.go
func NewDefaultRegistry() *Registry {
    r := NewRegistry()
    r.Register(SqliteAdapter{})
    r.Register(PostgresAdapter{})
    r.Register(MySQLAdapter{})  // <-- добавить
    return r
}
```

### Шаг 3: Добавить driver в валидацию Config

```go
// Файл: helperium-go/config/types.go
const (
    DriverSQLite   Driver = "sqlite"
    DriverPostgres Driver = "postgres"
    DriverMySQL    Driver = "mysql"     // <-- добавить
)

func (d Driver) Valid() bool {
    switch d {
    case DriverSQLite, DriverPostgres, DriverMySQL:  // <-- добавить
        return true
    }
    return false
}
```

### Шаг 4: Обновить JSON Schema

```json
"driver": {
    "type": "string",
    "enum": ["sqlite", "postgres", "mysql"]  // <-- добавить
}
```

### Шаг 5: Протестировать

```bash
# Unit-тесты адаптера
go test ./data-service/internal/datasource/ -run TestMySQLAdapter_*

# E2E: зарегистрировать tenant с MySQL DSN, запустить rewrite
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"id":"test","config":{"version":1,"data_source":{"driver":"mysql","dsn":"mysql://user:pass@host:3306/db"},"entities":[],"endpoints":[]}}' \
  http://localhost:8084/admin/tenants

curl -X POST -H "X-Tenant-ID: test" -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8084/admin/config/rewrite
```

**Что нужно реализовать (интерфейс Adapter):**
- `Driver() string` — вернуть "mysql"
- `Connect(ctx, dsn) (Conn, error)` — открыть соединение, PingContext
- `Introspect(ctx, conn) (*Schema, error)` — прочитать схему, вернуть generic описание
- `TranslatePlaceholder(index int) string` — вернуть "?" (MySQL) или "$1" (PG)
- `QuoteIdentifier(name string) string` — ` (backtick для MySQL, [brackets] для MSSQL)

**Весь существующий код** (runtime handlers, query builder, admin API, MCP tools) **не требует правок**
— он работает через интерфейс Adapter.

---

## 📦 2e. HTTP Client Layer — Как сервисы общаются между собой

### mcp-gateway → data-service

`mcp-gateway/internal/tools/client.go`:
- `FetchConfigWithTenant(tenantID)` → GET `http://data-service:8084/mcp/manifest?tenant={id}`
- `GetData(tenantID, path, params)` → GET `http://data-service:8084/{path}?{params}` с `X-Tenant-ID`
- Использует `http.Client` без промежуточного кэширования (stateless)
- При ошибке data-service возвращает JSON с `{"error": "..."}`

### api-service (MCPClient) → mcp-gateway

`api-service/src/api_service/agent/mcp_client.py`:
- `MCPClient` держит **один persistent SSE-сеанс** на tenant (один GET /mcp + одна очередь POST)
- Использует `mcp.client.sse.sse_client()` из официального Python MCP SDK
- Lock `asyncio.Lock` на сессию — последовательная отправка (SSE writer thread-unsafe)
- Timeout: `CALL_LOCK_TIMEOUT = 30s`, `sse_read_timeout = 30 min`
- При ошибке — переоткрытие сессии

### demo-web → все сервисы

`demo/web/server.py`:
- `httpx.AsyncClient` с `timeout=60s`
- `_proxy_to_api()` — SSE streaming (побайтово) через `response.aiter_bytes()`
- `_proxy_to_data_service()` — JSON через `response.json()`
- Прокидывает `X-Tenant-ID`, `X-Request-ID` (uuid4)
- `Forwarded: host=...`, `User-Agent: demo-web-proxy`

---

## 🔐 2f. Tenant Isolation — Три уровня

### 1. Database-level (data-service)
- Каждый tenant — отдельный SQLite файл или отдельная PG схема/БД
- `X-Tenant-ID` → выбор пула коннектов в `TenantStore.tenants[id]`
- Нет единой таблицы с tenant_id (физическая изоляция)

### 2. Tool-level (mcp-gateway)
- В composite режиме: `tenant-a__find_catalog_product` — префикс привязывает инструмент к tenant'у
- Даже если клиент укажет `X-Tenant-ID: tenant-a,tenant-b` → вызов `tenant-a__find_catalog_product` идёт строго в data-service с `X-Tenant-ID: tenant-a`
- Инструменты tenant-c не существуют в этой SSE-сессии

### 3. Session-level (api-service)
- `tenant_ids: list[str]` передаётся от web → orchestrator → MCPClient
- Если tenant не указан в заголовке, его данные и инструменты недоступны

**Верификация изоляции:**
- `pytest tests/e2e/test_data_isolation.py -v` — data-level: tenant-a не видит БД tenant-b
- `pytest tests/e2e/test_mcp_dynamic.py -v` — tool-level: tenant-shop не может вызвать инструмент tenant-uni
- `pytest tests/e2e/test_mcp_composite.py -v` — composite routing: tenant-uni__list_student идёт строго в data-service tenant-uni

---

## 🧵 2g. MCP Session Lifecycle (Детально)

```
GET /mcp (клиент)
  │
  │ 1. sseHandler():
  │    - Проверка лимита сессий (MaxSessions = 1000)
  │    - Создание sseSession{sessionID, writer, flusher, tenantIDs}
  │    - Отправка `event: endpoint\ndata: http://.../mcp/message?sessionId={id}\r\n\r\n`
  │    - Запуск idle-таймера (SessionIdleTimeout = 5m)
  │
  │ 2. POST /mcp/message?sessionId={id} (любой момент в будущем)
  │    - mcpPostHandler():
  │      a. Загружает SSE-сессию из sync.Map
  │      b. session.ensureCompositeServer(tenantIDs)
  │         → lazy init: createCompositeServer() → FetchConfig() → NewPrefixedRegistry()
  │         → или reuse: если tenant'ы те же, возвращает готовый mcpServer
  │      c. mcpServer.HandleMessage(ctx, rawMessage) — JSON-RPC dispatch
  │      d. Response пишется в SSE-стрим как `event: message
data: {...}\n\n`
  │      e. POST возвращает 202 Accepted (сам ответ пришёл по SSE)
  │
  │ idle-таймер сработал → session.isExpired() → удаление из sync.Map
  │
  │ r.Context().Done() (клиент закрыл SSE) → defer delete from sync.Map
```

---

## 📝 2h. Write-Tool Approval Flow

По умолчанию все сгенерированные конфиги имеют `read_only: true`.
Для включения write-операций:

1. **Вручную** в config.json: `"data_source": { ..., "read_only": false }`
2. **Через admin API:** `PUT /admin/tenants/{id}/config` с обновлённым конфигом
3. **Approval отдельных тулов:** `POST /admin/tools/{toolName}/approve`
   - Добавляет path в `approved_tools[]`
   - Тулы с операциями INSERT/UPDATE/DELETE появляются в MCP-манифесте
   - Без approval write-тулы скрыты от LLM

---

## ~~🧪 2i. Config Schema Validation~~

**Валидация конфига переехала из внешнего JSON Schema в Go-типы**

Файл `config.schema.json` **удалён из репозитория**. Валидация происходит
в `helperium-go/config/types.go` — метод `Config.Validate()`:

- Проверяет enum'ы: driver (`sqlite`/`postgres`), op, field type, param type, auth strategy
- Проверяет required поля: version, data_source.driver, data_source.dsn
- Проверяет cross-entity ссылки: endpoint → entity, custom_query → query_id, mcp_tool → endpoint
- **Никакого внешнего файла не требуется.**

---

## ⚡ 2j. Rate Limiting & Anti-Abuse

### mcp-gateway
- `mcpRateLimitMiddleware()` — per-IP лимит на POST запросы
- Сессионный лимит: MaxSessions = 1000 (OOM protection)
- Idle timeout: 5 минут, Max lifetime: 30 минут

### api-service
- TokenBucket: per-сессия (RPS + burst из `ABUSE_RPS`, `ABUSE_BURST`)
- UA-block: curl, wget, python-requests, Go-http-client
- Message limits: max 2000 chars, min 1s interval, 50 msg/session
- Repeated text: >3 повторов → блокировка
- Emergency presets: Normal / Cautious / Lockdown
- Prompt injection guard: `GuardChecker.check_input()`

---

## 🛠️ 2. Карта сервисов и навигация
Каждый сервис независим и общается по HTTP. Для детального изучения архитектуры каждого модуля используйте ссылки ниже.

| Сервис | Порт | Ответственность | Документация |
|---|---|---|---|
| **Data-service** (Go) | `:8084` | Generic CRUD/Query прокси. Интроспекция БД, генерация конфига, config hot-reload, write-tool approval flow, adapter registry. | [README](data-service/README.md), [configgen](data-service/internal/configgen/README.md) |
| **MCP-gateway** (Go) | `:8083` | MCP сервер (SSE/JSON-RPC). Динамическая генерация инструментов из data-service. Composite-режим для N tenant'ов. | [README](mcp-gateway/README.md) |
| **Admin Dashboard** (Go) | `:8085` | Веб-интерфейс для администрирования: tenant CRUD, конфиги, тулы, RAG, агенты. Alpine.js UI. Зависит от data + api. | [README](admin-dashboard/README.md) |
| **RAG** (Python) | `:8082` | Поиск по документам (ChromaDB), чанкинг, эмбеддинги (local/LiteLLM), кэш (Local/Redis), admin config API, Prometheus метрики, re-embedding pipeline. | [README](rag/README.md) |
| **API** (Python) | `:8081` | Оркестратор агента, LiteLLM, Agent Store (CRUD), rate limiter, MCPClient, управление сессиями и бэклогом, SSE-chat. Встраиваемый чат-виджет. | [AGENT_WORKFLOW](api-service/README.md) |
| **Web** (Python) | `:8080` | UI-интерфейс + reverse-proxy. Проксирует `X-Tenant-ID`, SSE-streaming, tenant routing. | [README](demo/web/README.md) |
| **SDK** (Python) | — | Общие Pydantic-модели и клиенты для сервисов. | [pyproject.toml](helperium-sdk/pyproject.toml) |
| **helperium-go** (Go) | — | Shared Go-модели (config types, CORS, metrics, swagger). | [types.go](helperium-go/config/types.go) |

> **Мониторинг (v1.1.0):** Все сервисы отдают Prometheus-метрики на `/metrics`.
> См. [секцию 10](#-10-monitoring--observability): Prometheus (:9090) + Grafana (:3000) с предустановленным дашбордом (18 панелей).

### 🚩 Глобальные документы

- **Стратегия**: [doc/FINAL_TASK.md](doc/FINAL_TASK.md) — план к pre-final версии и критерий готовности.
- **Конфигурация**: [.env.example](.env.example) — 85 переменных окружения (~150 строк).
- **API-контракты и config schema**: [specs/README.md](specs/README.md) — OpenAPI specs, JSON Schema валидация конфига data-service.
- **Agent Store**: [api-service/src/api_service/agent_store.py](api-service/src/api_service/agent_store.py) — SQLite-регистр агентов с CRUD API.
  - Таблица `global_config` (`agents.sqlite`) — key-valueystore для глобальных настроек (напр. `key="voice"` → JSON voice config).
  - Методы: `get_global_config(key)`, `set_global_config(key, value)` в `SqliteAgentRepository`.
- **Voice Config**: Хранится в `agents.sqlite` → `global_config` (key=`voice`), а не в отдельном JSON-файле.
  - Путь к БД: `AGENT_DB_PATH` env var (fallback: `<session_db_dir>/agents.sqlite`).
- **LLM Provider Prefixes**: `create_prioritized_client()` использует `KNOWN_PROVIDERS` из `litellm.provider_list` (динамически) вместо хардкода. Префикс модели добавляется автоматически если отсутствует (напр. `mistral/mistral-medium`). api_key передаётся как-is (пустая строка допустима для Ollama/локальных моделей).

### 🌐 Web Service — Multi-Tenancy Architecture

Web-сервис (`demo/web/server.py`) — тонкий reverse-proxy с поддержкой multi-tenancy:

**Два режима маршрутизации:**

1. **Стандартный (через заголовок `X-Tenant-ID`):**
   ```
   Browser → GET /api/data/students (X-Tenant-ID: tenant-a)
          → web:8080
          → data-service:8084/students (X-Tenant-ID: tenant-a)
   ```

2. **Явный tenant в URL (демо-режим):**
   ```
   Browser → GET /api/tenant/tenant-a/data/students
          → web:8080
          → data-service:8084/students (X-Tenant-ID: tenant-a)
   ```

**Ключевые маршруты:**
- `GET /api/manifest` → data-service `/mcp/manifest` (с tenant)
- `GET /api/data/{entity}` → data-service `/{entity}` (students, teachers, disciplines...)
- `GET /api/data/stats` → data-service `/stats`
- `GET /api/rag/documents` → rag-service `/documents/list`
- `GET/POST /api/chat` → api-service `/api/chat` (SSE)
- `GET /embed/{path}` → прокси на api-service `/embed/{path}` (виджет: [api-service/embed/README.md](api-service/embed/README.md))
  > **⚠️ После изменений в `api-service/embed/src/` или `api-service/embed/css/` нужно:**
  > ```bash
  > cd api-service/embed && npm run build  # typecheck + esbuild → dist/embed.js + dist/embed.css
  > ./scripts/dev.sh restart api            # api-service монтирует embed/dist/
  > ```
  > Без `restart api` api-service отдаёт старый JS (кеш в памяти).
- `GET/POST /api/tenant/{tenant_id}/{path:path}` — универсальный маршрут:
  - `data/{entity}` → data-service
  - `rag/{path}` → rag-service
  - `api/{path}` / `chat` → api-service (SSE для chat)

**Тесты:**
```bash
uv run pytest demo/web/tests/unit/ -v  # 50 тестов (22 proxy + 4 urls + 24 CORS)
uv run agent-db e2e-full               # полный e2e пайплайн
```

---

## 🚀 3. Эксплуатация и разработка (Manual)

### 🛠️ Нативный запуск: `scripts/dev.sh`
Скрипт `dev.sh` — основная точка управления в среде Mac/Linux.

**Управление сервисами:**
- `./scripts/dev.sh start` — поднять весь стек в правильном порядке (data $\rightarrow$ rag $\rightarrow$ mcp $\rightarrow$ admin $\rightarrow$ api $\rightarrow$ web).
- `./scripts/dev.sh stop` / `restart` / `status` — управление жизненным циклом.
- `./scripts/dev.sh logs {service|all}` — просмотр логов из `.data/logs/`.

### 🐳 Docker-запуск
Если нативная среда недоступна или требуется изоляция:
- `docker compose up -d` — запуск всех 7 core-сервисов в Dev-режиме.
- `docker compose --profile prod up -d` — запуск с Caddy (HTTPS через Let's Encrypt) для Production.
- `docker compose build` — пересборка образов после изменений в Dockerfile.
- **Тома**: Данные хранятся в `./.data/` (БД, индексы ChromaDB, кэш моделей).

### 🗄️ Работа с данными и сценариями (Критично для тестов)

Seed generation вынесен из `data-service/internal/seedgen/` (Go) в `agent-db/agent_db/seedgen/` (Python).
`data-service --materialize` и `cmd/seed-cli/` удалены.

#### Python seedgen (рекомендуется)

```python
from agent_db.seedgen import materialize, generate_ddl, apply, TestSeed

# Создать БД из сценария
cfg = materialize("data-service/testdata/scenarios/sqlite-testseed", force=True)

# Или напрямую в SQLite
import sqlite3
conn = sqlite3.connect(":memory:")
apply(conn, TestSeed)

# Сгенерировать DDL из описания сущностей
ddl = generate_ddl(entities, "sqlite")
```

Быстро накидать свою БД:
```bash
mkdir -p agent-db/scenarios/mydb
cp specs/config.example.json agent-db/scenarios/mydb/config.json
# правишь config под свою схему, создаёшь seed.json с данными
uv run --package agent-db python3 -c "from agent_db.seedgen import materialize; materialize('agent-db/scenarios/mydb', force=True)"
curl -X POST http://127.0.0.1:8084/admin/tenants -H "Authorization: Bearer secret" ...
```

#### agent-db CLI (legacy)
- `uv run agent-db register <tenant_id> <scenario>` — зарегистрировать тенанта (вызывает Python seedgen под капотом)
- `uv run agent-db tenants` — список активных тенантов
- `uv run agent-db drop <scenario>` — удалить БД сценария

#### pytest e2e (рекомендуется)
Новые тесты в `tests/e2e/` — модульные, с Python seedgen для генерации БД:
```bash
# Все e2e (с LLM) — 52 теста, ~15 сек
uv run pytest tests/e2e/ -v

# Без LLM — 48 тестов, ~5 сек
uv run pytest tests/e2e/ -v --ignore=tests/e2e/llm

# Traceback выключить
uv run pytest tests/e2e/ --no-traceback

# Отдельные модули
uv run pytest tests/e2e/test_admin_lifecycle.py -v
uv run pytest tests/e2e/test_agents.py -v

# LLM-тесты (требуют MISTRAL_API_KEY из .env)
uv run pytest tests/e2e/llm/ -v
```

---

## 🧪 4. Регрессионное тестирование
Перед коммитом или после правок **обязательно** проверить следующие уровни:

### 1. Python Unit/Integration тесты
```bash
uv run pytest rag/tests/                   # RAG (индексация, поиск, pipeline, repository) — 108 тестов
uv run pytest api-service/src/api_service/tests/              # API (OpenAPI spec, guardrails, sessions, spending) — 262 теста
uv run pytest demo/web/tests/              # Web (73 теста: proxy, CORS, URL mapping)
uv run pytest demo/tests/                  # Settings (18 тестов конфигурации из env)
uv run pytest helperium-sdk/tests/       # SDK модели и seedgen — 83 теста
```

### 2. Go Unit/Integration тесты
```bash
go test ./data-service/... ./mcp-gateway/...  # ~585 тестов в 16 пакетах (data-service: ~416, mcp-gateway: ~121, admin-dashboard: ~58, helperium-go: ~22)
# Seedgen больше не часть data-service — вынесен в agent-db/agent_db/seedgen/ (Python)
```

### 2b. Embed Widget (TypeScript)
```bash
cd api-service/embed && npm test    # 59 тестов (vitest)
cd api-service/embed && bash build.sh  # typecheck + esbuild → dist/embed.js
# Или через Makefile из корня:
make ci-test-embed
```
> **⚠️ После пересборки виджета:** `./scripts/dev.sh restart api` — api-service монтирует `embed/dist/`, без перезапуска отдаёт старый JS.

### 3. Сквозные интеграционные тесты

**Рекомендуемый CI (pytest e2e):**
- `uv run pytest tests/e2e/ -v` — 48 тестов: data isolation, admin lifecycle, MCP dynamic/composite, SSE session, agents, config persistence. Без LLM.
- `uv run pytest tests/e2e/test_data_isolation.py -v` — только data isolation.
- `uv run pytest tests/e2e/agents -v` — CRUD агентов, LLM providers, widget-config.
- `uv run pytest tests/e2e/ -v --llm-key` — 52 теста, включая LLM SSE чат.
- `uv run pytest tests/e2e/llm/ -v` — только LLM-тесты (#MISTRAL_API_KEY).

**LLM тесты (в `tests/e2e/llm/`):**
- `test_chat_over_http` — прямой HTTP POST /api/chat с SSE парсингом
- `test_chat_via_agent_endpoint` — чат через конкретного агента /api/chat/{name}
- `test_llm_calls_tool_and_returns` — LLM использует MCP инструменты и возвращает ответ
- `test_chat_without_tenant_id_falls_back` — отказоустойчивость без X-Tenant-ID

**Legacy (agent-db CLI, не обновляется):**
- `uv run agent-db e2e-data` — изоляция данных между tenant'ами (8 тестов, дублируется pytest)
- `uv run agent-db e2e-mcp` — динамические MCP-инструменты (3 теста, дублируется pytest)

### 4. Mutation testing (api-service, Python)

Оценка реального качества тестов: мутация кода → проверка, ловит ли тест изменения.

**Python (mutmut, только api-service):**
```bash
./scripts/run_mutmut.sh --build   # сборка Docker образа (1 раз)
./scripts/run_mutmut.sh --docker  # запуск (~30 мин, 12 052 мутанта)
```

> **⚠️ Время:** ~30-40 минут на полный прогон. macOS fork-crash — только Docker/Linux.
> **Текущий score:** ~65% (8100+ KILLED / 2681 SURVIVED / 12052 total).
> **Лимит на CI:** ~12К мутантов, 194 теста на каждый.
> Для daily run: `bash .nightly_mutmut.sh` (опционально).

**Go (go-mutesting, Avito fork):**
```bash
./scripts/run_mutmut.sh --go  # data-service + mcp-gateway (~5 мин)
```

---

## 🧠 5. Использование Knowledge Graph (codebase-memory MCP)

Проект использует **codebase-memory** MCP сервер для графа зависимостей (5234 узла, 24614 рёбер). **Не читай код вслепую — используй граф.**

**Алгоритм работы для агента:**
1. **Ориентирование**: Вместо `grep` используй `codebase_memory_search_graph({ query: "ClassName", project: "helperium" })`, чтобы увидеть все связи.
2. **Трассировка**: Чтобы понять, как данные текут от API до БД, используй `codebase_memory_trace_path({ function_name: "APIHandler", project: "helperium", direction: "both", mode: "calls", depth: 3 })`.
3. **Поиск**: Используй `codebase_memory_search_code({ pattern: "...", project: "helperium" })` для текстового поиска по коду.
4. **Обновление**: После внесения правок в код выполни `codebase_memory_index_repository({ repo_path: ".", name: "helperium", mode: "moderate" })`, чтобы граф оставался актуальным.

---

## 🔒 6. Security & Tenant Isolation

Изоляция данных и инструментов между tenant'ами обеспечивается на трёх уровнях:

### Data-service level
`TenantStore` хранит изолированные конфиги и подключения к БД для каждого tenant'а. Каждый tenant имеет свою БД (отдельный SQLite файл или PG схему/БД). `X-Tenant-ID` определяет, к какой БД идёт запрос. Нет единой таблицы с tenant_id колонкой — физическая изоляция.

**Write-tool approval flow:** все сгенерированные конфиги имеют `read_only: true`. Write-тулы активируются через `POST /admin/tools/{toolName}/approve` или ручной `"read_only": false` в конфиге. Без подтверждения write-тулы скрыты от LLM.

**resolvePath() баг-фикс:** Для PostgreSQL DSN в формате `postgres://...` функция `resolvePath()` НЕ должна склеивать DSN с путём (см. `tenant.go:resolvePath()` — добавлена проверка `strings.Contains(dsn, "://")`).

### mcp-gateway level
Инструменты регистрируются с tenantID в замыкании (closure) через `makeHandler(td, client, tenantID)`. Даже если клиент укажет `X-Tenant-ID: tenant-a,tenant-b`, вызов `tenant-a__list_students` пойдёт строго в data-service с `X-Tenant-ID: tenant-a`. Инструменты tenant-c не существуют в этой сессии, если tenant-c не был указан при открытии SSE.

### api-service level
Список tenant'ов определяется заголовком `X-Tenant-ID` от web-прокси и передаётся как `tenant_ids: list[str]` через orchestrator → MCPClient. Если tenant не указан в заголовке, его данные и инструменты недоступны.

### Admin Dashboard level (RBAC)
Два уровня доступа к admin dashboard:
- **admin** (`ADMIN_TOKEN`) — полный CRUD: создание/удаление tenant'ов, редактирование конфигов, аппрув тулов, управление агентами/RAG/LLM/voice
- **viewer** (`VIEWER_TOKEN`) — только чтение: GET на `/api/*`, POST/PUT/DELETE → 403 Forbidden

Роль определяется на уровне middleware в `admin-dashboard/internal/server/server.go` по токену в заголовке `Authorization: Bearer <token>`. Фронтенд (Alpine.js) фетчит роль из `/api/dashboard` при логине и скрывает write-кнопки для viewer — блокировка на уровне backend, CSS — чисто UX.

**Публичные пути** (без auth): `/health`, `/api/health`, статика `/`, `/styles.css`, `/app.js`, `/js/*`, `/static/*`, `/i18n.json`, `/i18n.js`, `/metrics`.

### Верификация изоляции
- `e2e-data` — data-level: tenant-a не видит БД tenant-b (разные SQLite файлы).
- `e2e-mcp` — tool-level: tenant-shop не может вызвать инструмент `list_student` tenant-uni (возвращается ошибка).
- `e2e-mcp-composite` — composite routing: `tenant-uni__list_student` идёт строго в data-service tenant-uni, `tenant-shop__list_product` строго в data-service tenant-shop, несмотря на одну SSE сессию.

Никаких cross-tenant утечек.

---

## 📄 7. API контракты и specs/ — как это работает

[specs/README.md](specs/README.md) — полное описание. Кратко:

```
specs/
├── ~~config.schema.json~~     # Удалён — валидация в Go-типах helperium-go/config/types.go
├── config.example.json       # Пример конфига (SQLite, тесты/dev)
├── config.postgres.json      # Пример конфига (PostgreSQL, production)
├��─ api.openapi.yaml — автогенерация из FastAPI
├── rag.openapi.yaml          # OpenAPI rag — автогенерация из FastAPI
└── ...
```

**Два типа контрактов:**
- ~~`config.schema.json`~~ — удалён. Валидация в `helperium-go/config/types.go`. Меняешь типы → меняешь `Validate()` → `go test`.
- `api.openapi.yaml` / `rag.openapi.yaml` — **слепки** автогенерации FastAPI. Первичен код. Тесты ловят рассинхрон:
  ```bash
  uv run pytest api-service/src/api_service/tests/unit/test_openapi_api.py
  uv run pytest rag/tests/unit/test_openapi_spec.py
  ```

---

## ⚠️ 8. Важные ограничения и правила
- **Никакого SQL в Python**: Весь доступ к данным идет ТОЛЬКО через HTTP-запросы к `data-service`.
- **Generic-подход**: При добавлении новых полей или сущностей не хардкодь их в коде — конфиг data-service описывает сущности декларативно.
- **Stateless**: Сервисы не должны хранить состояние сессии локально (кроме кэша сессий в SQLite), чтобы обеспечить масштабируемость.
- ~~**Config schema — runtime-обязательна**~~ — удалена. `config.schema.json` больше не нужен. Валидация встроена в `config.Load()` через `cfg.Validate()`.

---

## ✅ 9. CI/CD и Quality Gates

Проект проходит полный CI-пайплайн на GitHub Actions при каждом пуше в `main`/`master`/`develop`.

### 🔄 CI Pipeline (`.github/workflows/ci.yml`)

| Job | Что проверяет | Команда |
|---|---|---|
| `lint-python` | Ruff lint, Ruff format check, Pyright type check | `ruff check`, `ruff format --check`, `pyright` |
| `test-python` | Все Python unit/integration тесты | `pytest api-service/src/api_service/tests/` + `demo/web/tests/` + `demo/tests/` + `rag/tests/unit/` + `helperium-sdk/tests/` |
| `lint-go` | golangci-lint v2 (errcheck, staticcheck, unused, ineffassign, govet) | `golangci-lint run ./...` |
| `test-go` | Go тесты в data-service и mcp-gateway | `go test ./... -count=1 -timeout 180s` |

**Pipeline считается зелёным**, когда все 4 джобы проходят. Каждая джоба падает независимо — если хоть одна красная, CI красный.

### 🐶 Pre-commit hooks (`.pre-commit-config.yaml`)

```bash
pre-commit install          # установить хуки (однократно)
pre-commit run --all-files  # прогнать на всех файлах
```

| Hook | Источник | Проверяет |
|---|---|---|
| `ruff` | astral-sh/ruff-pre-commit | Lint errors |
| `ruff-format` | astral-sh/ruff-pre-commit | Code formatting |
| `Pyright` | jordemort/action-pyright | Type correctness |
| `go vet (data-service, mcp-gateway)` | классический go vet | Подозрительные конструкции (быстро) |
| `trailing-whitespace` | pre-commit-hooks | Лишние пробелы в конце строк |
| `end-of-file-fixer` | pre-commit-hooks | Пустая строка в конце файла |
| `check-yaml` | pre-commit-hooks | Валидность YAML |
| `check-added-large-files` | pre-commit-hooks | Файлы >500KB в коммите |
| `check-merge-conflict` | pre-commit-hooks | Маркеры merge conflict |
| `gitleaks` | gitleaks `v8.24.0` | Секреты в коде |
| `admin-dashboard-stale` | local | Бинарник admin-dashboard не старше `app.js` + domain-модулей (забыл `go build`) |
| `admin-dashboard-tests` | local | vitest теста на `api()` + contract scan по domain-модулям (при изменении `app.js` или `js/domains/*.js`) |

> Pre-commit — быстрый (`go vet`, не `golangci-lint` — он медленный). Полный линтинг только в CI.
> Хуки admin-dashboard проверяют что бинарник свежий (`go build`) и JS тесты проходят.
> При изменении domain-модулей (`js/domains/*.js`) хуки запускаются автоматически.

### 🔧 Линтеры — настройка и прогон

#### Python (ruff + Pyright)

```bash
# Ruff — быстрый линтер / форматтер
uv run ruff check api-service/src/           # lint
uv run ruff format --check api-service/src/  # check formatting
uv run ruff format api-service/src/          # apply formatting

# Pyright — статическая типизация
npx pyright                                  # проверить всё
```

Конфиг Pyright: `pyrightconfig.json` (excludes: `tests/`, `node_modules/`, `.venv/`, `.data/`).

#### Go (golangci-lint v2)

```bash
# Установка (однократно)
go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest

# Вручную — быстрая проверка одного пакета при изменении
cd data-service && golangci-lint run ./...
cd mcp-gateway && golangci-lint run ./...
```

Оба модуля должны выдавать **0 issues**. Конфиг: `.golangci.yml` (v2, errcheck exclude-functions для стандартных идиом Go).

### 🏁 Makefile — локальная симуляция CI

```bash
make ci         # полный прогон (линт + audit + тесты Python, Go и admin-dashboard)
make ci-lint-py # только Python линт + typecheck
make ci-test-py # только Python тесты (api-service + demo + RAG unit + SDK)
make ci-lint-go # только Go линтинг (data-service + mcp-gateway)
make ci-test-go # только Go тесты (data-service + mcp-gateway)
make ci-audit   # полный security audit (uv audit + govulncheck)
make ci-admin   # сборка admin-dashboard + JS тесты (Vitest: 24 теста, ~300ms)
```

**Перед каждым пушем:** `make ci` — занимает ~2–3 мин, ловит ~95% проблем, которые упадут в CI.

Альтернатива — запустить только нужный набор:
- `make ci-test-py` — только Python тесты (509 тестов, ~10 сек)
- `make ci-test-go` — только Go тесты (655 тестов, ~30 сек)
- `make ci-admin` — только admin-dashboard (сборка + JS тесты, ~2 сек)

### 🐳 act — точная симуляция GitHub Actions

```bash
brew install act           # установка (однократно)
act -j lint-go             # одна джоба в Docker
act --pull=false           # весь пайплайн
```

Требует Docker Desktop. Использует **те же раннеры, те же версии тулов** — 100% совпадение с CI. Полезно, когда `make ci` прошёл, но CI падает на невоспроизводимых отличиях (macOS vs Ubuntu, версии тулов).

### 🎯 Admin-dashboard: защита от регрессий (v1.1.1)

Admin-dashboard — SPA на Alpine.js, вкомпилированная в Go-бинар через `//go:embed`.
Источник багов: несоответствие между фронтом и API (формат ответа, валидация).

### Архитектура JS-модулей (v1.1.1)

Моноолитический `app.js` (1166 строк) разбит на **10 domain-модулей** + 4 core-модуля:

```
admin-dashboard/internal/server/static/
├── app.js                          # Точка входа, Alpine.start()
├── js/
│   ├── apiClient.js                # Обёртка fetch → Alpine.store('api')
│   ├── store.js                    # Alpine.store() — глобальное состояние
│   ├── core/
│   │   ├── apiLogger.js            # Логирование API-вызовов + debug-панель
│   │   ├── eventBus.js             # Простой pub/sub между модулями
│   │   └── notify.js               # Toast-уведомления (ok/err)
│   └── domains/
│       ├── auth.js                 # Авторизация, токен
│       ├── tenants.js              # CRUD tenant'ов
│       ├── config.js               # Конфиги tenant'ов
│       ├── tools.js                # MCP-инструменты, approval
│       ├── rag.js                  # RAG-документы
│       ├── agents.js               # CRUD агентов
│       ├── abuse.js                # Anti-abuse настройки
│       ├── emergency.js            # Big Red Button (Lockdown)
│       ├── llm.js                  # LLM-провайдеры, модели
│       └── voice.js                # STT/TTS провайдеры
└── styles.css                      # CSS (включает toast + debug-panel стили)
```

**Auth bypass:** Go-сервер (`server.go`) пропускает авторизацию для `/static/` и `/js/`.

### Три уровня защиты

1. **JS unit-тесты** (`admin-dashboard/tests/api.test.js`, 16 тестов) — проверяют
   `api()` функцию: парсинг 200/204/422/401, Pydantic validation errors,
   сетевые ошибки. Mock'и, ~200ms.

2. **Contract-тесты** (`admin-dashboard/tests/contract.test.js`) — сканируют
   все domain-модули (`js/domains/*.js`) и сверяют API-вызовы с **3 контрактными JSON**:
   - `tests/contracts/api-endpoints.json` — api-service (agents, voice, llm, abuse, chat)
   - `tests/contracts/rag-endpoints.json` — rag-service
   - `tests/contracts/admin-endpoints.json` — Go proxy (tenants, config, tools)

   Поддерживаются паттерны: `api('/api/...')`, `Alpine.store('api').get('/api/...')`,
   `fetch('/api/...', {method:'POST'})`.

   Если тест упал — endpoint не найден ни в одном контракте → добавь в нужный JSON.
   Если удалил endpoint из бэка — удали из контракта.

3. **Pre-commit хуки:**
   - `admin-dashboard-stale` — не даёт закоммитить изменения в `app.js` / domain-модулях без
     пересборки бинарника (`go build`)
   - `admin-dashboard-tests` — гоняет все vitest теста при изменении `app.js` или `js/domains/*.js`

**Запуск:**
```bash
make ci-admin                 # сборка + все тесты
cd admin-dashboard/tests && npm test  # только тесты (без сборки)
```

**Контракт-тесты (shell):** `scripts/check-admin-contract.sh` — парсит Go-хендлеры
(`grep -rn`) + JS-вызовы (`extract-frontend-endpoints.js` по `app.js` + `js/domains/*.js`)
и сверяет пересечение. Поддерживает `Alpine.store('api')` и raw `fetch()` паттерны.

**OpenAPI контракт** — `specs/api.openapi.yaml` (автогенерация из FastAPI):
```bash
# Обновить спека после изменения api-service:
curl -s http://127.0.0.1:8081/openapi.json | python3 -c "import sys,yaml,json; yaml.dump(json.load(sys.stdin), sys.stdout)" > specs/api.openapi.yaml

# Сгенерировать TS-типы для админки:
npx openapi-typescript specs/api.openapi.yaml -o admin-dashboard/internal/server/static/api-types/api-service.d.ts
```

--

### 📦 Версионирование

Все 6 Python-пакетов (`agent-db`, `helperium-sdk`, `api-service`, `demo-web`, `rag`, `pyproject.toml`) и 2 Go-модуля (`data-service`, `mcp-gateway`) синхронизированы на одной версии:
- Текущая: **`1.1.0`**
- Go (data-service / mcp-gateway): `go 1.26.5`
- Go (admin-dashboard / helperium-go): `go 1.24.0`

### 🧪 Критерий готовности перед коммитом

1. [ ] `make ci` — зелёный (если не уверен — `make ci-test-py` быстрее: ~10 сек)
2. [ ] Pre-commit hooks — все Passed
3. [ ] `uv run pytest tests/e2e/ -v` — 44 e2e теста (без LLM), либо `uv run agent-db e2e-full` (если нужны legacy команды)
4. [ ] Mutation score не упал: `./scripts/run_mutmut.sh --docker` (30 мин, не для каждого коммита — опционально)

---

## 📝 10. Contributor License Agreement (CLA)

Проект использует **CLA** ([`CLA.md`](CLA.md)) для защиты коммерческой модели (возможность продавать кастомные сборки и white-label версии без перелицензирования каждого PR).

**Что даёт CLA:**
- Право включать сторонние PR в коммерческие/проприетарные версии проекта
- Защита от патентных претензий со стороны контрибьюторов
- Возможность сублицензировать код (белая этикетка для клиентов)

**Как работает:**
- При первом PR бот [CLA Assistant](https://cla-assistant.io/) запрашивает подпись
- Без подписи PR не принимается
- MPL-2.0 сама по себе не даёт права перелицензирования — CLA закрывает эту дыру

**Прошлые коммиты:** Авторское право на код до введения CLA остаётся за автором проекта и его другом (эпизодические коммиты из MVP). Коммиты от AI-агентов (Vibe/Mistral) считаются сгенерированным кодом, авторские права не применимы.

---

## 📊 11. Monitoring & Observability

Каждый сервис отдаёт Prometheus-метрики на `/metrics`:

| Сервис | Порт | Ключевые метрики |
|---|---|---|
| **data-service** | :8084 | `data_requests_total`, `data_request_duration_ms` |
| **mcp-gateway** | :8083 | `mcp_tool_calls_total`, `mcp_sessions_active`, `mcp_rate_limit_hits_total` |
| **admin-dashboard** | :8085 | `admin_requests_total` |
| **api-service** | :8081 | `chat_sessions_total`, `chat_messages_total`, `llm_calls_total`, `llm_duration_ms`, `llm_token_usage`, `llm_cost_total`, `abuse_blocked_total`, `backlog_*` |

### Docker monitoring profile

```bash
docker compose --profile monitoring up -d
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000 (admin/admin)
```

Grafana дашборд предустановлен (18 панелей) — `docker/grafana/dashboards/helperium-overview.json`.

### Logging

- **api-service**: structlog, JSON-логи (`LOG_FORMAT=json`). `LOG_LEVEL` поддерживается.
- **data-service / mcp-gateway / admin-dashboard**: slog, structured JSON. `LOG_LEVEL` поддерживается.

### Anti-Abuse

api-service имеет встроенный anti-abuse engine:

- **TokenBucket**: per-сессия, конфигурируемый RPS/burst (`ABUSE_RPS`, `ABUSE_BURST`).
- **UA block**: curl, wget, python-requests, Go-http-client и др. User-Agent'ы.
- **Message limits**: max length 2000 chars, min interval 1s, session budget 50 messages.
- **Repeated text**: >3 повторов блокируется.
- **Emergency presets**: Normal / Cautious / Lockdown (через admin-dashboard).

### Admin dashboard (v1.1.0 additions)

- **Anti-Abuse tab**: настройки abuse engine для глобал и per-agent.
- **Emergency Big Red Button**: Normal → Cautious → Lockdown одним кликом.
- **i18n**: bilingual RU/EN (309 ключей). Language switcher в хедере.
- `LOG_LEVEL=debug` для детальной трассировки запросов.
