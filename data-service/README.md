# Data Service — Config-Driven REST API для произвольных БД

**Назначение:** Универсальный HTTP-прокси к клиентским БД. Схема БД описывается JSON-конфигом → автогенерируются REST эндпоинты, MCP-тулы, OpenAPI. Никакого доменного кода.

**Цель:** Горизонтально масштабируемый stateless сервис — добавление инстансов за балансировщиком, каждый tenant изолирован (свои пул коннектов, роутер, конфиг).

---

## Архитектура (30 сек)

```
config.json → chi Router → TenantStore (per-tenant Router)
                │
                ├─ /{entity}/{id}        → get_by_id
                ├─ /{entity}?search=...  → find (legacy, name-based)
                ├─ /{entity}             → list (legacy)
                ├─ /{entity}/grep        → grep strategy (multi-token AND text search)
                ├─ /{entity}/filter      → filter strategy (field__op)
                ├─ /{entity}/count       → count with filters
                ├─ /{entity}/distinct    → unique values for enum columns
                ├─ /{entity}/schema      → schema discovery (total, distinct, min/max)
                ├─ /{entity}/{id}/...    → custom_query (whitelist SELECT)
                ├─ /docs                 → Swagger UI
                ├─ /openapi.json         → OpenAPI 3.1 spec
                ├─ /mcp/manifest         → runtime MCP tools generation
                ├─ /mcp/schema           → introspected DB schema for LLM
                ├─ /health, /stats       → builtin
                └─ /admin/*              → tenant CRUD, config hot-reload, discover
```

**Структура пакетов (`internal/`):**

```
internal/
├── configgen/                  # Генерация конфига из интроспекции БД
│   ├── configgen.go            # Оркестратор Generate, SkipRule, CRUD-endpoints
│   ├── columns.go              # Семантический анализ колонок (isNameField, findSearchField)
│   ├── entity.go               # datasource.Table → config.Entity
│   ├── naming.go               # Форматирование имён (pluralize, titleCase, toolDisplayName)
│   ├── navigation.go           # FK → custom_queries + navigation endpoints
│   ├── llm.go                  # SchemaForLLM — system prompt для агента
│   └── mcp.go                  # GenerateMCPTools — MCP-манифест
├── datasource/                 # Adapter interface (SQLite, PG, MySQL в перспективе)
│   └── tests/                  # black-box adapter тесты
├── openapigen/                 # Runtime OpenAPI 3.1 генерация из конфига
├── query/                      # Expression-based query engine (Condition AST → SQL)
│   ├── expression.go           # QueryPlan, Condition, Operator, EmptyHint — типы
│   ├── builder.go              # Engine.Build / BuildCount — Condition AST → SQL+args
│   ├── format.go               # SearchResult, FormatRows (compact/full/count)
│   └── builder_test.go         # white-box тесты query builder'а
├── runtime/                    # Query builder + typed response mapper
│   ├── instrumented_adapter.go # Conn+Adapter → AdapterSubset с опциональными метриками
│   ├── handlers/               # 16 хендлеров (см. Архитектура ниже)
│   │   └── tests/              # black-box handler тесты
│   └── tests/                  # black-box query builder тесты
├── search/                     # Search strategies (v4): единый Strategy interface
│   ├── strategy.go             # Strategy, Adapter interfaces
│   ├── grep.go                 # GrepStrategy — текст. поиск (multi-token AND, regex, LIKE)
│   ├── filter.go               # FilterStrategy — field-based field__op фильтрация
│   ├── schema.go               # SchemaStrategy — мета-инфо для LLM discovery
│   ├── strategy_common.go      # Общие утилиты (parseLimitParam, tokenize, regexOp и др.)
│   └── *_test.go               # white-box тесты стратегий
└── server/                     # HTTP server, middleware, TenantStore
    ├── tenant.go               # TenantInstance, TenantStore, ServeHTTP, config persistence
    ├── tenant_admin.go         # admin-хендлеры (CRUD tenant'ов, rewrite, reload)
    ├── tenant_health.go        # HealthCheck, multiTenantHealthHandler
    ├── tenant_lifecycle.go     # AddTenant, RemoveTenant, ReloadTenant, buildTenantInstance
    ├── server.go               # Middleware (Recovery, RequestID, StructuredLogging, RateLimit)
    ├── endpoint_builder.go     # NewRouterFromConfig — сборка chi-роутера из конфига
    ├── admin.go                # Admin API handlers (config CRUD, tool approval)
    ├── swagger.go              # Swagger UI (GET /docs) + OpenAPI (GET /openapi.json)
    └── tests/                  # black-box scenario/integration тесты
```

