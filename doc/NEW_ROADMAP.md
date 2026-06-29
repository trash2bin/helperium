# Roadmap: B2B SaaS платформа с автогенерацией API по базе клиента

> **Контекст.** Этот документ описывает **текущую фазу** проекта — смену
> продукта: из domain-specific (один вуз, одна схема БД) в
> **generic B2B SaaS**, где клиент подключает **свою** базу (на старте
> SQLite/PostgreSQL), а сервис автоматически строит API и MCP-инструменты
> для агента на основе **схемы этой базы**.
>
> Этапы 0–2.7 из старого ROADMAP считаются выполненными и **не
> пересматриваются** как требования. Этапы 3.0–3.4 выполнены; 3.5 — с
> документированным отклонением; 3.6–3.9 — это оставшаяся работа.
> В этой версии документа отражены **реальные архитектурные решения,
> принятые в процессе**, и причины отклонений от первоначального плана.

---

## 1. Цель продукта (без изменений)

**Generic B2B SaaS-платформа для AI-агента над произвольной БД клиента.**

Пользовательский сценарий (после реализации оставшихся фаз 3.6–3.9):

1. Клиент регистрируется в платформе.
2. В UI указывает DSN своей БД (или выбирает один из коннекторов:
   PostgreSQL, SQLite, ...).
3. Платформа **автоматически интроспектирует схему** (`information_schema`
   для PostgreSQL, `sqlite_master` + `PRAGMA table_info` для SQLite).
4. На основе интроспекции **генерируется API** (REST) и **MCP-инструменты**
   для агента — с разумными дефолтами (таблица → сущность, колонки →
   поля, FK → relations, snake_case → camelCase).
5. Клиент через UI корректирует конфиг: переименовывает поля, отключает
   таблицы, добавляет вычисляемые endpoint'ы (whitelist-операции).
6. Конфиг сохраняется и применяется без рестарта.
7. Агент клиента (тот же LiteLLM-цикл, что сегодня в
   `demo/api/agent/`) уже имеет минимальный набор tools для
   поиска/чтения данных **без знания домена**.

**Что это даёт бизнесу:** нулевые затраты на интеграцию для типовых
клиентов (подключил БД → получил рабочий агент за минуты), глубокая
кастомизация через конфиг для нетиповых.

---

## 2. Что остаётся инвариантом, а что пересмотрено

### Остаётся неизменным

- **I1.** Каждый этап оставляет проект в рабочем состоянии. После
  каждого этапа UI-чат и MCP-инструменты работают (возможно — на
  временных заглушках).
- **I2.** Пользовательские данные не теряются при перезапуске. Никаких
  разовых «удалите БД» без миграции.
- **I3.** Существующие публичные HTTP-контракты не ломаются без явного
  согласования. Если API меняется — версия в URL или backward-compat
  обёртка.
- **I4.** Архитектура остаётся набором независимых сервисов с
  HTTP-контрактом. Любой сервис можно переписать на другом языке без
  затрагивания соседей.
- **I5.** OpenAPI/Swagger на каждом long-running HTTP-сервисе.
- **I6.** Конфигурация — JSON, валидируется JSON Schema при загрузке
  и при reload.
