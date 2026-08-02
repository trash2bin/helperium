# Data Service

Config-driven HTTP-доступ к клиентским БД. Схема описывается JSON-конфигом, из него собираются REST-эндпоинты, MCP-манифест и OpenAPI. Доменного кода нет.

Go/chi, порт 8084.

## Входные точки

- `cmd/server/main.go:56` — `main()`. Флаги: `--discover` (интроспекция → конфиг в stdout), `--config` (путь к JSON). Env: `PORT`, `DS_CONFIG`, `LOG_LEVEL`, `ADMIN_TOKEN`, `TENANTS_DIR`, `QUERY_TIMEOUT_SECONDS`.
- `cmd/server/main.go:239` — `runDiscover()`, CLI-режим интроспекции.
- `cmd/server/main.go:305` — `watchConfig()`, hot-reload конфига по fsnotify с дебаунсом 500ms.

## Пакеты

```
cmd/server/                    main, discover, watchConfig
internal/configgen/            генерация конфига из datasource.Schema; intent.go (ExtractIntent/Hydrate)
internal/datasource/           Conn/Adapter интерфейсы + драйверы (sqlite, postgres) + readonly
internal/query/                Expression AST → SQL (Engine)
internal/runtime/              AdapterSubset, EntityResolver, HTTP-хендлеры
internal/search/               стратегии grep/filter/schema (Strategy interface)
internal/server/               TenantStore, роутер, middleware, admin API
internal/openapigen/           runtime-генерация OpenAPI 3.1 из cfg.Endpoints
```

## datasource — интерфейсы и драйверы

### Conn / Adapter (`internal/datasource/adapter.go`)

- `Conn` (:30) — `QueryRowContext / QueryContext / ExecContext / PingContext / Close`.
- `Adapter` (:46) — `Driver()`, `Connect()`, `Introspect()`, `TranslatePlaceholder()`, `QuoteIdentifier()`.
- `Schema/Table/Column/ForeignKey` (:81-124) — результат интроспекции.

### Драйверы

- `sqlite_adapter.go:27` — `SqliteAdapter`. modernc.org/sqlite (без CGO). `Connect()` :42: WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `SetMaxOpenConns(2)`, `PRAGMA foreign_keys=ON` (и через DSN-параметры `_pragma=...` :90 — применяются к каждому коннекту). `Introspect()` :127 через sqlite_master + PRAGMA. `mapSQLiteType()` :292.
- `postgres_adapter.go:40` — `PostgresAdapter`. pgx/v5 через database/sql. `Introspect()` :126 — 4 запроса (таблицы, колонки+PK, описания, FK). `TranslatePlaceholder()` :82 — `?` → `$N`. `QuoteIdentifier()` :93 — двойные кавычки, экранирует `"` в сегментах. `mapPostgresType()` :377.
- `registry.go:13` — `NewDefaultRegistry()`: реестр драйверов по имени.

### ReadOnly (`internal/datasource/readonly.go`)

- `ReadOnlyDB` (:11) — обёртка над `*sql.DB`, только SELECT. `NewReadOnlyDB()` :16 проверяет при старте, что write падает.
- `ReadOnlyConn` (:41) — обёртка над `Conn`. `ExecContext()` :62 всегда возвращает ошибку. Используется для code-level гарантии: data path не пишет в БД.

### DataSource (абстракция для не-SQL бэкендов, `internal/datasource/datasource.go`)

- `DataSource` interface (:21) — `Search / Filter / GetByID / Count / Distinct / Schema / Close`.
- `SQLDataSource` (`sql.go:27`) — реализация поверх query.Engine. `NewSQLDataSource()` :42.
- **Важно:** `Search/Filter/GetByID/Count/Distinct` в SQLDataSource — заглушки (`sql.go:74-94`), возвращают не-implemented. Реально работают только `Schema()` :118. HTTP-стратегии идут через `search.Strategy`, не через DataSource.

## query — Expression AST → SQL (`internal/query/`)