### Search Strategies (v4)

6 стратегий через единый `search.Strategy` interface:

| Стратегия | HTTP path | MCP tool | Описание для LLM |
|-----------|-----------|----------|------------------|
| `grep` | `/{entity}/grep` | `grep_{entity}` | Multi-token AND текст. поиск (LIKE/ILIKE, regex, ignore_case, invert по полям) |
| `filter` | `/{entity}/filter` | `filter_{entity}` | Field-based field__op: `{field}__gt`, `__like`, `__in` и т.д. |
| `get_by_id` | `/{entity}/{id}` | `get_{entity}` | Получение записи по PK |
| `count` | `/{entity}/count` | `count_{entity}` | Количество записей с фильтрами |
| `distinct` | `/{entity}/distinct` | `distinct_{entity}` | Уникальные значения колонки |
| `schema` | `/{entity}/schema` | `schema_{entity}` | Мета-инфо: total, distinct values, min/max/avg для LLM discovery |

**Flow:**

```
LLM Tool Call (MCP / HTTP)
       │
       ▼
search.Strategy interface     ← grep, filter, schema имплементации
  └── ParseRequest(r, entity, adapter)  →  query.QueryPlan
       │
       ▼
query.Engine.Build(plan)      ← Condition AST → нативный SQL
       │
       ▼
ReadOnlyDB                    ← урезанный *sql.DB (только SELECT)
       │
       ▼
query.FormatRows              ← SearchResult (Total, Data/Preview, EmptyHint)
```

**EmptyHint:** При `total==0` стратегии возвращают подсказку LLM с доступными значениями:
```json
{"suggested_action": "Try schema_products() to discover available values",
 "available_values": {"brand": ["Brembo", "Bosch"]}}
```

**Принцип:** white-box тесты (`package xxx`) остаются рядом с исходным кодом, black-box тесты (`package xxx_test`) вынесены в `tests/` внутри каждого пакета для чистоты иерархии.

---

## Multi-Tenancy (Strict Mode, фаза 3.7)

- **TenantStore** — мапа `tenant_id → TenantInstance{Config, Conn, Router, ConfigPath, ...}` (разбит на 8 файлов: `tenant.go` + `tenant_admin.go` + `tenant_health.go` + `tenant_lifecycle.go` + `server.go` + `endpoint_builder.go` + `admin.go` + `swagger.go`)
- **Strict**: запрос **обязателен** `X-Tenant-ID` или `?tenant=` → иначе `404 tenant_not_found`
- **Изоляция**: у каждого tenant свой пул коннектов, роутер, конфиг
- **Admin API** (`Authorization: Bearer $ADMIN_TOKEN`):
  - `POST /admin/tenants` — добавить tenant на лету
  - `GET /admin/tenants` — список + health
  - `GET /admin/tenants/{id}` — детали tenant'а
  - `POST /admin/config` — обновить конфиг текущего tenant'а
  - `POST /admin/config/reload` — hot reload без рестарта процесса
  - `POST /admin/config/rewrite` — интроспекция БД → перезапись конфига
  - `GET /admin/config` — текущий конфиг (DSN скрыт)
  - `GET /admin/config/versions` — история версий конфига
  - `DELETE /admin/tenants/{id}` — graceful drain (закрыть пул, удалить из мапы, стереть конфиг с диска)
  - `GET /admin/tenants/{id}/tools/pending` — ожидающие подтверждения write-тулы
  - `POST /admin/tenants/{id}/tools/{toolName}/approve` — подтвердить write-тул
  - `GET /admin/discover` — интроспекция схемы (с `X-Tenant-ID`)
- **Health**: single-tenant `{"status":"ok","db":"ok"}` | multi-tenant `{"status":"degraded","tenants":[...]}`

### Tenant Config Persistence

Все tenant'ы, добавленные через admin API, автоматически сохраняются на диск.
После перезапуска data-service читает конфиги из файловой системы и восстанавливает tenant'ов.

**Директория хранения:** `$TENANTS_DIR` (по умолчанию `.data/tenants/` относительно корня проекта).
Каждый tenant — отдельный JSON-файл:

```
.data/tenants/
├── default.json          # Bootstrap-tenant из --config
├── shop.json             # Добавлен через POST /admin/tenants
└── my-client.json        # Добавлен через UI admin-dashboard
```

**Жизненный цикл:**

