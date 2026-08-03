# configgen — генерация конфига data-service из интроспекции БД

Пакет `data-service/internal/configgen`. Превращает `datasource.Schema` (результат `Adapter.Introspect()`) в декларативный `config.Config` с эндпоинтами, MCP-тулами и навигацией. Не выполняет I/O — чистая функция.

## Входные точки

- `Generate(schema *datasource.Schema, cfg *config.Config) *config.Config` — `configgen.go:83`. Главный оркестратор.
- `GenerateSchemaForLLM(schema, cfg) *SchemaForLLM` — `llm.go:83`. Обселиченное описание БД для system prompt.
- `GenerateMCPTools(endpoints, entities, displayPrefixes, customPlurals, filterableRules...)` — `mcp.go:14`. Эндпоинты → MCP-манифест.
- `ExtractIntent(cfg) *TenantIntent` — `intent.go:66`. Полный конфиг → только намерения (правила, кастомизации, explicit custom queries).
- `Hydrate(intent, schema) *config.Config` — `intent.go:108`. Intent + schema → полный конфиг (Entities/Endpoints/MCPTools пересобираются).
- `DefaultSkipRules()` — `configgen.go:30`.

Вызывается из: `server/tenant_admin.go:478` (adminRewriteHandler) и `cmd/server/main.go:238` (runDiscover). `ExtractIntent`/`Hydrate` — из `server/tenant_admin.go:525` (rewrite), `:621` (discover), `server/tenant.go:238` (PersistTenantConfig).

## Pipeline Generate (`configgen.go:83`)

```
1. shouldSkip(schema.Tables)          — SkipRule фильтрация (configgen.go:57)
2. tableToEntity(table)               — Table → Entity (entity.go:18)
3. resolveFieldRules(defaults, disabled, custom) — FieldRules resolution (configgen.go:206)
4. buildCRUDEndpoints(entities, rules) — REST endpoints (configgen.go:257)
5. buildNavigationEndpoints(entities)  — FK → custom_queries (navigation.go:20)
6. buildCounters(entities)             — stats counters (configgen.go:346)
7. GenerateMCPTools(...)               — MCP manifest (mcp.go:14)
```

## SkipRules (`configgen.go:30-57`)

`DefaultSkipRules()` — системные таблицы фреймворков:

| Паттерн | Причина |
|---|---|
| `sqlite_` | SQLite system |
| `pg_`, `pg_catalog`, `information_schema` | PostgreSQL system |
| `auth_`, `django_` | Django |
| `session` | Django session |
| `documents` | Helperium RAG internal |
| `migrations` | Laravel |
| `jobs`, `failed_jobs` | Laravel queue |
| `schema_migrations`, `ar_internal_metadata` | Rails |

`shouldSkip(name, rules, legacyPrefixes)` :57 — AND-матчинг непустых полей SkipRule. `SkipRule` — `helperium-go/config/types.go:543`.

Кастомизация: `cfg.SkipRules` (дополняет), `cfg.DisabledDefaultRules` (отключает по prefix).

## FieldRules — фильтрация полей в filter/grep/distinct тулы

### Механика

- Тип `FieldRule` — `helperium-go/config/types.go:565`: AllowNames/AllowSuffix/AllowContains/BlockNames/BlockSuffix/BlockContains/Reason.
- `Matches(name)` — `types.go:584`: Allow — OR (если непустые), Block — OR (вето).
- Дефолты и логика — `helperium-go/config/filterable.go`:
  - `DefaultFilterableFieldRules()` :9 — AllowNames: name, article, oem_number, description, price, old_price, category, brand, supplier, label, quantity, status, type, active.
  - `DefaultSearchableFieldRules()` :24 — block-only: `_image`, `_url`, image, thumbnail, json, seo.
  - `DefaultEnumFieldRules()` :36 — AllowContains: status, type, role, city, country.
  - `IsFilterableField(field, rules)` :54 — имплицитные правила (FK `*_id` кроме tenant_id, `*_date`, булы is_available/is_active) + конфигурируемые.
- Прокси в configgen: `columns.go:12-14` (`DefaultFilterableFieldRules` и т.д.).

### Resolution (`configgen.go:206`)

`resolveFieldRules(defaults, disabledPrefixes, custom)` — дефолты минус отключённые (матч по Reason-префиксу, как SkipRule) плюс кастомные из конфига. Результат записывается обратно в `result.*Rules` для переживания reload.

### Условная генерация эндпоинтов (`configgen.go:257 buildCRUDEndpoints`)