- **I7.** SQL-запросы строятся **только** через подготовленные выражения
  (`?`/`$1` placeholder'ы), пользовательские значения никогда не
  конкатенируются в SQL. Whitelist операций: только `SELECT`, запрет
  `;`, обязательный `LIMIT` per-query.

### Пересмотрено (по сравнению с первой версией roadmap)

- **R1. ✅ ВЫПОЛНЕНО.** `data-service` стал generic CRUD/query-прокси над
  произвольной БД. Доменная семантика ушла из Go-кода в конфиг и
  интроспекцию. Все 7 Go-моделей и пакет `internal/repository/` удалены.
- **R2. ✅ ВЫПОЛНЕНО.** `mcp_server` (Python) удалён. MCP реализован на
  Go в `mcp-gateway/` — отдельным сервисом (решение «не объединять с
  data-service» подтверждено — см. §6).
- **R3. ⚠️ ВЫПОЛНЕНО С ОТКЛОНЕНИЕМ.** Внутренние доменные Pydantic-модели
  в `agent-tutor-sdk/contracts/` не превратились в **тонкие алиасы** над
  generic `Entity`, а были **полностью удалены**. Старые drift-тесты
  заменены новыми (`test_entity_model.py`). Причина: keeping алиасы
  создавало бы постоянный слой deprecated-кода без потребителей —
  решено пойти на breaking change ради чистоты API. См. §13.
- **R4. ❌ НЕ ВЫПОЛНЕНО.** `demo/web` всё ещё рендерит вкладки студентов,
  преподавателей, оценок через хардкод в `app.js` и `server.py`. Это
  основное содержимое фазы 3.6.
- **R5. ❌ НЕ ВЫПОЛНЕНО.** `rag/fixtures/cli_docgen.py` и связанные
  утилиты (`agent-seedgen`, `agent-rag-docgen`) остаются доменными —
  генерируют русскоязычных студентов и лекции по жёстко прописанным
  дисциплинам. Будет исправлено в фазе 3.8.

### Что НЕ делаем в этой фазе

- Не строим UI-конфигуратор как полноценный продукт (фаза 3.9).
- Не подключаем ORM (`sqlx`, `gorm`, `sqlc`). Используем `database/sql`
  с явными prepared statements.
- Не уходим в k8s/Istio/multi-region.
- Не делаем полноценную авторизацию пользователей на этом этапе —
  достаточно tenant-isolation через конфиг (`X-Tenant-ID`) и
  admin-токена для reload.
- Не делаем миграции чужих БД. Клиентская БД — read-only с точки
  зрения платформы.

---

## 3. Целевая архитектура (vision) и текущее состояние

```
                            ┌──────────────────────┐
                            │  Web UI (config +    │  ← ⚠️ пока доменный (фаза 3.6)
                            │  generic tables)     │
                            └──────────┬───────────┘
                                       │
                                       ▼
        ┌────────────────────────────────────────────────────────┐
        │                  API (Python, LiteLLM)                 │ ✅
        │   • Агент: оркестратор, история, tool-вызовы           │
        │   • Подключается к MCP-gateway по HTTP                  │
        │   • Чат, бэклог, сессии                                │
        └────────────┬─────────────────────────────┬─────────────┘
                     │                             │
                     ▼                             ▼
        ┌────────────────────────┐    ┌──────────────────────────┐
        │   MCP-gateway (Go)     │    │   RAG (Python, FastAPI)  │ ✅
        │   ──────────────────   │    │   • generic RAG-клиент   │
        │   • HTTP-MCP сервер    │    │   • Без изменений        │
        │     (mark3labs/mcp-go  │    │     в архитектуре        │
        │      v0.8.3)           │    │                          │
        │   • Tools генерируются │    │                          │
        │     из /mcp/manifest   │    │                          │
        └────────────┬───────────┘    └──────────────────────────┘
                     │ (HTTP, internal)
                     ▼
        ┌────────────────────────────────────────────────────────┐
        │           Data-service (Go, config-driven)             │ ✅
        │   ─────────────────────────────────────────────────    │
        │   • Config loader (JSON, envsubst, JSON Schema)        │
        │   • Driver registry: sqlite, postgres                  │
        │   • Introspector (information_schema / sqlite_master)  │
        │   • Query builder (только SELECT, prepared, LIMIT)     │
        │   • Endpoint builder (REST по конфигу)                 │
        │   • Admin API: /mcp/manifest, /admin/config/rewrite    │ ⚠️
        │   • OpenAPI generator (из runtime)                     │
        └────────────┬───────────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────────────────────────────────────┐
        │              Клиентская БД (read-only)                 │ ✅
        └────────────────────────────────────────────────────────┘
```

### Что достигнуто технически

| Слой | Состояние | Комментарий |
|---|---|---|
| `data-service` SQL | generic query builder ✅ | Без хардкода домена |
| `data-service` модели | generic `Entity` через конфиг ✅ | Все 7 Go-структур удалены |
| `data-service` endpoints | N URL, описанных в `config.endpoints[]` ✅ | chi-роутер собирается runtime |
| `data-service` schema | introspection чужой БД ✅ | DDL не наш |
| `data-service` адаптеры | sqlite + postgres ✅ | Equivalence-тесты проходят |
| Общий config-пакет | `agent-tutor-go/config/` через `go.work` ✅ | Переиспользуется в обоих Go-сервисах |
| MCP tools | Auto-gen из config + runtime `/mcp/manifest` ✅ | mark3labs/mcp-go v0.8.3 |
| SDK контракты | generic `Entity` + `api/models.py` ✅ | Старые `contracts/` удалены |
| **Web UI** | ❌ всё ещё доменный | `students`/`teachers`/`grades` в `app.js` |
| **Multi-tenant** | ❌ `row_filters: []` | Phase 3.7 |
| **Hot reload** | ❌ нет fsnotify | Phase 3.7 |
| **Generic fixtures** | ❌ seedgen/docgen доменные | Phase 3.8 |

---

## 4. Карта хардкода: итог

| Источник хардкода | Статус | Заметки |
|---|---|---|
| `data-service/internal/repository/` | ✅ удалён (фаза 3.3) | Заменён `runtime/` |
| `data-service/internal/models/` | ✅ удалён (фаза 3.3) | Generic через конфиг |
| `data-service/internal/handlers/` | ✅ удалён (фаза 3.3) | Заменён `runtime/handlers/` |
| `data-service/internal/db/schema.sql` | ✅ удалён (фаза 3.3) | DDL не наш |
| `data-service/internal/seedgen/` | ✅ изолирован | Остался как `cmd/seed-cli/` (dev-only) |
| `mcp_server/tools_via_http.py` + `server.py` | ✅ удалён | Переехал в `mcp-gateway/` (Go) |
| `agent-tutor-sdk/contracts/__init__.py` | ✅ удалён | Прямо, без алиасов (см. §13) |
| `agent-tutor-sdk/data_client.py` (16 методов) | ✅ удалены | Заменены на generic (см. фазу 3.5) |
| `demo/web/static/app.js` (доменные таблицы) | ❌ **остался** | Фаза 3.6 |
| `demo/web/server.py` (доменные роуты `/api/data/...`) | ❌ **остался** | Фаза 3.6 |
| `rag/fixtures/seedgen.py` (домен вуза) | ❌ **остался** | Фаза 3.8 |
| `rag/fixtures/document_generator.py` (домен вуза) | ❌ **остался** | Фаза 3.8 |
| `specs/data-service.openapi.yaml` | ✅ удалён | Генерируется runtime из конфига |
| `specs/schemas/*.schema.json` (7 файлов) | ✅ удалены | Заменены на `config.schema.json` + runtime OpenAPI |

---

## 5. Контракт конфигурации

Конфиг — JSON, валидируется JSON Schema (`specs/config.schema.json`).
Формат подробно описан в первой версии roadmap и **не менялся**.
Минимальный жизнеспособный конфиг содержит:

- `version`, `data_source` (driver, dsn, pool_size, read_only)
- `introspection` (enabled, include_schemas, exclude_tables)
- `entities[]`, `endpoints[]`, `custom_queries`, `stats`, `mcp_tools[]`
- `auth` (strategy, tenant_header, row_filters) — **поле определено в
  схеме, но `row_filters[]` не применяется runtime — фаза 3.7**

**Ключевые инварианты контракта** (без изменений):

- `entities[].fields[].name` — публичные имена, `column` — внутренние.
- Все user-input параметры endpoint'ов — через `params[]` и placeholder'ы.
- `custom_queries` — только SELECT, не более одного statement, `max_rows`.
- `entities[].relations[]` описывает FK, но **не** генерирует JOIN — для
  JOIN'ов используется `custom_queries`.

Реальный пример развёрнутого конфига для university-БД — `specs/config.example.json`
(с включённым `mcp_tools[]` и всеми 11 endpoint'ами, повторяющими
поведение демо).

---

## 6. Почему MCP переехал на Go (и почему остался отдельным сервисом)

### Что сделано

- `mcp-gateway/` (Go), порт 8083, `mark3labs/mcp-go v0.8.3` (включён в
  `mcp-gateway/go.mod`).
- Реализован через Go-SDK `mark3labs/mcp-go`, без своего MCP-протокола.

### Почему отдельным сервисом, а не в data-service

Первоначальный план («после стабилизации объединить в один бинарник,
если профилирование покажет выигрыш») **сохраняется**. Текущее
разделение обосновано:

- Разные жизненные циклы: mcp-gateway чаще перезапускается при
  изменении списка tools, data-service стабильнее.
- In-process vs HTTP — разница 0.1мс vs 1мс, в цикле агента
  с 5–10 tool-вызовами это ~5–10мс, **не критично**.
- Сохраняется I4 (независимость сервисов).

### Что неожиданно хорошо получилось: `/mcp/manifest`

План фазы 3.4 включал опциональный пункт о `GET /mcp/manifest` в
data-service, но в реализации это стало **единственным путём**:
mcp-gateway при старте дёргает `http://data-service:8084/mcp/manifest`
и регистрирует tools оттуда. Это устраняет необходимость парсить
`config.json` в двух местах и риск рассинхронизации.

Файл: `data-service/internal/runtime/handlers/mcp_manifest.go`:

```go
func MCPManifestHandler(cfg *config.Config) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        RespondJSON(w, http.StatusOK, map[string]any{
            "endpoints":      cfg.Endpoints,
            "entities":       cfg.Entities,
            "custom_queries": cfg.CustomQueries,
            "mcp_tools":      cfg.MCPTools,
        })
    }
}
```

---

## 7. Адаптеры под источники данных

Старт — SQLite и PostgreSQL. Реализовано оба (§3.1).

### Что должен уметь адаптер (реализовано)

1. **Introspector.** Метод `Introspect(ctx) (*Schema, error)`, generic
   описание: таблицы, колонки, FK, индексы.
2. **Driver.** `DB`-интерфейс (`QueryContext`, `ExecContext`, `PingContext`,
   `Close`) — `database/sql`-совместимый.
3. **Placeholder.** Адаптерный метод `TranslatePlaceholder(idx int) string`.
4. **Result type mapping.** Нормализация колонок в generic типы.
5. **Limiter / OFFSET.** В SQLite/PostgreSQL унифицировано.

**Контракт адаптера** — `data-service/internal/datasource/adapter.go`.
Postgres — `pgx/v5 stdlib` (решение ADR по Q1 — `pgx/v5`, отвергаем
`lib/pq` в maintenance).

### Дальнейшие адаптеры (roadmap, не сейчас)

| Адаптер | Способ интроспекции | Способ чтения |
|---|---|---|
| MySQL | `information_schema` | прямой SQL |
| MSSQL | `INFORMATION_SCHEMA`, sys.tables | прямой SQL |
| Airtable | Airtable Metadata API | REST API (не SQL) |
| Notion DB | Notion API | REST API (не SQL) |
| Bitrix24 | REST API | REST API (не SQL) |
| amoCRM | REST API | REST API (не SQL) |

Для CRM-адаптеров понадобится более общий `Source`-интерфейс — **не в
этом roadmap**.

---

## 8. Фазы: выполненные и оставшиеся

> Формат: каждая фаза указана с **текущим статусом** и **отклонениями
> от первоначального плана** (если есть).

### Фаза 3.0 — Контракты и валидация ✅ ВЫПОЛНЕНО

**Коммит:** `4c7878a feat(data-service): phase 3.0 — config schema + datasource adapter interface`

**Что сделано:**
- `specs/config.schema.json` — JSON Schema для конфига.
- `specs/config.example.json` — рабочий пример для university-БД.
- `data-service/internal/datasource/adapter.go` — интерфейс `Adapter`.

**Отклонения:** нет.

---

### Фаза 3.1 — Postgres driver + introspector ✅ ВЫПОЛНЕНО

**Коммиты:**
- `e07b94f` Postgres driver (`pgx/v5 stdlib`) + тесты
- `a6d3f85` SqliteAdapter + introspector + тесты
- `701bf1c` PostgresAdapter + introspector (`information_schema` + `pg_catalog`) + тесты
- `6b7482f feat(data-service): phase 3.1 — remove domain hardcode + cross-driver equivalence tests`

**Что сделано:**
- Оба адаптера возвращают **одинаковый** `Schema` для эквивалентных
  таблиц — `TestEquivalence_CrossDriver` проходит на реальном Postgres.
- `d195473` registry + connector dispatch + разрыв циклической
  зависимости.

**Отклонения:** `lib/pq` отвергнут, выбран `pgx/v5` (ADR по Q1).

---

### Фаза 3.2 — Config loader + Query/Endpoint builder ✅ ВЫПОЛНЕНО С ОТКЛОНЕНИЕМ

**Коммиты:**
- `97d9e4b feat(data-service): phase 3.2.a — config loader with envsubst + JSON Schema validation`
- `1cb7d45 feat(data-service): phase 3.2.b — query builder + entity resolver + response mapper`
- `2d2e68b feat(data-service): phase 3.2.c + 3.2.d — endpoint builder, handlers, e2e equivalence tests`

**Что сделано:**
- `runtime/query_builder.go`, `entity_resolver.go`, `response_mapper.go`,
  `converter.go`, `handlers/{get_by_id, find, list, custom_query, health,
  stats}.go`, `endpoint_builder.go`.
- При `RUNTIME_MODE=config` data-service обслуживает те же 11 endpoint'ов.

**Отклонение №1 (архитектурное):**
Конфиг-типы вынесены в **отдельный Go-модуль `agent-tutor-go/config/`**
(`go 1.24.0`, `use (./agent-tutor-go ./data-service ./mcp-gateway)`),
а не жили в `data-service/internal/config/`. Причина: и `data-service`,
и `mcp-gateway` используют один и тот же набор типов конфига
(`Config`, `Endpoint`, `Entity`, `CustomQuery`, `MCPTool`). Без
разделения это было бы дублирование или `internal/` пакет,
экспортируемый между сервисами через хак.

**Отклонение №2 (упрощение):**
`RUNTIME_MODE` feature-flag **не понадобился** — старый путь удалён
вместе с `repository/`/`handlers/`/`models/` сразу в фазе 3.3, без
переходного периода. Тестовая нагрузка на два параллельных пути
не возникла, потому что:
- Существующих продакшен-потребителей у `repository/`/`handlers/` уже
  не было (data-service — новый сервис, введённый в этом же roadmap'е);
- Equivalence-тесты между старым и новым путями написаны как
  `TestEquivalence_CrossDriver` — **между двумя СУБД**, что оказалось
  полезнее, чем тесты между старым и новым Go-кодом.

---

### Фаза 3.3 — Удаление domain-specific кода ✅ ВЫПОЛНЕНО

**Что сделано:**
- `data-service/internal/repository/` — удалён.
- `data-service/internal/handlers/` — удалён, заменён на
  `runtime/handlers/`.
- `data-service/internal/models/` — удалён (7 структур).
- `data-service/internal/db/schema.sql` — удалён.
- E2E-тесты переписаны на config-driven.
- `cmd/seed-cli/` вынесен как dev-only утилита
  (раньше — `internal/seedgen/`, доступный из `cmd/server`).
- `cmd/baseline-openapi/` и `cmd/schema-gen/` удалены — генерируется
  на лету из runtime.

---

### Фаза 3.4 — MCP на Go + generic tool registry ✅ ВЫПОЛНЕНО С ОТКЛОНЕНИЕМ

**Коммиты:**
- `932288d feat(data-service+mcp-gateway): Phase 3.2-3.4 — config-driven, generic MCP-manifest`
- `b47efdf fix(mcp-gateway): connect RAG tools (search_documents, list_documents, get_rag_context)`
- `051ee54 / f29e0ff fix(mcp-client): correct close of MCP session on cross-task shutdown`

**Что сделано:**
- `mcp-gateway/` (Go), `:8083`, `mark3labs/mcp-go v0.8.3`.
- 8 university-инструментов сгенерированы из `cfg.mcp_tools[]` +
  3 RAG-инструмента через `mcp-gateway/internal/ragclient/client.go`
  (post-fix `b47efdf`).
- `data-service/internal/runtime/handlers/mcp_manifest.go` —
  `GET /mcp/manifest` как **единственный source of truth** для
  mcp-gateway.
- `mcp_server/` (Python) удалён вместе со всеми 4 файлами тестов.
- `demo/api/agent/mcp_client.py` переписан на долгоживущую
  streamable_http-сессию.

**Отклонение №3 (усиление):**
`/mcp/manifest` в `data-service` стал обязательным runtime-эндпоинтом
(а не «опциональным дополнением», как было в первой версии roadmap).
mcp-gateway **не парсит config.json** — он стучится к data-service
за манифестом. Это устраняет дублирование и единый источник правды:
правда о доступных tools живёт в data-service, а mcp-gateway — лишь
их презентация по протоколу MCP.

---

### Фаза 3.5 — Generic SDK контракты ✅ ВЫПОЛНЕНО С ОТКЛОНЕНИЕМ

**Коммит:** `f5964c3 refactor(sdk): move HTTP DTO to agent_tutor_sdk.api, generic Entity as canonical`

**Что сделано:**
- `agent-tutor-sdk/src/agent_tutor_sdk/models.py` — generic `Entity`
  (`model_config = ConfigDict(extra="allow")`).
- `agent-tutor-sdk/src/agent_tutor_sdk/api/models.py` — канонические
  HTTP DTO для `demo/api`.
- Старый `test_contracts_drift.py` (135 строк) удалён, заменён на
  `test_entity_model.py` (75 строк) + `test_seedgen_validation.py`
  обновлён.
- `data_client.py`: 16 доменных методов заменены на generic
  (см. ADR Q5 — generic Entity живёт в SDK, а не в общем `pkg/`,
  потому что SDK и есть общий кросс-сервисный слой для Python).
- `agent-tutor-sdk/tests/unit/test_contracts_drift.py` — **удалён**.

**Отклонение №4 (breaking change):**
Старые модели в `agent_tutor_sdk/contracts/` (`Student`, `Teacher`,
`Discipline`, ...) **полностью удалены**, без `class Student(Entity)`
deprecated-обёрток. Решение:
- Потребители старых моделей — это `demo/api` и `seedgen`.
- `demo/api` мигрировал на `api/models.py` (единый DTO).
- `seedgen` валидируется через `StorageSeed` (другая модель, не из
  contracts) + `entity_model.py`.
- Keeping алиасы создало бы постоянный deprecated-слой, который
  никто, кроме `seedgen`, не использует.

Цена: это **breaking change** SDK. Версия в `pyproject.toml` должна
быть поднята отдельно (см. открытые вопросы Q8 ниже).

---

### Фаза 3.6 — Generic web UI ❌ НЕ НАЧАТО

**Что осталось сделать:**

1. `demo/web/static/app.js` — заменить хардкод-таблицы
   (`students`, `teachers`, `grades`, `disciplines`, `schedule`) на
   рендер по схеме endpoint'а. Один компонент `EntityTable`
   принимает метаданные колонок из первого ответа (или из
   `GET /entities/{name}/schema`, который ещё предстоит добавить в
   data-service).
2. `demo/web/server.py` — заменить захардкоженные маршруты
   `/api/data/students`, `/api/data/teachers`, ... на generic
   `/api/data/{entity}` с whitelist по конфигу (что разрешено
   отдавать в UI).
3. `demo/web/static/index.html` — вкладки генерируются по списку
   entities из `/mcp/manifest` (или нового `/entities`).
4. Чат с агентом остаётся как есть.

**Зависимости:** данные уже доступны через `/mcp/manifest` —
нужно решить, отдавать ли их напрямую web'у или ввести новый
`GET /entities` в data-service. См. открытый вопрос Q9.

**Оценка трудозатрат:** 2–3 недели.

**Критерий готовности:**
- UI работает с **любым** конфигом data-service (подмена
  `config.example.json` на другой → UI перерисовывается).
- 18 тестов в `demo/web/tests/unit/test_proxy.py` переписаны на generic.

---

### Фаза 3.7 — Multi-tenancy, admin API, hot reload ❌ НЕ НАЧАТО

**Что осталось сделать:**

1. `cfg.auth.row_filters[]` — `WHERE tenant_id = :tenant_id`
   применяется на уровне query builder. Сейчас
   `specs/config.example.json` содержит `"row_filters": []`,
   и в `query_builder.go` нет кода, который бы применял фильтры.
2. `POST /admin/config` (защищён admin-токеном) — PUT новый
   конфиг, валидация по schema, hot reload без рестарта.
3. `GET /admin/config` — текущий конфиг (без секретов).
4. `GET /admin/config/versions` — история конфигов.
5. Watcher (`fsnotify`) на config-файл в dev-режиме.
6. Конфиг-стор: переключение с файла на platform-БД (отдельная
   БД платформы, не клиентская) с миграцией.

**Сейчас в коде data-service** есть только `POST /admin/config/rewrite`
(в `endpoint_builder.go`) — это re-generate конфига из БД после
`--discover`, не external hot reload. Эту точку следует сохранить
как dev-only и **не** путать с production admin API.

**Блокеры перед стартом (ADR):**
- **Q3** Версионирование конфигов: в platform-БД или в git?
- **Q4** Multi-tenancy через `X-Tenant-ID` или отдельные инстансы
  data-service?

**Оценка трудозатрат:** 2 недели.

**Критерий готовности:**
- Два тенанта с разными конфигами работают на одном data-service.
- Изменение конфига через admin API применяется без рестарта.
- Существующие тесты + новые multi-tenant тесты — зелёные.

---

### Фаза 3.8 — Generic dev-инструментарий ❌ НЕ НАЧАТО (опционально)

**Что осталось сделать:**

1. `agent-seedgen` принимает на вход конфиг (или endpoint `/entities`)
   и генерирует seed для **любого** конфига, а не для university-БД.
   Сейчас `rag/fixtures/seedgen.py` использует хардкод
   `CURRICULUM`, `DISCIPLINE_NAMES`, русские ФИО из `catalog.py`.
2. `agent-rag-docgen` итерирует по `entities` из конфига, генерирует
   тематические документы. Сейчас
   `rag/fixtures/document_generator.py` жёстко работает с дисциплинами.
3. `cli_docgen.py` сейчас итерирует по фиктивным дисциплинам —
   переделать на entities из конфига.

**Критерий готовности:**
- `agent-seedgen --config <other>.json` создаёт seed для произвольного
  конфига.
- Не блокирует выход на прод.

**Важно:** это фоновая фаза — может быть отложена без влияния на
основной продукт.

---

### Фаза 3.9 — UI-конфигуратор ❌ ОТДЕЛЬНЫЙ ROADMAP

За пределами core-платформы. Требует отдельного дизайна (визард,
drag-and-drop схема, live-превью, версионирование). Архитектурные
заделы для неё есть в фазе 3.7 (admin API).

---

## 9. Технологические развилки (зафиксировано)

### Postgres driver в Go: ✅ `jackc/pgx/v5`
Выбран и реализован в фазе 3.1. `lib/pq` отвергнут (maintenance).

### MCP-библиотека для Go: ✅ `mark3labs/mcp-go v0.8.3`
Используется в `mcp-gateway` (см. `mcp-gateway/go.mod`).

### JSON Schema validator в Go
В data-service используется `xeipuuv/gojsonschema` (стабильный,
аккуратный). См. `data-service/go.mod`.

### Платформа для admin-БД
SQLite по умолчанию (как сейчас), опционально PostgreSQL через
`DATABASE_URL`. Это будет нужно в фазе 3.7 для хранения
версий конфигов.

---

## 10. Метрики успеха фазы 3

После завершения фаз 3.0–3.7 платформа должна удовлетворять:

1. **Time-to-first-query.** Подключение новой БД клиента (с известной
   схемой) до первого работающего `/students`-подобного endpoint'а —
   ≤ 30 минут ручной работы (без UI). **Сейчас достижимо для
   non-web шага** (data-service + mcp-gateway уже generic).
2. **Zero-code новый домен.** Для клиента с PostgreSQL-БД, где
   таблицы называются по-человечески, платформа выдаёт работающий
   API **без единой строки кода**. **Сейчас достижимо.**
3. **Backward-compat.** Существующий demo-сценарий (university-БД с
   агентом, чатом, RAG, web-UI) работает без изменений в
   пользовательском опыте. **Частично достигнуто** — web-UI
   остаётся доменным.
4. **Тесты.** Все ранее зелёные тесты остаются зелёными. Покрытие
   generic-слоя ≥ 70%. **Достигнуто.**
5. **Документация.** NEW_ROADMAP.md (этот файл), ADR по ключевым
   решениям, README.md отражают новое устройство.

---

## 11. Что **точно не делаем** в этой фазе (без изменений)

- Не переписываем `rag/` на Go.
- Не строим полноценный UI-конфигуратор (фаза 3.9).
- Не подключаем CRM-адаптеры (следующая дорожная карта после 3.9).
- Не делаем авторизацию пользователей с логином/паролем.
- Не уходим в k8s/Helm/Istio.
- Не пишем свой MCP-протокол — уже используем `mark3labs/mcp-go`.

---

## 12. Сводка по этапам (timeline)

| Фаза | Содержание | Статус | Отклонения | Длительность |
|---|---|---|---|---|
| 3.0 | Контракты конфига | ✅ | — | 1–2 нед |
| 3.1 | Postgres + introspect | ✅ | `pgx/v5` вместо `lib/pq` | 2–3 нед |
| 3.2 | Config + Query/Endpoint builder | ✅ | `agent-tutor-go/`; нет `RUNTIME_MODE` | 3–4 нед |
| 3.3 | Удаление domain-кода | ✅ | — | 1–2 нед |
| 3.4 | MCP на Go + manifest | ✅ | `/mcp/manifest` как обязательный runtime-источник | 3–4 нед |
| 3.5 | Generic SDK | ✅ | Полное удаление contracts без алиасов (breaking) | 2–3 нед |
| **3.6** | **Generic web UI** | ❌ | — | **2–3 нед** |
| **3.7** | **Multi-tenant + admin + reload** | ❌ | — | **2 нед** |
| 3.8 | Generic fixtures | ❌ (опц.) | — | 1–2 нед |
| 3.9 | UI-конфигуратор | ❌ | — | отдельный roadmap |

**Параллельность:**
- 3.5 и 3.6 могут идти параллельно с 3.4 (после 3.2 — конфиг
  стабилен). В нашем случае 3.4 и 3.5 уже сделаны.
- 3.7 ждёт 3.6.
- 3.8 — фоновая, не блокирует.

**Общая оценка оставшейся работы:**
~6–8 недель full-time (1 разработчик) на фазы 3.6 и 3.7.
Фазы 3.8 и 3.9 не блокируют.

---

## 13. Документированные отклонения от первой версии roadmap

Этот раздел — **reason log** решений, которые отличаются от
первоначального плана. Все они осознанные, с обоснованием.

### O1: `RUNTIME_MODE` feature-flag не понадобился
**Было:** два параллельных runtime-пути (старый hardcoded и новый
config-driven), переключение через переменную.
**Стало:** сразу удалён старый путь в фазе 3.3.
**Почему:** data-service — новый сервис, введённый в этом roadmap'е,
прод-потребителей у `repository/` не было. Equivalence-тесты
оказались полезнее между двумя СУБД, чем между старым и новым
Go-кодом.
**Цена:** нулевая страховка отката.

### O2: Конфиг в `agent-tutor-go/`, а не в `data-service/internal/config/`
**Было:** конфиг-типы внутри `data-service`.
**Стало:** отдельный Go-модуль, подключённый через `go.work`.
**Почему:** `data-service` и `mcp-gateway` используют одни и те же
типы (`Config`, `Endpoint`, `Entity`, `CustomQuery`, `MCPTool`).
Без разделения — дублирование. `internal/` пакет между сервисами —
хак.
**Цена:** дополнительный workspace member в `go.work`.

### O3: `/mcp/manifest` как обязательный runtime endpoint
**Было:** опциональное дополнение в фазе 3.4 (mcp-gateway может
парсить config.json сам).
**Стало:** единственный source of truth.
**Почему:** устраняет риск рассинхронизации между двумя парсерами
конфига. runtime-эндпоинт — это API, контракт, валидируется.
**Цена:** mcp-gateway не стартует без запущенного data-service.
Смягчение: добавить локальный fallback на config.json для offline dev.

### O4: Полное удаление SDK contracts без deprecation-обёрток
**Было:** `class Student(Entity): deprecated`-обёртки сохраняются.
**Стало:** `agent_tutor_sdk/contracts/` удалён.
**Почему:** потребители — только `demo/api` и `seedgen`. Оба уже
мигрированы (`demo/api` → `api/models.py`, `seedgen` →
`StorageSeed`). Держать deprecated-слой ради двух уже
мигрированных потребителей — шум.
**Цена:** breaking change SDK, требует bump версии (открытый
вопрос Q8).

### O5: MCP на Go через `mark3labs/mcp-go v0.8.3`, а не свой протокол
**Было:** «Не пишем свой MCP-протокол» (в §11).
**Стало:** ✅ ровно так и сделано — `mark3labs/mcp-go v0.8.3`
используется.
**Почему:** не отклонение, а подтверждение принципа.

### O6: HTTP-контракт OpenAPI генерируется runtime, а не коммитится в `specs/`
**Было:** «эталонная версия в репозитории, drift-тест проверяет».
**Стало:** `specs/data-service.openapi.yaml` **удалён**.
**Почему:** runtime-генерация через `data-service/internal/openapigen/`
всегда актуальна. Drift-тест не нужен — контракт существует в одном
месте. Клиенты (web, mcp-gateway) могут сходить на
`GET /openapi.json` напрямую.
**Цена:** нельзя ссылаться на yaml в README без предварительного
старта сервиса. Но эта проблема решается ссылкой на
Swagger UI (`:8084/docs`).

---

## 14. Открытые вопросы (обновлено)

| # | Вопрос | Статус | Где решается |
|---|---|---|---|
| Q1 | Postgres driver | ✅ `pgx/v5` | ADR перед фазой 3.1 — сделано |
| Q2 | Имя нового сервиса | ✅ `mcp-gateway` | Перед фазой 3.4 — сделано |
| Q3 | Версионирование конфигов: platform-БД или git | ❓ | Перед фазой 3.7 |
| Q4 | Multi-tenancy: `X-Tenant-ID` или multi-instance | ❓ | Перед фазой 3.7 |
| Q5 | Где живёт generic `Entity` | ✅ SDK | Перед фазой 3.5 — сделано |
| Q6 | Куда делись `mcp_server/tests/unit/test_*_tools.py` | ✅ удалены | Фаза 3.4 — сделано |
| Q7 | Объединять data-service + mcp-gateway | ❓ | После 3.7 по профилированию |
| **Q8** | **Bump версии SDK из-за O4 breaking change** | **❓ открыто** | **До публичного релиза** |
| **Q9** | **Web получает метаданные через `/mcp/manifest` или новый `/entities`?** | **❓** | **Перед фазой 3.6** |
| **Q10** | **Local fallback `config.json` в mcp-gateway (см. O3)?** | **❓** | **Перед стабилизацией** |

---

## 15. Контрольные точки (checkpoints)

| Чекпоинт | Что должно работать | Статус |
|---|---|---|
| После 3.0 | `specs/config.schema.json` валиден. Существующая система не сломана. | ✅ |
| После 3.1 | `cmd/discover` читает Postgres и SQLite, выдаёт generic Schema. | ✅ |
| После 3.2 | data-service отвечает на 11 endpoint'ов через конфиг (без `RUNTIME_MODE` — см. O1). | ✅ |
| После 3.3 | data-service собирается без `repository`/`handlers`/`models`/`schema.sql`. | ✅ |
| После 3.4 | 8 MCP-инструментов работают через Go-сервис. `mcp_server/` удалён. `/mcp/manifest` — runtime source of truth (см. O3). | ✅ |
| После 3.5 | SDK имеет generic `Entity` + `api/models.py` как канонический HTTP DTO. Старые `contracts/` удалены без backward-compat (см. O4). | ✅ |
| **После 3.6** | **Web UI работает с любым конфигом data-service.** | ❌ |
| **После 3.7** | **Два тенанта с разными конфигами сосуществуют, hot reload работает.** | ❌ |
| После 3.8 | `agent-seedgen --config <other>.json` создаёт seed для произвольного конфига. | ❌ (опц.) |
| После 3.9 | Клиент правит конфиг через Web UI без знания Go/Python. | ❌ |

---

## 16. Что реально осталось (action items)

В порядке приоритета для следующих коммитов:

1. **Ответить на Q8** — bump версии `agent-tutor-sdk` (major bump
   из-за O4). Это блокирует любой внешний consumer.
2. **Фаза 3.6 (Generic web UI)** — основной пользовательский эффект
   generic-платформы. 2–3 недели.
3. **Ответить на Q3/Q4 (ADR)** перед фазой 3.7.
4. **Фаза 3.7 (Multi-tenancy + admin API + hot reload)** — превращает
   платформу в реальный SaaS. 2 недели.
5. **Фаза 3.8 (Generic fixtures)** — фоновая. 1–2 недели.
6. **Q10 (local fallback в mcp-gateway)** — повышает DX, можно в любой
   момент.
7. **Фаза 3.9 (UI-конфигуратор)** — отдельный roadmap, не блокирует.

Оценка: **6–8 недель** full-time до момента, когда платформа сможет
принять первого реального клиента (без 3.9).