```
POST /admin/tenants   ──→  AddTenant() + SaveTenantConfig(id, cfg)  ──→  .data/tenants/{id}.json
POST /admin/config    ──→  update + reload  ──→  SaveTenantConfig(id, cfg)  ──→  перезаписан
POST /admin/config/rewrite ──→  introspect → generate → save ──→  SaveTenantConfig(id, cfg)  ──→  обновлён
DELETE /admin/tenants ──→  RemoveTenant() + DeleteTenantConfig(id)  ──→  файл удалён

Startup               ──→  os.ReadDir(.data/tenants/) → config.Load() → AddTenant()  ──→  восстановлен
```

**Механизм:** три публичных метода `TenantStore` отвечают за запись/чтение/удаление:

| Метод | Назначение |
|---|---|
| `SaveTenantConfig(id, cfg) string` | Маршалит конфиг в JSON и пишет в `.data/tenants/{id}.json`. Возвращает полный путь. |
| `TenantConfigPath(id) string` | Возвращает ожидаемый путь для tenant'а: `.data/tenants/{id}.json`. Создаёт директорию если нужно. |
| `DeleteTenantConfig(id)` | Удаляет файл конфига с диска. Игнорирует `ENOENT` (уже удалён). |

Все admin-хендлеры пишут ТОЛЬКО через `SaveTenantConfig()` — никаких inline `os.WriteFile`.

---

## Конфиг (Source of Truth)

```json
{
  "version": 1,
  "data_source": { "driver": "sqlite|postgres", "dsn": "${DB_PATH:-file.db}", "pool_size": 10, "read_only": true },
  "entities": [{ "name": "student", "table": "students", "id_column": "id", "fields": [...] }],
  "endpoints": [{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }],
  "custom_queries": { "student_grades": { "sql": "SELECT ... WHERE student_id = ?", "params": ["id"], "max_rows": 500 }},
  "stats": { "counters": [{ "name": "students", "entity": "student" }] }
}
```

**Генерация:** `--discover` / `POST /admin/config/rewrite` — интроспекция схемы через configgen → entities + endpoints + MCP tools.

---

## Быстрый старт

```bash
# Сборка
cd data-service && go build -o bin/data-service ./cmd/server/

# Dev SQLite (из корня проекта)
./bin/data-service --config specs/config.example.json

# Dev PostgreSQL
docker compose up -d db
./bin/data-service --config specs/config.postgres.json

# Smoke-test
curl -s http://127.0.0.1:8084/health                    # {"status":"ok"}
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/students
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

**Env vars:** `DS_CONFIG`, `PORT` (8084), `LOG_LEVEL` (info/debug/warn/error), `ADMIN_TOKEN` (обязателен для admin).

---

## Драйверы БД

### SQLite (`sqlite_adapter.go`)
- Чистый Go через `modernc.org/sqlite` (без CGO)
- WAL mode + `synchronous=NORMAL` — ~2x write throughput при той же durability
- `busy_timeout=5000` — конкурентные запросы ждут до 5s вместо `"database is locked"`
- `SetMaxOpenConns(2)` — конкурентное чтение под WAL
- `PRAGMA foreign_keys=ON` — проверка целостности FK

### PostgreSQL (`postgres_adapter.go`)
- pgx/v5 через `database/sql` stdlib (без pgxpool)
- Интроспекция: **4 запроса вместо 4N+1** (один JOIN для колонок + PK, один для описаний, один для FK, один для списка таблиц)
- Надёжный маппинг типов из `data_type` в generic-типы (TypeInt/TypeFloat/TypeString/TypeBool/TypeJSON/TypeDatetime/TypeDate)

### InstrumentedAdapter (`runtime/instrumented_adapter.go`)
- Единый wrapper `Conn + Adapter → runtime.AdapterSubset`
- Заменил дубли `ConnAdapter` (tenant.go) + `metricsAdapter` (endpoint_builder.go)
- Опциональные метрики времени выполнения запросов

---

## Сценарии (Test DB Factory)

`testdata/scenarios/<name>/` содержат pre-built `data.db` для Go-тестов.

| Сценарий | Драйвер | Назначение |
|---|---|---|
| `sqlite-testseed` | SQLite | Базовый смоук (13 entities) |
| `postgres-testseed` | PG | Cross-driver parity |
| `big-testseed` | SQLite | Load test (500 students, 4000 grades) |
| `shop` | SQLite | Сторонняя БД (FK lookups) |

---

## Тестирование

```bash
# Все go-тесты (см. `make ci` — количество тестов динамическое)
go test ./... -count=1