| Endpoint | Условие | Функция |
|---|---|---|
| `/{entity}/count` | non-PK поля есть | `hasDataFields` (columns.go:70) |
| `/{entity}/grep` | есть searchable string | `hasSearchableFields` (columns.go:18) |
| `/{entity}/filter` | есть filterable | `hasFilterableFields` (columns.go:51) |
| `/{entity}/schema` | всегда | — |
| `/{entity}/distinct` | enum-поля есть | `findEnumColumnsFromEntity` (columns.go:81) |

Эффект: таблицы без подходящих полей не получают пустые тулы.

## MCP tools (`mcp.go`)

- `GenerateMCPTools()` :12 — проходится по эндпоинтам, для каждого создаёт `config.MCPTool`.
- `strategyToMCPTool()` :137 — grep/schema: параметры берутся из `Strategy.ToolParams()` (search/strategy.go:32), не из дискового конфига.
- `deriveToolParams()` :93 — параметры для не-strategy тулов (get_by_id, custom_query).
- `extractPathParams()` :118 — `{id}` из пути.

Манифест runtime: `runtime/handlers/mcp_manifest.go:20` регенерирует тулы из cfg.Endpoints на каждый запрос (не кэширует на диске).

**Поверхность тулов (Фаза 2/2.5 — LLM-first):**
- **5 консолидированных `db_*`** — `db_map`, `db_describe`, `db_search`, `db_get`, `db_related` (O(1), не зависят от числа сущностей; указывают на `/q/*` диспетчер, см. `runtime/handlers/q_dispatch.go`). `db_filter` НЕ существует.
- **N пер-энтити `filter_{entity}`** — фильтрация с именами полей прямо в схеме тула (`Strategy.ToolParams()` из `FilterStrategy` с resolved FilterableRules). Причина деконсолидации: слабая модель не вытаскивала имена полей из db_map и не могла вызвать консолидированный filter. Валидировано живой моделью Ollama (`filter_products?price__gt=100` с первого раза).
- **`get_*`/`count_*`/`distinct_*` по умолчанию НЕ эмитятся** — opt-in через `config.LLMToolPolicy` (`ExposeGetByID`/`ExposeCount`/`ExposeDistinct`, default false). Анти-перебор по id.
- `db_map` **fallback**: при `schema==nil` (после рестарта до rewrite) `GenerateSchemaForLLM` строит карту из `cfg.Entities` (FK из `Relations`) — db_map не отдаёт 503.

**Кастомные FilterableRules доходят до runtime** (было: всегда дефолтные):
- `runtime/handlers/mcp_manifest.go:33` — `GenerateMCPTools(..., configgen.ResolveFieldRules(DefaultFilterableFieldRules(), cfg.DisabledDefaultFilterableRules, cfg.FilterableRules)...)`.
- `server/endpoint_builder.go:190` — filter-эндпоинт: `NewFilterStrategy(idCol, nameCol, filterableRules...)` через `ResolveFieldRules` (пересчёт на каждый запрос, т.к. PUT /admin/config может править правила без регенерации).
- `ResolveFieldRules` экспортирован: `configgen.go:195`.

## SchemaForLLM (`llm.go`)

- `SchemaForLLM` :15 — Entities + WorkflowHints.
- `LLMEntity` :26 — Name, Description, SearchFields, FilterFields []FilterGroup, Relations []LLMRelation.
- `GenerateSchemaForLLM()` :83 — группирует фильтры по типам (bool/range/exact), добавляет workflow hints.
- **WorkflowHints** (доменно-нейтральные, ссылаются только на реальные тулы): `db_map` (карта), `db_search` (текст), `filter_<entity>` (точные значения — имена полей в схеме тула), `db_describe` (значения/диапазоны), `db_get` (id из результата поиска). Никаких `db_filter`, `get_*`, доменных слов (Bosch/KYB/brake pads) и `{entity}`-литералов.
- **Fallback при `schema==nil`** — карта строится из `cfg.Entities` (FK-индекс из `Relations`, `llm.go`). Покрыт тестом `TestSchemaForLLM_NilSchema_FallbackToEntities`.

Формат — текстовое описание без SQL-типов и системных таблиц. Потребляется api-service через mcp-gateway `GET /mcp/schema` → system prompt.

## Naming (`naming.go`)

- `DefaultDisplayPrefixes()` :12 — `["catalog_", "auth_", "django_"]`.
- `shortBusinessName()` :19 — `catalog_Product` → `Product` (с учётом `customShortNames`).
- `pluralizeEntity()` :67 — с учётом `customPlurals`.
- `toolDisplayName()` :107 — display_name тула.
- `titleCase()` :48 — unicode-safe: `[]rune` (а не `s[:1]`, который ломал кириллицу), `strings.ToUpper(r[0]) + string(r[1:])`.
- `shortColumnName()` :56.