- `expression.go:10` — `QueryPlan`: Select, From, Where []Condition, RawWhere, Order, Limit, Offset, Format.
- `expression.go:55` — `Condition`: Field, Operator, Value, Values, Not.
- `expression.go:72` — `Operator`: Eq, Neq, Lt, Gt, Lte, Gte, Like, ILike, NotLike, Regex, In, Between.
- `expression.go:102-159` — конструкторы `Eq/Neq/Lt/Lte/Gt/Gte/Like/ILike/Regex/NotLike/In/Between`.
- `builder.go:35` — `NewEngine(adapter)`. `Build()` :43, `BuildCount()` :50, `build()` :54, `RenderConditions()` :124, `renderCondition()` :156.
- `renderCondition()` :156 — инверсии операторов при `c.Not`: `OpEq+Not → "<>"` (:164), `OpRegex+Not → NOT REGEXP / !~`. Раньше `notPrefix="NOT "` давал невалидный SQL `"x" NOT = ?` (закреплён тестом как ожидание — переписан).
- LIKE/ILIKE/NOT LIKE — `ESCAPE '\'` (:232, :251, :261, :274): `QuoteString` экранирует `%`/`_` обратным слэшем, без ESCAPE-клаузы экранирование не работает (в SQLite `\` — литерал, wildcard оставался активным → данные с `%`/`_` не находились).
- `format.go:4` — `SearchResult`. `FormatRows()` :32 — compact/full/count форматы.
- **RawWhere остался в grep** (`search/grep.go:252-253`): multi-token AND собирается строкой. Tenant-фильтр для RawWhere — обёртка в подзапрос (`strategy_handler.go:235 insertTenantBeforeLimit`).

## search — стратегии (`internal/search/`)

### Strategy interface (`strategy.go:16`)

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

- `Adapter` (:46) — `QuoteIdentifier / QuoteString / TranslatePlaceholder / IsPostgres`.
- `NewAdapter(inner query.AdapterSubset)` :67 — мост runtime.AdapterSubset → search.Adapter.

### GrepStrategy (`grep.go:38`)

Multi-token AND по полям, OR между полями. Лимиты в `NewGrepStrategy()` :42-45:
- `maxRegexLen=200` (ReDoS), `maxTokens=10`, `maxPatternLen=500`.

`ToolParams()` :75 — `pattern` (required), `limit` (1-100, default 10), `fields`.
`ParseRequest()` :91 — проверка длины :99, regex :109, token search :220+.

Параметры MCP: `pattern`, `ignore_case`, `fields`, `invert`, `regex`, `limit`, `offset`, `format`, `sort_by` (последние не в JSON Schema).

### FilterStrategy (`filter.go:33`)

`NewFilterStrategy(idCol, nameCol string, filterableRules ...config.FieldRule)` :33. Лимиты:
- `maxFilterValueLen=200` (:26), `maxInValues=50` (:28), `maxFilters=15` (:44).

`ToolParams()` :86 — фильтрует поля через `config.IsFilterableField(field, s.filterableRules)` :99.
`ParseRequest()` :165 — exact / `__gt` / `__gte` / `__lt` / `__lte` / `__like` / `__in` / `__neq`; maxFilters :299, maxInValues :269.

### SchemaStrategy (`schema.go:25`)

`ToolParams()` :48 — nil (без параметров). `ParseRequest()` :54 — nil (не использует Engine; handler работает напрямую с БД). `FormatFields()` :79.

## runtime — типы и хендлеры

### Типы (`internal/runtime/types.go`)

- `AdapterSubset` (:140) — урезанный Conn+Adapter: QueryContext, QuoteIdentifier, TranslatePlaceholder, PingContext.
- `Entity/EntityField` (:34/:60), `CustomQuery` (:85), `Endpoint` (:116).
- `EntityResolver` (`entity_resolver.go:23 NewEntityResolver`, `Resolve` :38, `ColumnFor` :48) — резолв entity по имени.

### HTTP-хендлеры (`internal/runtime/handlers/`)

| Файл | Функция | Назначение |
|---|---|---|
| `get_by_id.go:9` | `GetByIDHandler` | `GET /{entity}/{id}` |
| `count.go:17` | `CountHandler` | `GET /{entity}/count` |
| `distinct.go:15` | `DistinctHandler` | `GET /{entity}/distinct?column=` |
| `custom_query.go:19` | `CustomQueryHandler` | `GET /{parent}/{id}/{child}` (whitelist SELECT) |
| `stats.go:11` | `StatsHandler` | `GET /stats` — count по `Stats.Counters` из конфига (с tenant-фильтром, см. ниже) |
| `strategy_handler.go:30` | `NewStrategyHandler` | grep/filter стратегии (collectEmptyHint :175, insertTenantBeforeLimit :235) |
| `schema_handler.go:23` | `NewStrategySchemaHandler` | schema стратегия |
| `mcp_manifest.go:20` | `MCPManifestHandler` | `GET /mcp/manifest` |
| `context.go:40-63` | `queryCtx` :40 / `tenantID` :48 / `RespondJSON` :56 / `RespondError` :63 | общие утилиты |

### Tenant-фильтр (`row_filter.go`)

- `tenantFilter()` :22 — вставляет `WHERE` из `auth.RowFilters` по entity. Возвращает `("", nil)` если: auth nil, Strategy != header, tenantID пуст, нет RowFilter для сущности.
- `insertTenantBeforeLimit()` (`strategy_handler.go:235`) — перестановка args: WHERE + tenant + LIMIT/OFFSET.
- `/stats` (`stats.go:36`): `StatsHandler` вызывает `tenantFilter` для каждого counter — иначе в multi-tenant `/stats` отдавал глобальные счётчики (cross-tenant leak). `tenantWhere` конкатенируется поверх `counter.Filter`.
- `CountHandler` (`count.go:37-42`): `tenant_id` исключён из fieldMap и из системных параметров (:54) — HIGH-15 fix, защита от фильтрации по чужому tenant_id.
- **Security Gap:** при `AuthStrategyHeader` без настроенных `RowFilters` — tenant-фильтр не применяется, запрос вернёт все строки. Регресс-тест: `row_filter_security_test.go`.

### Schema handler (`schema_handler.go`)

`distinctValues()` :103 (до 20 значений), `fieldStats()` :135 (min/max/avg). Обходит tenantWhere через параметры.

### Маппинг значений (`response_mapper.go`)

- `coerceNative()` :210 — приведение значения к типу колонки из конфига. Для `int`: `safeFloatToInt64()` :167 — при дробном или out-of-range значении НЕ кастует молча, а возвращает float64 с `slog.Warn` (раньше `int64(95.7)` → 95 тихо).
- `datetime`/`date` (:280-289) — канонический `RFC3339`: `time.Time` → `UTC().Format(RFC3339)`, строка → `normalizeDateTime()` :181 (sqlite-формат `"2006-01-02 15:04:05"`, date `"2006-01-02"`). Раньше формат зависел от драйвера (sqlite — string, pgx — time.Time).
- `DistinctHandler` (`distinct.go:26`): типизированное сканирование через `columnFieldType()` :12 — числовые колонки отдают числа, bool — bool (раньше всё `sql.NullString` → строки).

## server — TenantStore, роутер, middleware

### TenantStore (`tenant.go`)

- `TenantInstance` (:40) — ID, Config, Conn, ReadonlyConn, Adapter, Router, ApprovedTools, IntrospectedSchema. Доп. поля синхронизации: `healthMu` (Healthy/LastError), `schemaMu` (RWMutex для IntrospectedSchema), `removing` (atomic.Bool — RemoveTenant в процессе).
- `TenantStore` (:65) — мапа id→instance, RWMutex. `ServeHTTP` :267 — роутинг по X-Tenant-ID (держит RLock через `resolveTenantAndLock` на весь запрос).
- `resolveTenantAndLock()` :322 — резолв с удержанием RLock на весь запрос (закрывает TOCTOU с ReloadTenant/RemoveTenant). `resolveTenant()` :304 — без лока (для admin-хендлеров, с проверкой `removing`).
- `NewTenantStore(registry, tenantsDir)` :78.
- Персистентность: `SaveTenantConfig()` :115 — **атомарная запись** (temp-файл + `os.Rename`, битый JSON невозможен), `DeleteTenantConfig()` :244 — удаляет и `{id}.json`, и `{id}.schema.json`, `TenantConfigPath()` :90. Директория `$TENANTS_DIR` (default `.data/tenants/`).
- Schema cache: `TenantSchemaPath()` :169, `SaveTenantSchema()` :177, `LoadTenantSchema()` :196 (нет файла → (nil, nil)), `PersistTenantConfig()` :225 — регенерирует Entities/Endpoints из intent+закэшированной схемы (fallback: сохраняет как есть).

### Lifecycle (`tenant_lifecycle.go`)

| Функция | Строка | Назначение |
|---|---|---|
| `AddTenant` | :42 | Открыть соединение, собрать роутер, зарегистрировать (double-check закрывает оба пула при дубликате) |
| `RemoveTenant` | :85 | **Двухфазный drain**: (1) под Lock удалить из мапы + `removing.Store(true)` → новые запросы 404; (2) `drainTenantConns` :113 — SetMaxIdleConns(0) + poll `Stats().InUse==0` (лимит 2s, fallback grace period); (3) `closeTenantConns` :162 — закрыть оба пула |
| `GetTenant` | :179 | По id |
| `ListTenants` | :187 | Все |
| `ReloadTenant` | :202 | Пересобрать роутер из обновлённого конфига (под Lock, гонка закрыта `resolveTenantAndLock`) |
| `buildTenantInstance` | :244 | Создание instance: Conn → ReadOnlyConn, ApprovedTools из cfg, инициализация healthMu/schemaMu |

### Admin API (`tenant_admin.go:28 BuildAdminRouter`)

Маршруты: `POST /admin/tenants` :48, `GET /admin/tenants` :49, `GET /admin/tenants/{id}` :50, `DELETE /admin/tenants/{id}` :51, `GET /admin/config` :54, `POST /admin/config` :55, `POST /admin/config/reload` :56, `GET /admin/config/versions` :57, `POST /admin/config/rewrite` :58, `GET /admin/discover` :62, `GET /admin/tenants/{id}/tools/pending` :66, `POST /admin/tenants/{id}/tools/{toolName}/approve` :67.

Auth: `AdminAuthMiddleware` (`admin.go:83`), rate limit (`tenant_admin.go:45`).

### Middleware (`server.go`)

- `RequestIDMiddleware` :28, `StructuredLoggingMiddleware` :42, `RecoveryMiddleware` :70, `BodyLimitMiddleware` :90, `AdminRateLimitMiddleware` :163, `ThrottleMiddleware` :184, `TenantIDMiddleware` :283 (кладёт X-Tenant-ID в context).
- Конфигурация из cfg: `ResolveRequestTimeout` :208 (30s), `ResolveBodyLimit` :221, `ResolveMaxConcurrent` :235. `configValue()` :262 — nil-safe для cfg.
- **Подключены:** `BodyLimitMiddleware` в admin-роутере (`tenant_admin.go:44`) и tenant-роутере (`endpoint_builder.go:66`); `ThrottleMiddleware` в rootRouter (`cmd/server/main.go:186`). Раньше были только определения — теперь закрывают OOM на `/admin/*` и лимит конкурентности.

### Роутер из конфига (`endpoint_builder.go:29 NewRouterFromConfig`)

- Read-only guard: :116-147 — при `ReadOnly=true` write-методы (POST/PUT/PATCH/DELETE) не регистрируются (:147), кроме `approvedTools[ep.Path]`.
- Маршрутизация стратегий: :150+ — grep/filter через `search.NewGrepStrategy()/NewFilterStrategy()`, schema через `handlers.NewSchemaHandler` (DataSource) или fallback `NewStrategySchemaHandler`.
- `isWriteMethod()` :257.

## configgen — генерация конфига (`internal/configgen/`)

### Pipeline

```
datasource.Adapter.Introspect() → Schema
  → configgen.Generate(schema, cfg) (configgen.go:83)
    → shouldSkip (SkipRule) → entities
    → resolveFieldRules (FieldRules) → filterable/searchable/enum
    → buildCRUDEndpoints → REST endpoints
    → buildNavigationEndpoints (FK) → custom_queries
    → GenerateMCPTools → MCP manifest
```

### Generate (`configgen.go:83`)

- SkipRules: `DefaultSkipRules()` :30 (sqlite_/pg_/auth_/django_/migrations/documents и т.д.), кастомные из cfg.
- ReadOnly форс: :115-116 — если nil, ставит `&true`.
- FieldRules: `resolveFieldRules()` :201 — Defaults − DisabledDefault + Custom.
- Условные эндпоинты `buildCRUDEndpoints()` :224:
  - count — если `hasDataFields` (columns.go:70)
  - grep — если `hasSearchableFields` (columns.go:18)
  - filter — если `hasFilterableFields` (columns.go:51)
  - schema — всегда
  - distinct — если enum-поля (findEnumColumnsFromEntity columns.go:81)
- `buildCounters()` :313.

### Intent round-trip (`intent.go`)

- `TenantIntent` :13 — только намерения: DataSource, правила (FieldRules/DisabledDefault*), CustomShortNames, explicit CustomQueries, ApprovedTools, Stats, Introspection, Auth, Server. Без Entities/Endpoints/MCPTools/derived CustomQueries — они вычислимы через Hydrate.
- `ExtractIntent(cfg)` :66 — выделяет интенты из полного `config.Config`, исключая FK-derived запросы (`DerivedCustomQueryKeys` :55).
- `Hydrate(intent, schema)` :108 — собирает полный `config.Config` из intent + свежей схемы (Generate + возврат explicit queries/Stats/ApprovedTools).
- Вызывается из: `tenant_admin.go:521` (rewrite), `tenant_admin.go:615` (discover), `tenant.go:238` (PersistTenantConfig).

### FieldRules (прокси в `columns.go:12-14` → `helperium-go/config/filterable.go`)

- `DefaultFilterableFieldRules()` (filterable.go:9) — AllowNames: name, article, oem_number, description, price, old_price, category, brand, supplier, label, quantity, status, type, active.
- `DefaultSearchableFieldRules()` (filterable.go:24) — block-only: `_image`, `_url`, image, thumbnail, json, seo.
- `DefaultEnumFieldRules()` (filterable.go:36) — AllowContains: status, type, role, city, country.
- `IsFilterableField()` (filterable.go:54) — имплицитные правила (FK `*_id` кроме tenant_id, `*_date`, is_available/is_active) + конфигурируемые.
- Тип `FieldRule` — `helperium-go/config/types.go:564`, `Matches()` :591.
- Runtime использует resolved правила: `mcp_manifest.go:33` и `endpoint_builder.go:190` вызывают `configgen.ResolveFieldRules(...)` (ранее кастомные FilterableRules/DisabledDefault* не доходили до runtime — всегда дефолты).

### MCP tools (`mcp.go`)

- `GenerateMCPTools()` :12 — эндпоинты → тулы.
- `strategyToMCPTool()` :137 — grep/filter/schema тулы с параметрами из `Strategy.ToolParams()`.
- `deriveToolParams()` :93, `extractPathParams()` :118.

### SchemaForLLM (`llm.go`)

- `SchemaForLLM` :15, `LLMEntity` :26, `FilterGroup` :49, `FilterField` :58, `LLMRelation` :68.
- `GenerateSchemaForLLM()` :83 — обселиченное описание БД для system prompt (без SQL).

### Naming (`naming.go`)

`DefaultDisplayPrefixes()` :12, `shortBusinessName()` :19, `titleCase()` :48, `shortColumnName()` :56, `pluralizeEntity()` :67, `toolDisplayName()` :107.

## openapigen — OpenAPI 3.1 runtime (`internal/openapigen/openapigen.go`)

- `Generate()` :19 — из cfg.Endpoints на каждый запрос.
- `buildPaths()` :394, `buildComponents()` :645, `entitySchema()` :726, `openapiType()` :749, `operationID()` :768.

## Конфиг

```json
{
  "version": 3,
  "data_source": { "driver": "sqlite|postgres", "dsn": "...", "read_only": true },
  "entities": [...],
  "endpoints": [...],
  "custom_queries": {...},
  "auth": { "strategy": "header", "tenant_header": "X-Tenant-ID", "row_filters": [...] },
  "filterable_rules": [...], "searchable_rules": [...], "enum_rules": [...],
  "skip_rules": [...], "disabled_default_rules": [...],
  "custom_short_names": {...}
}
```

Типы: `helperium-go/config/types.go:176 Config`, `:290 Entity`, `:254 DataSourceConfig`, `:512 AuthConfig`, `:564 FieldRule`.

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
./scripts/dev.sh start
ADMIN_TOKEN=secret .venv/bin/python -m pytest tests/e2e/test_mcp_validation.py -v
ADMIN_TOKEN=secret .venv/bin/python -m pytest tests/e2e/test_data_isolation.py -v
```

## Связь с сервисами

Полная HTTP-матрица: `doc/api-flow.md`. Ключевые каналы:

| Канал | Откуда → Куда | Контракт |
|---|---|---|
| MCP tool backend | mcp-gateway → data-service | `GET /mcp/manifest` (кэш 30s в `mcp-gateway/internal/httpclient/client.go:206-248`), затем `GET /{endpoint}` для каждого тула |
| LLM chat | api-service → mcp-gateway → data-service | api-service НЕ ходит напрямую; через mcp-gateway JSON-RPC → HTTP |
| Admin | admin-dashboard → data-service | `/admin/*` (tenant CRUD, rewrite, tools approval), `Authorization: Bearer {admin_token}` |
| Dev proxy | demo-web → data-service | `GET /{entity}` `/grep` `/filter` `/count`, `GET /mcp/manifest`, `GET /health` |
| Schema для LLM | data-service `GET /mcp/schema` → mcp-gateway → api-service | `configgen.GenerateSchemaForLLM` → system prompt |

### mcp-gateway → data-service (детально)

Источник: `mcp-gateway/internal/httpclient/client.go`.

- `FetchConfigWithTenant(tenantID)` → `GET /mcp/manifest` — загружает манифест, кэширует 30s per tenant.
- `Call(ctx, endpoint, params)` → `GET /{endpoint}` — выполняет data-запрос (get/grep/filter/schema/count/distinct).
- Манифест генерируется runtime из cfg.Endpoints (`mcp_manifest.go:20`), не из дискового cfg.MCPTools.
- При rewrite конфига mcp-gateway инвалидирует кэш.
- `X-Tenant-ID: a,b` → composite mode, тулы с префиксом `{tenant}__grep_x`.

## Security

- Только SELECT, prepared statements (`?`/`$1`), `max_rows` для custom_query.
- `read_only: true` по умолчанию (`configgen.go:115`), write-методы не регистрируются (`endpoint_builder.go:138`).
- `ReadOnlyConn` блокирует `ExecContext` (`readonly.go:62`).
- Field whitelist: `Entity.FindColumn()` (`types.go:325`), незнакомые поля тихо скипаются.
- Tenant isolation: `tenant_id` не доступен LLM (grep.go:130, filter.go:205); `tenantFilter` из auth.RowFilters; `/stats` (`stats.go:36`) и `count` (`count.go:37-42`) тоже применяют tenant-фильтр / исключают tenant_id.
- Body-лимит и конкурентность: `BodyLimitMiddleware` подключён в admin- (`tenant_admin.go:44`) и tenant-роутере (`endpoint_builder.go:66`), `ThrottleMiddleware` в rootRouter (`cmd/server/main.go:186`).
- Лимиты: стратегии (grep/filter) режут limit до 100 (`parseLimitParam` strategy_common.go:84), общий пагинационный — до 1000 (`readPagination` pagination.go:16), maxPatternLen=500, maxRegexLen=200, maxTokens=10, maxFilters=15, maxInValues=50, maxFilterValueLen=200.
- Per-query timeout: 30s (`QUERY_TIMEOUT_SECONDS`).
- Ошибки БД → generic message + structured log (без утечки деталей).
- **Известный gap:** RowFilters не обязателен при AuthStrategyHeader — без него tenant-изоляции нет. Регресс-тест: `runtime/handlers/row_filter_security_test.go`.