# White-box тесты (рядом с кодом)
go test ./internal/server/... ./internal/runtime/... ./internal/datasource/ ./internal/configgen/... -count=1

# Black-box тесты (в tests/)
go test ./internal/server/tests/ ./internal/runtime/tests/ ./internal/runtime/handlers/tests/ ./internal/datasource/tests/ -count=1

# Race detector
go test -race ./... -count=3

# Cross-driver parity (PG)
docker compose up -d db
AGENT_TUTOR_TEST_PG=1 go test ./internal/server/tests/ -v
```

---

## Security & Hardening

- Только SELECT, prepared statements (`?` / `$1`), `max_rows` обязателен для custom_query
- `read_only: true` по умолчанию: configgen.Generate() форсирует `ReadOnly = &true` если nil
  (вручную можно выставить `false` через config.json; см. `configgen.go:114`)
- ReadOnlyConn обёртка (`datasource.NewReadOnlyConn`) блокирует `ExecContext` на уровне Go
- Banned SQL: `;`, `--`, `/*`, DDL/DML ключевые слова в `counter.Filter`
- Валидация через Go-типы (`helperium-go/config/types.go`), JSON Schema не используется
- Защита от SQL injection в `counter.Filter` — `isValidFilterExpression()` блокирует `;`, `--`, DDL/DML
- Content-Type: `application/json` для всех ответов (включая error recovery)
- Per-query timeout: 30s (`QUERY_TIMEOUT_SECONDS`)
- Data race protection: `healthMu sync.Mutex` в TenantInstance, `atomic.Int32` в ThrottleMiddleware
- Data race protection: `healthMu sync.Mutex` в TenantInstance, `atomic.Int32` в ThrottleMiddleware

---

## Horizontal Scalability

- **Stateless**: никакого локального состояния сессии
- **Tenant isolation**: каждый tenant = независимый `*sql.DB` + `chi.Router`
- **Config hot-reload**: `POST /admin/config/reload` без рестарта
- **Shared-nothing**: добавление инстансов за LB требует только shared config store

---

## Skip Rules (почему таблицы не генерируются)

Configgen.DefaultSkipRules() исключает таблицы по prefix/contains MatchRule:

| Prefix | Причина |
|--------|--------|
| `sqlite_`, `pg_`, `information_schema` | Системные таблицы СУБД |
| `auth_`, `django_`, `migrations`, `jobs` | Django/Laravel framework internals |
| `documents` | Helperium RAG: внутренние чанки документов |
| `schema_migrations`, `ar_internal_metadata` | Rails ActiveRecord metadata |

**Кастомизация:** `skip_rules[]` в config.json (дополняет default), `disabled_default_rules[]` (отключает default по prefix).
Подробно: [doc/agents/search-strategies.md](doc/agents/search-strategies.md).

---

## Ссылки по теме

- [AGENTS.md](../AGENTS.md) — общая архитектура, data flow
- [doc/agents/search-strategies.md](../doc/agents/search-strategies.md) — Search Strategies v4 детально
- [doc/agents/adapter-pattern.md](../doc/agents/adapter-pattern.md) — DataSource interface и адаптеры
- [doc/agents/tenant-lifecycle.md](../doc/agents/tenant-lifecycle.md) — Tenant CRUD, persistence
- [specs/config.schema.md](../specs/config.schema.md) — полная схема config.json

## Связь с остальными сервисами

| Сервис | Порт | Контракт |
|---|---|---|
| **mcp-gateway** | 8083 | `GET /mcp/manifest` (с `X-Tenant-ID`) → runtime MCP tools;
  все data-запросы от LLM через MCP-тулы → data-service HTTP |
| **api-service** | 8081 | Получает данные через mcp-gateway (не напрямую);
  embed-виджет, оркестратор, LiteLLM |
| **admin-dashboard** | 8085 | `/admin/*` (tenant CRUD, config, tools approval);
  прямые запросы к data-service для данных |

---

## Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `bind: address already in use` | Порт 8084 занят | `lsof -ti:8084 \| xargs kill -9` |
| `config: load "...": no such file` | Не тот cwd | Запуск из корня проекта |
| `ADMIN_TOKEN not configured` / 401 | Токен mismatch | `export ADMIN_TOKEN=secret` |
| PG `connection refused` | Colima PG упал | `pg_isready -h localhost -p 5432` |
---
**Last verified:** 2026-07-28 (commit `a12e54c96fb1b751902329133786daf8bab8e971`)
