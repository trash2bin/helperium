# OpenAPI-спецификации сервисов + конфигурация

`specs/` содержит декларативные контракты между сервисами и документацию
к формату конфига.

## File layout

```
specs/
├── config.schema.md           # Человеко-читаемое руководство (архив — валидация в Go-типах)
├── config.example.json        # Пример конфига SQLite (shop scenario)
├── config.postgres.json       # Пример конфига PostgreSQL (production-шаблон)
├── api.openapi.yaml           # OpenAPI api-service сервера (порт 8081)
├── rag.openapi.yaml           # OpenAPI rag-сервиса (порт 8082)
├── fixtures/                  # seed.json для data-service --seed (.gitignore)
└── README.md
```

---

## Как работает конфиг data-service (runtime)

Конфиг — корневой артефакт, описывающий **клиентскую БД** и то, как
data-service должен с ней работать: какие таблицы доступны, какие REST
эндпоинты публикуются, какие SQL-запросы разрешены.

### 1. Создание конфига

**Два пути:**

#### A. Интроспекция БД (рекомендуется)

```
POST /admin/config/rewrite
X-Tenant-ID: <tenant_id>
Authorization: Bearer <admin_token>
```

→ `adminRewriteHandler()` → [`data-service/internal/configgen/configgen.go`](../data-service/internal/configgen/configgen.go)

1. `adapter.Connect(ctx, dsn)` — коннект к БД tenant'а
2. `adapter.Introspect(ctx, conn)` — читает схему (таблицы, колонки, PK)
3. `configgen.Generate(schema, dsConfig, skipPrefixes)` — генерирует:
   - `entities[]` — по одной на таблицу
   - `endpoints[]` — `GET /{entity}/{id}`, `GET /{entity}` (find по name)
   - `mcp_tools[]` — `get_{entity}`, `distinct_{entity}`, `count_{entity}` (op-based) + `grep_{entity}`, `filter_{entity}`, `schema_{entity}` (strategy-based). `find_{entity}` **НЕ генерируется** как MCP-тул — эндпоинты find существуют как REST, но `GenerateMCPTools()` их скипает (см. [`data-service/internal/configgen/mcp.go`](../data-service/internal/configgen/mcp.go))
   - `stats.counters[]` — по одному счётчику на entity
   - `read_only: true` — по умолчанию (защита от записи)
4. `SaveTenantConfig()` → пишет `.data/tenants/{id}.json`
5. `ReloadTenant()` — пересоздаёт роутер без рестарта

#### B. Pучное создание через admin API

```bash
POST /admin/tenants
Authorization: Bearer <admin_token>
{
  "id": "my-tenant",
  "config": { ... полный конфиг JSON ... }
}
```

→ `adminAddTenantHandler()` → `config.Load()` из файла или `config.Validate(raw)` из тела запроса.
Сохраняется в `.data/tenants/{id}.json`.

**Третий способ (bootstrap):**
- `$DS_CONFIG` env → загружается как tenant `"default"`
- `$TENANTS_DIR` (`.data/tenants/`) → все `.json` файлы восстанавливаются при старте

---

### 2. Загрузка конфига

Путь загрузки: [`helperium-go/config/loader.go`](../helperium-go/config/loader.go)

```
Load(path string) (*Config, error)
```

Конвейер:

```
ReadFile(path) → Envsubst() → json.Unmarshal() → cfg.Validate()
```

1. **os.ReadFile** — сырые байты с диска
2. **Envsubst** — подстановка `$ENV` / `${ENV:-default}` из окружения
3. **json.Unmarshal** — типизированный парсинг в `Config` (types.go)
4. **cfg.Validate()** — семантическая валидация на Go-типах

**Никакой внешний JSON Schema файл больше не требуется.**
Валидация живёт в Go-коде: [`helperium-go/config/types.go`](../helperium-go/config/types.go) — метод `Config.Validate()`.

---

### 3. Потребители конфига

| Потребитель | Как получает | Что делает |
|---|---|---|
| **data-service** (Go, :8084) | `config.Load()` с диска + `SaveTenantConfig()` | Строит роутер (`chi`), хендлеры данных, middleware, query builder |
| **mcp-gateway** (Go, :8083) | HTTP `GET /mcp/manifest?tenant=...` к data-service | Только `json.Decode()` — без валидации. Строит MCP-инструменты из `Entities`+`Endpoints` |
| **api-service** (Python, :8081) | Прокси через web → напрямую не читает конфиг | Получает MCP-инструменты через mcp-gateway |
| **admin-dashboard** (Go, :8085) | HTTP-прокси к data-service admin API | Только read/write через API — своей валидации нет |

**mcp-gateway не валидирует конфиг повторно** — он доверяет data-service.

---

### 4. Валидация

Валидация происходит **один раз** — при загрузке конфига в data-service.

Функция: [`helperium-go/config/validate.go`](../helperium-go/config/validate.go)

```go
func Validate(rawJSON []byte) error   // для admin API
func (cfg *Config) Validate() error   // для Load() — проверяет всё
```

Метод `cfg.Validate()` проверяет:

- **Required поля**: version, data_source.driver, data_source.dsn
- **Enum'ы**: driver (`sqlite`/`postgres`), op, method, field type, param type, relation kind, auth strategy
- **Cross-entity ссылки**:
  - endpoint.entity → существует в entities
  - endpoint.query_id → существует в custom_queries
  - stats.entity → существует в entities
  - mcp_tool.endpoint → существует в endpoints