## Intent (`intent.go`)

`TenantIntent` (:13) — единственный источник правды на диске: только намерения, без производного. `Entities/Endpoints/MCPTools/derived CustomQueries/Meta/Version` сюда НЕ входят — они вычислимы из intent + схемы БД через `Hydrate()`.

- `ExtractIntent(cfg)` :66 — полный `config.Config` → `TenantIntent`: правила (FieldRules, DisabledDefault*), DisplayPrefixes, CustomPlurals/ShortNames, explicit `CustomQueries` (без FK-derived), Stats, Auth/Server/Introspection.
- `DerivedCustomQueryKeys(entities)` :55 — ключи, которые генерит `buildNavigationEndpoints` (FK). Классификация explicit vs derived — по ключу из ТЕКУЩЕЙ схемы.
- `Hydrate(intent, schema)` :108 — intent + schema → полный конфиг: `Generate(schema, genCfg)` + возврат intent-полей. Explicit queries мержатся после Generate; коллизия explicit/derived решается по SQL:
  - SQL идентичен авто-паттерну (`SELECT t.* FROM {t} t WHERE t.{fk} = ?`) → это протухший derived (FK удалили, ключ выпал из набора) — отбрасывается (`intent.go:159-161`).
  - SQL отличается → пользовательская кастомизация, сохраняется с warn (`intent.go:163`).
  - `Stats.Counters` фильтруются по реально сгенерированным entities — counter на несуществующую сущность отбрасывается с warn (иначе `Config.Validate` убьёт конфиг, `intent.go:124-143`).
- **Edge case** (закомментирован в `intent.go:46-54`): при удалении FK протухший авто-запрос остаётся в конфиге и классифицируется как explicit (ключа нет в новом derived-наборе) — переживает Hydrate до коллизии.

## Navigation (`navigation.go`)

`buildNavigationEndpoints()` :20 — для каждого FK отношения создаёт:
- queryID: `{child}_by_{parent}_{fk_col}`
- SQL: `SELECT t.* FROM {child} t WHERE t.{fk_col} = ?`
- endpoint: `GET /{parent}/{id}/{child}`, op custom_query

**MCP-тулы для навигации НЕ генерируются (v4).** Relationship-тулы `_by_` удалены в v4 (commit 1de916e): LLM навигирует по FK через `filter_{child}({fk_field}=...)` — тот применяет tenant-фильтр, не имеет капа 1000 строк и поддерживает `__in`. Навигационный custom_query остаётся только REST-эндпоинтом (без tenant-фильтра, лимит 1000 — `custom_query.go`).

**Валидация идентификаторов** (`navigation.go:16`): `safeIdentRe = ^[A-Za-z_][A-Za-z0-9_]*$`. navigation генерирует SQL напрямую без `QuoteIdentifier` (runtime `BuildCustomQuery` не квотирует) — имена с пробелами/`"`/`;`/дефисами сломали бы SQL или дали инъекцию из имени БД. Небезопасные имена пропускаются с `slog.Warn` (FK-колонка :31-34, имя таблицы :64-67). Schema-qualified PG-имена (`public.brands`) тоже пропускаются — навигация не умеет безопасно квотировать `schema.table`.

## Файлы пакета

```
configgen.go          SkipRules + Generate + resolveFieldRules + buildCRUDEndpoints + buildCounters
columns.go            FieldRules прокси + hasSearchable/hasFilterable/hasDataFields/findEnumColumns
entity.go             tableToEntity (Table → Entity)
naming.go             форматирование имён (titleCase unicode-safe :48)
navigation.go         FK → custom_queries (safeIdentRe-валидация :16)
intent.go             TenantIntent/ExtractIntent/Hydrate (intent-модель)
llm.go                SchemaForLLM
mcp.go                GenerateMCPTools
configgen_test.go     unit-тесты
integration_test.go   интеграция против реальной БД (-tags=integration)
fieldrules_integration_test.go  FieldRules-интеграция
debug_test.go, debug_custom_rules_test.go  dev-скрипты отладки правил
```

## Тесты

```bash
go test ./internal/configgen/... -count=1                          # unit
go test -tags=integration ./internal/configgen/ -run TestAutoparts -v   # интеграция (нужен PG)
```

## Related

- `data-service/README.md` — обзор сервиса, связи с mcp-gateway/api-service
- `doc/agents/search-strategies.md` — стратегии grep/filter/schema детально
- `helperium-go/config/types.go` — Config/Entity/Endpoint/MCPTool/FieldRule типы
- `doc/api-flow.md` — HTTP-матрица
