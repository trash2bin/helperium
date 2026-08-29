# Data Service

Config-driven read-only HTTP/MCP доступ к клиентским БД. Схема описывается JSON-конфигом, из него собираются REST-эндпоинты, MCP-манифест и OpenAPI. Доменного кода нет.

Go/chi, порт 8084.

## TL;DR

| Аспект | Значение |
|---|---|
| Порт | `8084` |
| Transport | HTTP REST + MCP tool backend (`/q/*`, `/mcp/manifest`) |
| Auth | `X-Tenant-ID` (обязателен); `/admin/*` — `Authorization: Bearer {admin_token}` |
| Read-only | Да, по умолчанию; write-методы не регистрируются |
| Quick start | `go run ./cmd/server/ --config specs/config.example.json` |
| Smoke test | `curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/health` |

**Reference:**
- [MCP tool surface](#mcp-тулы) — инструменты и `/q/*` диспетчер
- [Search strategies](doc/agents/search-strategies.md) — grep/filter/schema стратегии
- [Security isolation](doc/agents/security-isolation.md) — tenant-изоляция и RowFilters
- [Config format](specs/config.schema.md) — JSON-схема конфига
- [OpenAPI runtime](services/helperium-go/openapigen/openapigen.go) — генерация OpenAPI 3.1

---

## Входные точки

- `main()` — флаги: `--discover` (интроспекция → конфиг в stdout), `--config` (путь к JSON). Env: `PORT`, `DS_CONFIG`, `LOG_LEVEL`, `ADMIN_TOKEN`, `TENANTS_DIR`, `QUERY_TIMEOUT_SECONDS`.
- `runDiscover()` — CLI-режим интроспекции.
- `watchConfig()` — hot-reload конфига по fsnotify с дебаунсом 500ms.

## Пакеты

```
cmd/server/                    main, discover, watchConfig
internal/configgen/            генерация конфига из datasource.Schema; intent.go (ExtractIntent/Hydrate)
internal/datasource/           Conn/Adapter интерфейсы + драйверы (sqlite, postgres) + readonly
internal/query/                Expression AST → SQL (Engine)
internal/runtime/              AdapterSubset, EntityResolver, HTTP-хендлеры
internal/search/               стратегии grep/filter/schema (Strategy interface)
internal/server/               TenantStore, роутер, middleware, admin API
```

> OpenAPI-генератор живёт в общем пакете `services/helperium-go/openapigen/`.
> См. `services/helperium-go/openapigen/openapigen.go`.

## datasource — интерфейсы и драйверы

### Conn / Adapter (`internal/datasource/adapter.go`)

- `Conn` — `QueryRowContext / QueryContext / ExecContext / PingContext / Close`.
- `Adapter` — `Driver()`, `Connect()`, `Introspect()`, `TranslatePlaceholder()`, `QuoteIdentifier()`.
- `Schema/Table/Column/ForeignKey` — результат интроспекции.

### Драйверы

- `SqliteAdapter` — modernc.org/sqlite (без CGO). `Connect()`: WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `SetMaxOpenConns(2)`, `PRAGMA foreign_keys=ON` (и через DSN-параметры `_pragma=...` — применяются к каждому коннекту).
- `PostgresAdapter` — pgx/v5 через database/sql. `TranslatePlaceholder()`: `?` → `$N`. `QuoteIdentifier()`: двойные кавычки, экранирует `"` в сегментах.
- `NewDefaultRegistry()`: реестр драйверов по имени.

### ReadOnly (`internal/datasource/readonly.go`)

- `ReadOnlyConn` — обёртка над `Conn`. `ExecContext()` всегда возвращает ошибку. Используется для code-level гарантии: data path не пишет в БД.

### DataSource (абстракция для не-SQL бэкендов, `internal/datasource/datasource.go`)

- `DataSource` interface — `Search / Filter / GetByID / Count / Distinct / Schema / Close`.
- `SQLDataSource` — реализация поверх query.Engine. **Важно:** `Search/Filter/GetByID/Count/Distinct` в `SQLDataSource` — заглушки, возвращают не-implemented. Реально работает только `Schema()`. HTTP-стратегии идут через `search.Strategy`, не через DataSource.

## query — Expression AST → SQL (`internal/query/`)

- `QueryPlan`: Select, From, Where []Condition, RawWhere, Order, Limit, Offset, Format.
- `Condition`: Field, Operator, Value, Values, Not.
- `Operator`: Eq, Neq, Lt, Gt, Lte, Gte, Like, ILike, NotLike, Regex, In, Between.
- `NewEngine(adapter)`. `Build()` / `BuildCount()` — внешние методы; `build()` + `RenderConditions()` / `renderCondition()` — внутренние. Инверсия операторов при `Not`: `Eq → <>`, `Regex → NOT REGEXP / !~`.
- LIKE/ILIKE/NOT LIKE — `ESCAPE '\'`: QuoteString экранирует `%`/`_` обратным слэшем; без ESCAPE-клаузы экранирование не работает (в SQLite `\` — литерал, wildcard остаётся активным).
- `SearchResult` / `FormatRows()` — compact/full/count форматы.
- **RawWhere в grep:** multi-token AND собирается строкой. Tenant-фильтр для RawWhere — обёртка в подзапрос (`insertTenantBeforeLimit`).

## search — стратегии (`internal/search/`)

### Strategy interface

```go
type Strategy interface {
    Name() string
    ToolName(entity config.Entity) string
    ToolDescription(entity config.Entity) string
    ToolParams(entity config.Entity) []config.EndpointParam
    ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error)
    EntityIDCol() string
    EntityNameCol() string
}
```

- `Adapter` — `QuoteIdentifier / QuoteString / TranslatePlaceholder / IsPostgres`.
- `NewAdapter(inner query.AdapterSubset)` — мост runtime.AdapterSubset → search.Adapter.

### GrepStrategy

Multi-token AND по полям, OR между полями. Лимиты: `maxRegexLen=200` (ReDoS), `maxTokens=10`, `maxPatternLen=500`.
Параметры: `pattern` (required), `limit` (1-100, default 10), `fields`, `ignore_case`, `invert`, `regex`, `offset`, `format`, `sort_by` (последние не в JSON Schema).

### FilterStrategy

Лимиты: `maxFilterValueLen=200`, `maxInValues=50`, `maxFilters=15`.
Фильтрует поля через `config.IsFilterableField`. Поддерживаемые операторы: exact / `__gt` / `__gte` / `__lt` / `__lte` / `__like` / `__in` / `__neq`.
Числовые поля поддерживают сравнение поле-с-полем: `<field>__gt_field=<other_field>` (аналогично `__gte_field`, `__lt_field`, `__lte_field`). Обе стороны — числовые колонки одной сущности, проходят `QuoteIdentifier`; PK, FK и `tenant_id` отклоняются.

## runtime — типы и хендлеры

### Типы (`internal/runtime/types.go`)

- `AdapterSubset` — урезанный Conn+Adapter: QueryContext, QuoteIdentifier, TranslatePlaceholder, PingContext.
- `Entity/EntityField`, `CustomQuery`, `Endpoint`.
- `EntityResolver` — резолв entity по имени (whitelist, неизвестный → 404).

### HTTP-хендлеры (`internal/runtime/handlers/`)

| Файл | Функция | Назначение |
|---|---|---|
| `get_by_id.go` | `GetByIDHandler` | `GET /{entity}/{id}` |
| `count.go` | `CountHandler` | `GET /{entity}/count` |
| `distinct.go` | `DistinctHandler` | `GET /{entity}/distinct?column=` |
| `custom_query.go` | `CustomQueryHandler` | `GET /{parent}/{id}/{child}` (whitelist SELECT) |
| `stats.go` | `StatsHandler` | `GET /stats` — count по `Stats.Counters` из конфига |
| `strategy_handler.go` | `NewStrategyHandler` | grep/filter стратегии |
| `schema_handler.go` | `NewStrategySchemaHandler` | schema стратегия |
| `mcp_manifest.go` | `MCPManifestHandler` | `GET /mcp/manifest` |
| `context.go` | `queryCtx` / `tenantID` / `RespondJSON` / `RespondError` | общие утилиты |

### Tenant-фильтр (`row_filter.go`)

- `tenantFilter` вставляет `WHERE` из `auth.RowFilters` по entity. Fail-closed: при header-auth и отсутствии RowFilter для entity запрос получает `400` (без `X-Tenant-ID`) или `403` (без RowFilter). `tenantWhere` конкатенируется поверх `counter.Filter`.
- `CountHandler` исключает `tenant_id` из fieldMap и из системных параметров (защита от фильтрации по чужому tenant_id).
- `GetByIDHandler` применяет `tenantFilter` перед SQL; нецелое `id` → `400 validation_error`. **Чужой id → 404** (нет оракула перебора id между tenants).
- `db_related` — тот же `tenantFilter` + явная проекция БЕЗ `tenant_id`.
- **Security Gap:** при `AuthStrategyHeader` без настроенных `RowFilters` — tenant-фильтр не применяется. Регресс-тест: `row_filter_security_test.go`.

### MCP-тулы и FK-навигация (v5 — консолидированные db_* + пер-энтити filter_, Фаза 2/2.5)

- **Поверхность тулов:** N пер-энтити `filter_{entity}` (имена полей прямо в схеме тула — слабая модель не может вытащить их из db_map) + 6 консолидированных `db_*`: `db_map`, `db_describe`, `db_search`, `db_filter`, `db_get`, `db_related` (указывают на `/q/*` диспетчер). Фильтрация выполняется пер-энтити `filter_{entity}` (когда endpoints объявляют `strategy=filter`; имена полей в схеме тула напрямую) ИЛИ консолидированным `db_filter` → `/q/filter` (fallback для конфигов без per-entity filter-эндпоинтов; имена полей берутся из db_map).
- **Почему filter пер-энтити:** консолидированный `db_filter` (entity runtime + статичная JSON-схема) не может перечислить поля; слабая модель вызывала его напрямую и не могла вытащить имена полей из db_map. Пер-энтити `filter_{entity}` кладёт поля топ-левел в схему тула → модель строит `filter_products?price__gt=100` с первого раза (валидировано живой моделью Ollama, см. `doc/agents/search-strategies.md`).
- **`entity` — обычный string, не enum** (на большой БД enum на сотни значений расдул бы манифест). Допустимые имена модель узнаёт из `db_map`; сервер валидирует через `EntityResolver` (whitelist, `/q/*` с неизвестным entity → 404).
- **`db_related`** — один хоп по объявленному FK с tenant-фильтром и лимитом (в отличие от legacy custom_query-навигации, у которой нет tenant-фильтра и лимит 1000 строк).
- **LLMToolPolicy** (`config.LLMToolPolicy`) — opt-in возврат `get_*`/`count_*`/`distinct_*` в манифест (`ExposeGetByID`/`ExposeCount`/`ExposeDistinct`, default false). Анти-перебор: `db_get` требует id из предыдущего поиска, NEVER enumerate.
- **`_by_` relationship-тулы НЕ генерируются.** FK-навигация — через `filter_{entity}({fk}=...)` или `db_related`.
- Навигационные custom-query эндпоинты (`GET /{parent}/{id}/{child}`) существуют в REST, но не экспонируются LLM (нет tenant-фильтра).
- Каждый FK (`*_id`, кроме `tenant_id`) implicitly filterable → `filter_{entity}({fk}=...)` доступен по умолчанию.
- REST-эндпоинты (`GET /{entity}/grep|filter|schema|{id}|count|distinct`) **сохранены** — это только про MCP-манифест.

### Schema handler (`schema_handler.go`)

`distinctValues()` (до 20 значений), `fieldStats()` (min/max/avg).

### `/q/*` диспетчер (Фаза 2, `q_dispatch.go`)

Консолидированные LLM-эндпоинты, за которыми стоят те же стратегии/хендлеры (filter — пер-энтити, через живые REST-роуты `/{entity}/filter`; `/q/filter` — fallback `db_filter` для конфигов без per-entity filter-эндпоинтов):

| Route | Тул | Handler |
|---|---|---|
| `GET /q/map` | `db_map` | SchemaForLLM (карта БД: сущности, поля, FK, hints) |
| `GET /q/describe?entity=X` | `db_describe` | SchemaStrategy (метаданные сущности) |
| `GET /q/search?entity=X&pattern=..` | `db_search` | GrepStrategy |
| `GET /q/get?entity=X&id=..` | `db_get` | GetByID |
| `GET /q/related?entity=X&id=..&relation=..` | `db_related` | FK-навигация (RelatedHandler) |

Фильтрация — пер-энтити `filter_{entity}` на живых REST-роутах `GET /{entity}/filter` ИЛИ, если конфиг не объявляет per-entity filter-эндпоинты, консолидированным `db_filter` через `/q/filter`.

- **Entity whitelist:** все /q/* резолвят entity через `EntityResolver.Resolve`; неизвестный → 404.
- **Стрип entity:** параметр `entity` удаляется из query перед делегированием стратегии.
- **db_related** — tenant-фильтр применяется всегда, лимит по умолчанию 20 (max 100).
- **db_map fallback:** при `IntrospectedSchema == nil` (после рестарта до rewrite) `GenerateSchemaForLLM` строит карту из `cfg.Entities` (FK из `Relations`) — db_map не отдаёт 503, модель не слепнет.

### Маппинг значений

- `coerceNative()` — приведение значения к типу колонки из конфига.
- **Числовые колонки:** `safeFloatToInt64()` — при дробном или out-of-range значении возвращает `float64` с предупреждением (silent cast запрещён).
- **Даты:** канонический `RFC3339`. `time.Time` → `UTC().Format(RFC3339)`, SQLite-строка (`"2006-01-02 15:04:05"`, `"2006-01-02"`) → нормализация в `RFC3339`.
- **Сканирование:** типизированное через `columnFieldType()` — числовые колонки отдают числа, bool — bool.

### TenantStore (`tenant.go`)

- `TenantInstance` — ID, Config, Conn, ReadonlyConn, Adapter, Router, IntrospectedSchema. Доп. поля синхронизации: `healthMu` (Healthy/LastError), `schemaMu` (RWMutex), `removing` (atomic.Bool — удаление в процессе).
- `TenantStore` — мапа id→instance, RWMutex. `ServeHTTP` — роутинг по `X-Tenant-ID` (удерживает RLock на весь запрос, закрывает TOCTOU с ReloadTenant/RemoveTenant). `resolveTenant()` — без лока (для admin-хендлеров, с проверкой `removing`).
- Персистентность: `SaveTenantConfig()` — **атомарная запись** (temp-файл + `os.Rename`, битый JSON невозможен), `DeleteTenantConfig()` — удаляет и `{id}.json`, и `{id}.schema.json`. Директория `$TENANTS_DIR` (default `.data/tenants/`).
- Schema cache: `TenantSchemaPath()`, `SaveTenantSchema()`, `LoadTenantSchema()` (нет файла → nil), `PersistTenantConfig()` — регенерирует Entities/Endpoints (fallback: сохраняет как есть).
- **Tenant resolution (deprecated fallback):** порядок — context → `X-Tenant-ID` header → `?tenant=` query. Query-параметр **остаётся только как deprecation bridge** (Swagger UI fetch `/openapi.json` и curl-сценарии в RUNBOOK); каждое использование логирует `deprecated`-warn и указывает на `X-Tenant-ID`. Планируемое удаление — после миграции Swagger spec-fetch на header-инъекцию; не добавляйте новых потребителей query-параметра.

### Lifecycle (`tenant_lifecycle.go`)

| Функция | Назначение |
|---|---|
| `AddTenant` | Открыть соединение, собрать роутер, зарегистрировать (double-check закрывает оба пула при дубликате) |
| `RemoveTenant` | **Двухфазный drain**: (1) под Lock удалить из мапы + `removing.Store(true)` → новые запросы 404; (2) drain соединений (SetMaxIdleConns(0) + poll InUse==0, лимит 2s); (3) закрыть оба пула |
| `GetTenant` | По id |
| `ListTenants` | Все |
| `ReloadTenant` | Пересобрать роутер из обновлённого конфига |
| `buildTenantInstance` | Создание instance: Conn → ReadOnlyConn |

### Admin API (`tenant_admin.go`)

Маршруты: `POST /admin/tenants`, `GET /admin/tenants`, `GET /admin/tenants/{id}`, `DELETE /admin/tenants/{id}`, `GET /admin/config`, `POST /admin/config`, `POST /admin/config/reload`, `GET /admin/config/versions`, `POST /admin/config/rewrite`, `GET /admin/discover`.

Auth: `AdminAuthMiddleware`, rate limit.

### Middleware (`server.go`)

- `RequestIDMiddleware`, `StructuredLoggingMiddleware`, `RecoveryMiddleware`, `BodyLimitMiddleware`, `AdminRateLimitMiddleware`, `ThrottleMiddleware`, `TenantIDMiddleware` (кладёт `X-Tenant-ID` в context).
- Конфигурация из cfg: `ResolveRequestTimeout` (30s), `ResolveBodyLimit`, `ResolveMaxConcurrent`. `configValue()` — nil-safe.
- **Подключены:** `BodyLimitMiddleware` — admin- и tenant-роутер; `ThrottleMiddleware` — rootRouter. Закрывают OOM на `/admin/*` и лимит конкурентности.

### Роутер из конфига (`NewRouterFromConfig`)

- Read-only guard: при `ReadOnly=true` write-методы (POST/PUT/PATCH/DELETE) не регистрируются.
- Маршрутизация стратегий: grep/filter через `search.NewGrepStrategy()/NewFilterStrategy()`, schema через `handlers.NewSchemaHandler` (DataSource) или fallback `NewStrategySchemaHandler`.
- `isWriteMethod()` — определяет мутирующие методы.

## configgen — генерация конфига (`internal/configgen/`)

### Pipeline

```
datasource.Adapter.Introspect() → Schema
  → configgen.Generate(schema, cfg)
    → shouldSkip (SkipRule) → entities
    → resolveFieldRules (FieldRules) → filterable/searchable/enum
    → buildCRUDEndpoints → REST endpoints
    → buildNavigationEndpoints (FK) → custom_queries
    → GenerateMCPTools → MCP manifest
```

### Generate

- SkipRules: `DefaultSkipRules()` (sqlite_/pg_/auth_/django_/migrations/documents и т.д.), кастомные из cfg.
- ReadOnly форс: если nil, ставит `&true`.
- FieldRules: `resolveFieldRules()` — Defaults − DisabledDefault + Custom.
- Условные эндпоинты `buildCRUDEndpoints()`:
  - count — если непустые non-PK поля
  - grep — если есть searchable string
  - filter — если есть filterable
  - schema — всегда
  - distinct — если enum-поля
- `buildCounters()`.

### Intent round-trip (`intent.go`)

- `TenantIntent` — только намерения: DataSource, правила (FieldRules/DisabledDefault*), CustomShortNames, explicit CustomQueries, Stats, Introspection, Auth, Server. Без Entities/Endpoints/MCPTools/derived CustomQueries — они вычислимы через Hydrate.
- `ExtractIntent(cfg)` — выделяет интенты из полного `config.Config`, исключая FK-derived запросы.
- `Hydrate(intent, schema)` — собирает полный `config.Config` из intent + свежей схемы (Generate + возврат explicit queries/Stats).
- Вызывается из: admin rewrite/discover handlers.

### FieldRules — фильтрация полей в filter/grep/distinct тулы

- Дефолты: `DefaultFilterableFieldRules()` — AllowNames: name, article, oem_number, description, price, old_price, category, brand, supplier, label, quantity, status, type, active.
- `DefaultSearchableFieldRules()` — block-only: `_image`, `_url`, image, thumbnail, json, seo.
- `DefaultEnumFieldRules()` — AllowContains: status, type, role, city, country.
- `IsFilterableField()` — имплицитные правила (FK `*_id` кроме tenant_id, `*_date`, is_available/is_active) + конфигурируемые.
- Тип `FieldRule` — `helperium-go/config/types.go`, `Matches()` — Allow OR (если непустые), Block OR (вето).
- Resolved правила доставляются в runtime (`mcp_manifest.go`, `endpoint_builder.go`).

### MCP tools (`mcp.go`)

- `GenerateMCPTools()` — эндпоинты → тулы.
- `strategyToMCPTool()` — grep/filter/schema тулы с параметрами из `Strategy.ToolParams()`.
- `deriveToolParams()` / `extractPathParams()` — вспомогательные.

### SchemaForLLM (`llm.go`)

- `SchemaForLLM` — карта сущностей, полей и FK для LLM.
- `LLMEntity` — Name, Description, SearchFields, FilterFields []FilterGroup, Relations []LLMRelation.
- `FilterGroup` / `FilterField` / `LLMRelation` — вспомогательные типы.
- `GenerateSchemaForLLM()` — обселиченное описание БД для system prompt (без SQL).

### Naming (`naming.go`)

`DefaultDisplayPrefixes()`, `shortBusinessName()`, `titleCase()`, `shortColumnName()`, `pluralizeEntity()`, `toolDisplayName()`.

## openapigen — OpenAPI 3.1 runtime (`services/helperium-go/openapigen/openapigen.go`)

> OpenAPI-генератор живёт в общем пакете `services/helperium-go/openapigen/`,
> чтобы admin-dashboard мог импортировать его в контрактных тестах без internal-ограничений.

- `Generate()` — из `cfg.Endpoints` на каждый запрос.
- `GenerateSystemSpec()` — system-only спека (health, admin-эндпоинты).
- Основные методы: `buildPaths()`, `buildComponents()`, `entitySchema()`, `openapiType()`, `operationID()`.

## Конфиг

```json
{
  "version": 4,
  "data_source": { "driver": "sqlite|postgres", "dsn": "...", "readonly_dsn": "...", "read_only": true },
  "entities": [...],
  "endpoints": [...],
  "custom_queries": {...},
  "auth": { "strategy": "header", "tenant_header": "X-Tenant-ID", "row_filters": [...] },
  "filterable_rules": [...], "searchable_rules": [...], "enum_rules": [...],
  "skip_rules": [...], "disabled_default_rules": [...],
  "custom_short_names": {...}
}
```

Типы: `Config`, `Entity`, `DataSourceConfig`, `AuthConfig`, `FieldRule` в `helperium-go/config`.

## Запуск

```bash
go build -o bin/data-service ./cmd/server/
./bin/data-service --config specs/config.example.json
```

Smoke:
```bash
curl -s http://127.0.0.1:8084/health
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/students
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

## Тесты

```bash
go test ./... -count=1
go test -race ./... -count=3
```

E2E (из корня проекта):
```bash
./infra/scripts/dev.sh start
ADMIN_TOKEN=secret .venv/bin/python -m pytest tests/e2e/test_mcp_validation.py -v
ADMIN_TOKEN=secret .venv/bin/python -m pytest tests/e2e/test_data_isolation.py -v
```

## Связь с сервисами

Полная HTTP-матрица: `doc/api-flow.md`. Ключевые каналы:

| Канал | Откуда → Куда | Контракт |
|---|---|---|
| MCP tool backend | mcp-gateway → data-service | `GET /mcp/manifest` (кэш 30s per tenant), затем `GET /{endpoint}` для каждого тула |
| LLM chat | api-service → mcp-gateway → data-service | api-service НЕ ходит напрямую; через mcp-gateway JSON-RPC → HTTP |
| Admin | admin-dashboard → data-service | `/admin/*` (tenant CRUD, rewrite, tools approval), `Authorization: Bearer {admin_token}` |
| Dev proxy | demo-web → data-service | `GET /{entity}` `/grep` `/filter` `/count`, `GET /mcp/manifest`, `GET /health` |
| Schema для LLM | data-service `GET /mcp/schema` → mcp-gateway → api-service | `configgen.GenerateSchemaForLLM` → system prompt |

### mcp-gateway → data-service (детально)

- `FetchConfigWithTenant(tenantID)` → `GET /mcp/manifest` — загружает манифест, кэширует 30s per tenant.
- `Call(ctx, endpoint, params)` → `GET /{endpoint}` — выполняет data-запрос (get/grep/filter/schema/count/distinct).
- Манифест генерируется runtime из `cfg.Endpoints`, не из дискового `cfg.MCPTools`.
- При rewrite конфига mcp-gateway инвалидирует кэш.
- `X-Tenant-ID: a,b` → composite mode, тулы с префиксом `{tenant}__grep_x`.

## Security

- Только SELECT, prepared statements (`?`/`$1`), `max_rows` для custom_query.
- `read_only: true` по умолчанию, write-методы не регистрируются.
- `ReadOnlyConn` блокирует `ExecContext`.
- `readonly_dsn` создаёт отдельное database-level read-only соединение для data path; PostgreSQL DSN должен использовать роль без write grants, SQLite — `file:...?...mode=ro&immutable=1`.
- Fixture публичной demo `testdata/scenarios/shop` использует `read_only: true` и `readonly_dsn: file:data.db?mode=ro&immutable=1`; URI разрешается относительно tenant config.
- Field whitelist: `Entity.FindColumn()` — незнакомые поля тихо скипаются.
- **Tenant-изоляция:**
  - `tenant_id` не доступен LLM (grep/filter).
  - `tenantFilter` строится из `auth.RowFilters` по entity.
  - `/stats` и `count` также применяют tenant-фильтр и исключают `tenant_id`.
- **Middleware**
  - **Body-лимит:** `BodyLimitMiddleware` — admin- и tenant-роутер.
  - **Конкурентность:** `ThrottleMiddleware` — rootRouter.
- **Лимиты:**
  - grep/filter strategies: `limit` ≤ 100, `offset` ≤ 100000.
  - `maxPatternLen=500`, `maxRegexLen=200`, `maxTokens=10`.
  - `maxFilters=15`, `maxInValues=50`, `maxFilterValueLen=200`.
  - `max_rows` для custom_query — из конфига (навигационные endpoints генерируются с 1000).
- Per-query timeout: 30s (`QUERY_TIMEOUT_SECONDS`).
- Ошибки БД → generic message + structured log (без утечки деталей).
- **Известный gap:** при `AuthStrategyHeader` без настроенных `RowFilters` tenant-фильтр не применяется. Регресс-тест: `row_filter_security_test.go`.

## Ссылки

- [Search strategies](doc/agents/search-strategies.md) — grep/filter/schema стратегии
- [Adapter pattern](doc/agents/adapter-pattern.md) — драйверы и интроспекция
- [Security isolation](doc/agents/security-isolation.md) — tenant isolation


---
**Last verified:** 2026-08-24 (working tree following `0add4ea`) — documentation restructure (P0-P5 sweep).