- **Семантика**: GET-эндпоинты не могут иметь `op: custom_query` без query_id, find-эндпоинты без search_field и т.п.

---

### 5. Что генерируется автоматически

После `POST /admin/config/rewrite`:

| Секция | Авто | Описание |
|---|---|---|
| `entities[]` | ✅ | Все не-системные таблицы, PK, FK, колонки |
| `endpoints[]` | ✅ | `GET /{entity}/{id}` (get_by_id), `GET /{entity}` (find) |
| `mcp_tools[]` | ✅ | `get_{entity}`, `distinct_{entity}`, `count_{entity}` (op-based), `grep_{entity}`, `filter_{entity}`, `schema_{entity}` (strategy-based). `find`/`list` эндпоинты REST существуют, но `GenerateMCPTools()` их не включает |
| `stats.counters[]` | ✅ | Счётчики для `/stats` |
| `data_source.read_only` | ✅ | `true` — write по умолчанию выключен |

> **Source of truth:** [`data-service/internal/configgen/`](../data-service/internal/configgen/) — [`configgen.go`](../data-service/internal/configgen/configgen.go), [`mcp.go`](../data-service/internal/configgen/mcp.go).
> Полное описание тулов и стратегий: [AGENTS.md](../AGENTS.md) §2a MCP Архитектура.

### 6. Что нужно писать вручную

| Секция | Зачем |
|---|---|
| `custom_queries{}` | JOIN, агрегаты, отчёты — бизнес-логика |
| `endpoints[].method: POST/PUT/DELETE` | Write-операции (если `read_only: false`; configgen их не генерирует) |
| `endpoints[].description` | Уточнить описание для LLM |
| `auth{}` | Row-level isolation |
| `introspection{}` | `include_schemas`, `exclude_tables`, `skip_prefixes` |

### 7. Что НЕЛЬЗЯ редактировать вручную (⚠️)

**Не редактируй в конфиге руками то, что генерируется автоматикой.**

Если в entities/endpoints/MCP-тулах ошибка — значит баг в генераторе,
и его надо править в коде, а не патчить конфиг:

- [`data-service/internal/configgen/configgen.go`](../data-service/internal/configgen/configgen.go) — `Generate()`, `tableToEntity()`, `GenerateMCPTools()`
- [`data-service/internal/datasource/{sqlite,postgres}_adapter.go`](../data-service/internal/datasource/) — `Introspect()`
- [`helperium-go/config/types.go`](../helperium-go/config/types.go) — `Config.Validate()`

**Почему:** После следующего `POST /admin/config/rewrite` ручные правки будут
перезаписаны автоматикой. Если же rewrite не делать — баг останется навсегда.

**Пример:** Если таблица `order_items` не попала в entities — значит:
1. Или `shouldSkip()` её отфильтровал
2. Или `Introspect()` её не нашёл
3. Или у неё нет PK и все колонки nullable

Исправлять надо в `configgen.go` или адаптере, а не добавлять entity вручную.

---

### 8. ~~config.schema.json~~ (удалён)

Файл `specs/config.schema.json` **удалён из репозитория**. Валидация
переехала в Go-типы (`helperium-go/config/types.go`).

Был нужен рантайму data-service. Больше не требуется.
Человеко-читаемое описание формата — [`config.schema.md`](./config.schema.md).

---

## api.openapi.yaml / rag.openapi.yaml — OpenAPI контракты Python-сервисов

Оба спека **автоматически генерируются FastAPI** из Pydantic-моделей
и декораторов `@app.get/post`. Рабочий процесс:

```
FastAPI-код → app.openapi() → YAML spec → git commit
```

### Изменять spec вручную — НЕЛЬЗЯ

**Первичен код.** Spec — snapshot на момент последнего commit'а.
Добавил новый endpoint или поле в модели → прогнал тест → спека
обновилась сама:

```bash
uv run pytest api-service/src/api_service/tests/unit/test_openapi_api.py -v
uv run pytest rag/tests/unit/test_openapi_spec.py -v
```

Тест падает → обновляем spec:

```bash
# 1. Запустить сервис
# 2. Экспортировать схему
curl -s http://127.0.0.1:8081/openapi.json | yu -x . > specs/api.openapi.yaml
curl -s http://127.0.0.1:8082/openapi.json | yu -x . > specs/rag.openapi.yaml
```

> `yu` — конвертер JSON → YAML. Альтернатива: `python3 -c "import yaml,json; yaml.dump(json.load(sys.stdin), sys.stdout)"`.

---

## Data-service OpenAPI — runtime-генерация

В отличие от Python-сервисов, data-service НЕ хранит OpenAPI spec-файл.
Схема генерируется runtime через `helperium-go/openapigen/openapigen.go`
(общий пакет, переехал из `data-service/internal/openapigen` 2026-08-03)
на основе загруженного конфига. Живая спека:

```bash
curl http://data-service:8084/openapi.json
```

**Это нормально.** Конфиг data-service описывает сущности клиента —
хранить статический spec бессмысленно, он разный для каждого tenant'а.

---

## Генерация клиента (на любом языке)

```bash
openapi-generator generate -i specs/rag.openapi.yaml -g python -o /tmp/rag-client
openapi-generator generate -i specs/api.openapi.yaml -g typescript -o /tmp/api-client
```
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)
