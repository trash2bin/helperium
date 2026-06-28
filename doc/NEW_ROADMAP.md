# Roadmap: B2B SaaS платформа с автогенерацией API по базе клиента

> **Контекст.** Существующий `doc/ROADMAP.md` фиксирует путь к pre-prod уровню
> «университетского агента». Этот документ описывает **новую фазу** — смену продукта:
> из domain-specific (один вуз, одна схема БД) в **generic B2B SaaS**, где клиент
> подключает **свою** базу (на старте SQLite/PostgreSQL, в перспективе — адаптеры
> под другие СУБД и CRM-системы), а сервис автоматически строит API и MCP-инструменты
> для агента на основе **схемы этой базы**.
>
> Этапы 0–2.7 из старого ROADMAP считаются выполненными и **не пересматриваются**
> как требования — но часть артефактов (например, доменные модели в
> `data-service/internal/models/`) будет **вытеснена** generic-логикой.

---

## 1. Новая цель продукта

**Generic B2B SaaS-платформа для AI-агента над произвольной БД клиента.**

Пользовательский сценарий (после реализации всех фаз):

1. Клиент регистрируется в платформе.
2. В UI указывает DSN своей БД (или выбирает один из коннекторов: PostgreSQL, SQLite, MySQL, ...).
3. Платформа **автоматически интроспектирует схему** (`information_schema` для PostgreSQL,
   `sqlite_master` + `PRAGMA table_info` для SQLite и т.д. по адаптерам).
4. На основе интроспекции **генерируется API** (REST) и **MCP-инструменты** для агента —
   с разумными дефолтами (таблица → сущность, колонки → поля, FK → relations,
   snake_case → camelCase).
5. Клиент через UI корректирует конфиг: переименовывает поля, отключает таблицы,
   добавляет вычисляемые endpoint'ы (whitelist-операции).
6. Конфиг сохраняется и применяется без рестарта.
7. Агент клиента (тот же LiteLLM-цикл, что сегодня в `demo/api/agent/`) уже имеет
   минимальный набор tools для поиска/чтения данных **без знания домена**.

**Что это даёт бизнесу:** нулевые затраты на интеграцию для типовых клиентов
(подключил БД → получил рабочий агент за минуты), глубокая кастомизация через
конфиг для нетиповых. Путь к агентным CRM/ERP-ассистентам.

---

## 2. Что остаётся инвариантом, а что пересматривается

### Остаётся неизменным

- **I1.** Каждый этап оставляет проект в рабочем состоянии. После каждого этапа
  UI-чат и MCP-инструменты работают (возможно — на временных заглушках).
- **I2.** Пользовательские данные не теряются при перезапуске. Никаких разовых
  «удалите БД» без миграции.
- **I3.** Существующие публичные HTTP-контракты не ломаются без явного
  согласования. Если API меняется — версия в URL или backward-compat обёртка.
- **I4.** Архитектура остаётся набором независимых сервисов с HTTP-контрактом.
  Любой сервис можно переписать на другом языке без затрагивания соседей.
- **I5.** OpenAPI/Swagger на каждом long-running HTTP-сервисе.
- **I6.** Конфигурация — JSON (выбран пользователем), валидируется JSON Schema
  при загрузке и при reload.
- **I7.** SQL-запросы строятся **только** через подготовленные выражения
  (`?`/`$1` placeholder'ы), пользовательские значения никогда не
  конкатенируются в SQL. Whitelist операций: только `SELECT`, запрет `;`,
  обязательный `LIMIT` per-query.

### Пересматривается

- **R1.** `data-service` перестаёт быть «университетским». Это generic
  CRUD/query-прокси над произвольной БД. Доменная семантика уходит из Go-кода
  в **конфиг** и **интроспекцию**.
- **R2.** `mcp_server` переезжает с Python на **Go** и физически
  сближается с `data-service`. Обоснование ниже в §6.
- **R3.** Внутренние доменные Pydantic-модели в `agent-tutor-sdk/contracts/`
  уступают место **generic** `Entity` (мапа `field → value`). Старые модели
  живут как тонкие алиасы для существующих потребителей, помечены `deprecated`.
- **R4.** `demo/web` (фронтенд) перестаёт рендерить «вкладку Студенты /
  Преподаватели / Оценки». Рендеринг управляется **метаданными endpoint'ов**
  из конфига: одна универсальная таблица + форма детализации.
- **R5.** `rag/fixtures/cli_docgen.py` и связанные CLI утрачивают доменную
  логику вуза. Если клиенту нужны тестовые документы — генератор становится
  generic (по сущностям из конфига), но в фазах 3.x это **не приоритет**.

### Что НЕ делаем в этой фазе

- Не строим UI-конфигуратор как полноценный продукт — только **закладываем
  контракт** (`config.schema.json`) и читаемый человеком JSON. UI — отдельный
  этап, после core-платформы.
- Не подключаем ORM (`sqlx`, `gorm`, `sqlc`). Используем `database/sql`
  с явными prepared statements — это даёт полный контроль над запросами
  и совместимость с `information_schema`-интроспекцией.
- Не уходим в k8s/Istio/multi-region. Это уровень зрелости, к которому
  придём позже.
- Не делаем авторизацию пользователей на этом этапе — достаточно
  tenant-isolation через конфиг (`X-Tenant-ID`) и admin-токена для reload.
- Не делаем миграции чужих БД. Клиентская БД — read-only с точки зрения
  платформы (платформа не пишет в чужую БД, кроме как в свою admin-БД
  для конфигов и сессий).

---

## 3. Целевая архитектура (vision)

```
                            ┌──────────────────────┐
                            │  Web UI (config +    │
                            │  generic tables)     │
                            └──────────┬───────────┘
                                       │
                                       ▼
        ┌────────────────────────────────────────────────────────┐
        │                  API (Python, LiteLLM)                 │
        │   ─────────────────────────────────────────────────    │
        │   • Агент: оркестратор, история, tool-вызовы           │
        │   • Подключается к MCP-gateway по HTTP                  │
        │   • Чат, бэклог, сессии (как сейчас)                   │
        └────────────┬─────────────────────────────┬─────────────┘
                     │                             │
                     ▼                             ▼
        ┌────────────────────────┐    ┌──────────────────────────┐
        │   MCP-gateway (Go)     │    │   RAG (Python, FastAPI)  │
        │   ──────────────────   │    │   ───────────────────    │
        │   • HTTP-MCP сервер    │    │   • Без изменений в       │
        │   • Tools генерируются │    │     архитектуре           │
        │     из конфига data-    │    │   • RAG-клиент SDK        │
        │     service + интроспек│    │     остаётся generic       │
        │   • Внутри — прямые    │    │                            │
        │     вызовы к data-     │    └──────────────────────────┘
        │     service по HTTP    │
        └────────────┬───────────┘
                     │ (HTTP, internal)
                     ▼
        ┌────────────────────────────────────────────────────────┐
        │           Data-service (Go, config-driven)             │
        │   ─────────────────────────────────────────────────    │
        │   • Config loader (JSON, валидация JSON Schema)        │
        │   • Driver registry (sqlite, postgres, ...)            │
        │   • Introspector (читает information_schema /          │
        │     sqlite_master, нормализует в generic Table/Col)    │
        │   • Query builder (только SELECT, prepared, LIMIT)     │
        │   • Endpoint builder (REST endpoints из конфига)       │
        │   • OpenAPI generator (из runtime + конфига)           │
        │   • Admin API: GET/PUT /admin/config, /admin/reload    │
        └────────────┬───────────────────────────────────────────┘
                     │ (driver-specific)
                     ▼
        ┌────────────────────────────────────────────────────────┐
        │              Клиентская БД (read-only)                 │
        │   • SQLite | PostgreSQL | (MySQL, ...)                 │
        │   • Схема — реальная схема клиента, не наша            │
        │   • data-service НЕ модифицирует её DDL                │
        └────────────────────────────────────────────────────────┘

        Параллельно — admin-БД (своя):
        ┌────────────────────────────────────────────────────────┐
        │   Platform DB (SQLite по умолчанию, Postgres опц.)    │
        │   • Конфиги тенантов (версионированные)                 │
        │   • Кеш интроспекции (с TTL)                            │
        │   • Сессии чатов (как сейчас в demo_sessions.sqlite)   │
        └────────────────────────────────────────────────────────┘
```

### Что здесь принципиально нового по сравнению с текущим состоянием

| Слой | Сегодня | Целевое состояние |
|---|---|---|
| `data-service` SQL | хардкод SQL под вуз в 6 файлах `repository/` | `query builder` генерирует SELECT из конфига |
| `data-service` модели | 7 Go-структур с русскими `description` | 1 generic `Entity{Fields map[string]any}` |
| `data-service` endpoints | 11 хардкод URL в `server.go` | N URL, описанных в `config.endpoints[]` |
| `data-service` schema | `db/schema.sql` (наш DDL) | introspection чужой БД, DDL не наш |
| MCP tools | 8 функций с русскими описаниями | N tools, генерируемых из конфига |
| SDK контракты | 7 Pydantic-моделей в `contracts/` | 1 generic `Entity` + старые как алиасы |
| Web UI | вкладки students/teachers/grades | универсальная таблица по метаданным |

---

## 4. Карта хардкода: что и от чего избавляемся

Чтобы roadmap был предметным — фиксирую **где именно** сейчас вшита
доменная семантика. Понимание этого определяет объём переписывания
по фазам.

### 4.1. `data-service/internal/repository/` (Go)

**Что зашито:** имена таблиц (`students`, `teachers`, `disciplines`, `grades`,
`schedule`, `groups`), имена колонок (`name`, `lessons_json`, `disciplines_json`,
`speciality`, `course`), JOIN'ы между ними. ~56 SQL-запросов с захардкоженной
семантикой вуза. JSON-поля как `lessons_json` и `disciplines_json` — это
ad-hoc схема, специфичная для одного демо-проекта.

**Куда движемся:** удалить пакет. Заменить на пакет `runtime` с
`query_builder.go` + `entity_resolver.go`, которые принимают конфиг
и собирают запросы параметрически.

### 4.2. `data-service/internal/models/` (Go)

**Что зашито:** 7 структур (`Group`, `Student`, `Teacher`, `Discipline`,
`Grade`, `Lesson`, `ScheduleEntry`) с доменными полями. Это source of truth
для JSON Schema и Pydantic-моделей в SDK.

**Куда движемся:** оставить один тип `Entity` (`map[string]any` + `Meta`
с описаниями полей). Генерация JSON Schema — из runtime-конфига, а не из
Go-структур.

### 4.3. `data-service/internal/handlers/` (Go)

**Что зашито:** URL-маршруты (`/students/{id}`, `/teachers/{name}/schedule`,
`/students/{id}/grades`) — это семантика вуза.

**Куда движемся:** пакет `runtime/handlers/` с набором builtin-handler'ов
(`get_by_id`, `find_by_field`, `list`, `custom_query`, `health`, `stats`).
`endpoint_builder.go` собирает роутер chi по конфигу.

### 4.4. `data-service/internal/db/schema.sql`

**Что зашито:** DDL для university-схемы.

**Куда движемся:** удалить. data-service больше **не создаёт** таблицы в
клиентской БД. Если нужно протестировать на реальной БД — клиент
сам создаёт схему или использует демо-БД через отдельный seed-CLI
(фаза 3.2+).

### 4.5. `data-service/internal/seedgen/`

**Что зашито:** фикстуры в формате `seed.json` (Python-генератор → JSON →
Go-apply). Защита «от перезаписи prod-БД» через panic.

**Куда движемся:** оставить как dev-инструмент для локального
демо (фаза 3.2), но изолировать от прод-кода. Команда `--seed`
остаётся, но работает только если в конфиге явно указан флаг
`allow_seed: true` иначе — отказ.

### 4.6. `mcp_server/tools_via_http.py` + `server.py`

**Что зашито:** 8 функций-обёрток (`_find_student_by_name`, `_get_student`,
`_get_schedule`, ...) и 8 `@mcp.tool()` декораторов с русскими docstring'ами.
Тесты в `mcp_server/tests/unit/test_*_tools.py` ловят конкретные поля
(`full_name`, `course`, `discipline_id`).

**Куда движемся:** переписать на Go (§6). MCP-инструменты генерируются
из конфига data-service (одного источника правды). Существующие
8 тулов остаются как **явные алиасы** для backward-compat.

### 4.7. `agent-tutor-sdk/contracts/__init__.py`

**Что зашито:** 7 Pydantic-моделей с `extra="forbid"`, описания на русском,
точечные типы. Drift-тесты в `test_contracts_drift.py` жёстко сверяют
имена/описания/required-флаги с JSON Schema.

**Куда движемся:** ввести generic-модель `Entity`/`EntityField`. Старые
имена (`Student`, `Teacher`, ...) — алиасы, помеченные `deprecated`.
Drift-тесты переориентируются: проверяют, что JSON Schema из
`config.json` соответствует тому, что отдаёт data-service `/openapi.json`
в runtime. Старая проверка Go-моделей → JSON Schema → Pydantic
остаётся для тех моделей, что ещё не generic'ифицированы.

### 4.8. `agent-tutor-sdk/data_client.py`

**Что зашито:** 16 методов с конкретными URL (`/students/{id}`,
`/teachers/{name}/schedule`, `/disciplines`, ...). Каждый метод
принимает и возвращает конкретные Pydantic-модели.

**Куда движемся:** generic-метод `request(entity, op, params)` +
типизированные хелперы (`get(entity, id)`, `find(entity, field, value)`,
`list(entity, filters)`). Старые методы остаются как deprecated-обёртки.

### 4.9. `demo/web/static/app.js` + `index.html` + `server.py`

**Что зашито:** в `app.js` — таблицы `students`/`teachers`/`grades`/`schedule`
с захардкоженными колонками (`full_name`, `group`, `discipline_name`,
`grade`). В `index.html` — кнопки вкладок. В `server.py` — маршруты
`/api/data/students`, `/api/data/teachers`, ... (прокси к data-service).

**Куда движемся:** одна универсальная таблица. Метаданные колонок
приходят из data-service (`/entities/{name}/schema` или embedded в
каждый ответ). В `app.js` — рендер по схеме, без хардкода имён полей.
В `server.py` — generic-прокси по whitelist из конфига
(что разрешено отдавать в UI).

### 4.10. `rag/fixtures/` (dev-инструментарий)

**Что зашито:** `seedgen.py` генерирует фиктивных студентов/преподавателей
с русскими ФИО, `document_generator.py` пишет лекции по дисциплинам,
`cli_docgen.py` итерирует по `get_all_disciplines()`.

**Куда движемся:** это **dev-only инструменты**. Они не блокируют
переход к generic data-service — но сами тоже generic'ифицируются
по конфигу (берут список «сущностей» из data-service, а не из
захардкоженного списка дисциплин). Делается **после** core-платформы,
отдельной фазой.

### 4.11. `specs/data-service.openapi.yaml`

**Что зашито:** 11 операций с русскими `summary`/`description`.

**Куда движемся:** этот файл становится **генерируемым** из runtime-конфига
(через `cmd/openapi-gen` в data-service). В репозиторий коммитим
только эталонную версию, drift-тест проверяет её соответствие
runtime.

### 4.12. `specs/schemas/*.schema.json`

**Что зашито:** 7 файлов с доменными описаниями.

**Куда движемся:** в перспективе — генерируются на лету из конфига
data-service. На фазе 3.x — оставляем как есть, drift-тест продолжает
работать для них.

---

## 5. Контракт конфигурации (формальная спецификация)

Это сердце новой платформы. Конфиг — JSON, валидируется JSON Schema
(`specs/config.schema.json`). Минимальный жизнеспособный конфиг
содержит:

```jsonc
{
  "version": 1,
  "data_source": {
    "driver": "sqlite",                              // sqlite | postgres | ...
    "dsn": "${DB_DSN}",                              // шаблоны ${ENV} подставляются
    "pool_size": 10,
    "read_only": true                                // пока платформа только читает
  },
  "introspection": {
    "enabled": true,                                 // при первом старте — discovery
    "include_schemas": ["public"],
    "exclude_tables": ["^pg_", "^_"]
  },
  "entities": [                                      // описание домена клиента
    {
      "name": "student",                             // публичное имя (camelCase)
      "table": "students",                           // реальная таблица
      "id_column": "id",
      "fields": [
        { "name": "full_name", "column": "name", "type": "string", "nullable": false,
          "description": "Полное ФИО студента" },
        { "name": "course",   "column": "course", "type": "int", "nullable": true }
      ],
      "relations": [
        { "field": "group", "kind": "many_to_one",
          "table": "groups", "local_fk": "group_id" }
      ]
    }
  ],
  "endpoints": [
    { "method": "GET", "path": "/students/{id}",
      "entity": "student", "op": "get_by_id" },
    { "method": "GET", "path": "/students",
      "entity": "student", "op": "find",
      "search_field": "full_name", "query_param": "name" },
    { "method": "GET", "path": "/students/{id}/grades",
      "op": "custom_query",
      "query_id": "student_grades",
      "params": [{ "name": "id", "in": "path", "required": true }]
    },
    { "method": "GET", "path": "/health",
      "op": "builtin_health" }
  ],
  "custom_queries": {                                // whitelist для escape hatch
    "student_grades": {
      "sql": "SELECT g.id, g.grade, g.date FROM grades g WHERE g.student_id = ?",
      "params": ["id"],
      "result_mapping": {
        "id":    { "type": "string" },
        "grade": { "type": "string" },
        "date":  { "type": "string" }
      },
      "max_rows": 1000
    }
  },
  "stats": {
    "counters": [
      { "name": "students", "entity": "student" }
    ]
  },
  "mcp_tools": [                                     // генерация MCP-инструментов
    {
      "name": "find_student_by_name",
      "endpoint": "/students",
      "description": "Найти студента по полному ФИО",
      "params": [{ "name": "name", "type": "string", "required": true }]
    }
  ],
  "auth": {
    "strategy": "header",                            // header | none
    "tenant_header": "X-Tenant-ID",
    "row_filters": [                                 // multi-tenant isolation
      { "entity": "student",
        "where": "tenant_id = :tenant_id" }
    ]
  }
}
```

**Ключевые инварианты контракта:**

- Имена в `entities[].fields[].name` — публичные (API/JSON). Имена колонок
  (`column`) — внутренние. Маппинг обязателен, чтобы UI мог переименовать
  snake_case в camelCase без потери смысла.
- Все user-input параметры endpoint'ов — через `params[]` и
  placeholder'ы (`?`/`$1`), никогда не конкатенируются.
- `custom_queries` — только SELECT, не более одного statement, обязателен
  `max_rows`.
- `entities[].relations[]` описывает FK-структуру, но **не** генерирует
  JOIN автоматически (для JOIN'ов — `custom_queries`). Это даёт
  предсказуемость и контроль над N+1.

---

## 6. Почему MCP переезжает на Go (и переезжает ли)

### Аргументы за

- **Единая runtime-платформа.** data-service и mcp работают с одной
  конфигурацией. Если оба на Go — один процесс загружает конфиг,
  строит endpoints, тут же регистрирует MCP-tools. Никакого
  HTTP-тура между ними → минимум latency.
- **Типизация конфига.** Go-структуры с `encoding/json` дают строгую
  валидацию. Python (с pydantic) даёт почти то же, но держим
  зоопарк — дороже.
- **Единая кодовая база для OpenAPI и MCP-описаний.** Один и тот же
  endpoint-метаданные используются для chi-роутера и для MCP-tools.
- **Развёртывание.** Один бинарник на оба сервиса в одном контейнере.
  Меньше образов, меньше памяти.

### Аргументы против

- **mcp_server уже зрелый.** 4 тестовых файла, 8 инструментов,
  интеграция с LiteLLM (в `demo/api/`).
- **FastMCP (Python) даёт готовую экосистему** (stdio/HTTP-транспорт,
  аутентификация, логирование). На Go придётся писать свой MCP-сервер
  или использовать Go-SDK (`mcp-go` и аналоги).
- **Потеря скорости разработки.** Python быстрее на коротких итерациях.

### Решение

**Переезжаем на Go**, но **не в один контейнер с data-service**. Аргументация:

- Переписать на Go — да, это убирает зоопарк типов и даёт
  единый источник правды для endpoint'ов.
- Но оставить **отдельным сервисом** в compose (пока). Причины:
  - Разные жизненные циклы — mcp чаще перезапускается при
    изменении списка tools, data-service стабильнее.
  - Latency-разница: in-process call vs HTTP — это 0.1мс vs 1мс,
    в цикле агента с 5–10 tool-вызовами разница ~5–10мс, **не критично**.
  - Если объединить — теряем I4 (независимость сервисов).
- Когда/если объединим — **после** того, как платформа стабилизируется,
  и только если профилирование покажет реальный выигрыш.

**План:** на Go-SDK MCP (например, `mark3labs/mcp-go`). Поверх — generic
tool-registry, который при старте запрашивает у data-service список
endpoint'ов и регистрирует их как MCP-tools.

---

## 7. Адаптеры под другие источники данных

Старт — SQLite и PostgreSQL. Архитектура должна позволять добавление
новых адаптеров без переписывания ядра.

### Что должен уметь каждый адаптер

1. **Introspector.** Метод `Introspect(ctx) (*Schema, error)`, возвращающий
   generic-описание: список таблиц, колонок (с типами), FK, индексов.
2. **Driver.** Реализация `DB` интерфейса (`QueryContext`, `ExecContext`,
   `PingContext`, `Close`) — как сейчас, только теперь это часть
   пакета `data-source/<name>`.
3. **Placeholder.** Преобразование `?` в нативный placeholder СУБД
   (`?` для SQLite, `$1, $2, ...` для PostgreSQL, `?` для MySQL).
4. **Result type mapping.** Приведение типов колонок к generic-типам
   платформы (`string`, `int`, `float`, `bool`, `null`, `json`).
5. **Limiter / OFFSET.** Трансляция `LIMIT n` / `OFFSET m` в синтаксис СУБД
   (для совместимости — в SQLite/Postgres одинаково, но в MSSQL — `TOP`).

### Контракт адаптера

```go
package datasource

type Adapter interface {
    Driver() string                                          // "sqlite", "postgres"
    Connect(ctx context.Context, dsn string) (DB, error)
    Introspect(ctx context.Context, db DB) (*Schema, error)
    TranslatePlaceholder(idx int) string                      // ? → $1
    QuoteIdentifier(name string) string                      // "table" или `table`
}
```

### Дальнейшие адаптеры (roadmap, не сейчас)

| Адаптер | Способ интроспекции | Способ чтения |
|---|---|---|
| PostgreSQL | `information_schema` + `pg_catalog` | прямой SQL |
| MySQL | `information_schema` | прямой SQL |
| MSSQL | `INFORMATION_SCHEMA`, sys.tables | прямой SQL |
| Airtable | Airtable Metadata API | REST API (не SQL) |
| Notion DB | Notion API | REST API (не SQL) |
| Bitrix24 | REST API | REST API (не SQL) |
| amoCRM | REST API | REST API (не SQL) |

Для CRM-адаптеров `DB`-интерфейс не подходит — понадобится
более общий `Source` interface с методами `Query`, `Get`, `List`.
Это **не в этом roadmap**, но архитектурно оставляем задел:
адаптеры под БД и CRM реализуют один и тот же `Source`-уровень,
просто CRM — без SQL-строителя, с фиксированными запросами через API.

---

## 8. Фазы

Каждая фаза оставляет проект в рабочем состоянии (I1) и завершается
контрольной точкой.

### Фаза 3.0 — Контракты и валидация (1–2 недели)

**Цель:** зафиксировать новые контракты до того, как начнём переписывать код.

**Что делаем:**

1. Создаём `specs/config.schema.json` — JSON Schema для конфига data-service
   (§5). Это **первый** формальный артефакт новой фазы.
2. Создаём `specs/config.example.json` — рабочий пример для
   текущей university-БД (полная обратная совместимость).
3. Обновляем `doc/ROADMAP.md`: помечаем старый roadmap как «этапы 0–2.7
   выполнены», ссылаемся на `NEW_ROADMAP.md` для этапов 3+.
4. Фиксируем `data-service/internal/datasource/adapter.go` — interface Adapter.
5. Генерируем эталонную OpenAPI для текущего состояния — это baseline
   для drift-тестов в фазе 3.1.

**Что НЕ делаем:** не трогаем runtime, не переписываем репозитории.

**Критерии готовности:**

- `config.schema.json` проходит валидацию на `config.example.json`.
- Существующий data-service продолжает работать как раньше.
- Все 16 Go-тестов + 113 Python-тестов — зелёные.

---

### Фаза 3.1 — Postgres driver + introspector (2–3 недели)

**Цель:** закрыть технический долг по второй СУБД и научиться читать
чужие схемы.

**Что делаем:**

1. Реализуем `internal/db/postgres.go` — полноценный Postgres driver
   (через `pgx/v5` или `lib/pq`; выбор фиксируем ADR).
2. Реализуем `internal/introspect/postgres.go` — читает
   `information_schema.tables`, `information_schema.columns`,
   `information_schema.table_constraints`, `information_schema.key_column_usage`,
   `pg_catalog.pg_indexes`. Возвращает generic `Schema{ Tables, Columns, FKs }`.
3. Реализуем `internal/introspect/sqlite.go` — то же через `sqlite_master`
   + `PRAGMA table_info` + `PRAGMA foreign_key_list`.
4. Тесты на обе СУБД с реальными docker-compose контейнерами.
5. Контрактные тесты: адаптеры возвращают **одинаковый** `Schema` для
   эквивалентных схем (одинаковые таблицы/колонки в SQLite и Postgres
   дают один и тот же JSON).

**Что НЕ делаем:** не подключаем адаптеры к runtime data-service.
Не меняем контракты наружу.

**Критерии готовности:**

- `go test ./internal/db/...` и `go test ./internal/introspect/...` зелёные.
- `--driver postgres` работает в `cmd/discover` (или test-mode бинарнике).
- Существующие 16 Go-тестов не сломаны.

---

### Фаза 3.2 — Config loader + Query builder + Endpoint builder (3–4 недели)

**Цель:** data-service начинает работать по конфигу. **Это первый
checkpoint на пути к generic.** После этой фазы существующая схема вуза
работает через конфиг, а не через хардкод.

**Что делаем:**

1. `internal/config/loader.go` — загружает JSON, валидирует по
   `config.schema.json`, подставляет `${ENV}`, разрешает включения
   (`$include`).
2. `internal/config/store.go` — интерфейс `Store`. Две реализации:
   `FileStore` (читает с диска) и `DbStore` (читает из platform-БД).
   На фазе 3.2 — только `FileStore`.
3. `internal/runtime/query_builder.go` — собирает SELECT'ы из конфига.
   Параметрические placeholder'ы, whitelist операций, обязательный LIMIT.
4. `internal/runtime/entity_resolver.go` — маппит имя поля в конфиге
   → имя колонки, обрабатывает relations, склейка JOIN через
   `custom_queries`.
5. `internal/runtime/endpoint_builder.go` — собирает chi-роутер
   по `cfg.endpoints[]`.
6. `internal/runtime/handlers/` — builtin-handler'ы: `get_by_id`,
   `find_by_field`, `list`, `custom_query`, `health`, `stats`.
7. `cmd/server/main.go` — фича-флаг `RUNTIME_MODE=config`.
   По умолчанию — старый путь (хотя бы до тех пор, пока не
   переедем 100%). `RUNTIME_MODE=config` — новый путь.
8. `config.example.json` описывает текущую university-схему.

**Что НЕ делаем:** не удаляем старые `repository/`, `handlers/`, `models/`.
Не переписываем MCP. Не трогаем SDK.

**Критерии готовности (checkpoint):**

- При `RUNTIME_MODE=config` data-service обслуживает **те же 11 endpoint'ов**
  с **теми же ответами**, что и раньше.
- `config.example.json` валидируется по `config.schema.json`.
- 16 Go-тестов + 113 Python-тестов — зелёные (тесты не знают про
  фича-флаг, они просто идут через старый путь).
- Сравнительный тест: для каждого endpoint'а делаем запрос через
  старый и новый путь, проверяем что JSON-ответы совпадают
  (или diff в пределах whitelist'а — порядок полей, форматирование).

---

### Фаза 3.3 — Удаление domain-specific кода в data-service (1–2 недели)

**Цель:** data-service больше не знает про «университет».

**Что делаем:**

1. Удаляем `internal/repository/` целиком.
2. Удаляем `internal/handlers/` целиком (заменены на `runtime/handlers/`).
3. Удаляем `internal/models/` (заменены на generic `Entity`).
4. Удаляем `internal/db/schema.sql` (больше не наш DDL).
5. `cmd/schema-gen/` — теперь генерирует `config.schema.json` (если нужно)
   или удаляется (если используем готовый JSON Schema validator).
6. Reseed-логика: `seedgen/` → опциональный `cmd/seed-cli/` для dev/demo,
   не часть прод-кода.
7. `RUNTIME_MODE=config` становится режимом по умолчанию.
8. Удаляем старые Go-тесты, заменяем на конфиг-driven тесты
   (один `university_config.json` + один набор e2e-тестов).

**Что НЕ делаем:** MCP, SDK, web — не трогаем.

**Критерии готовности:**

- data-service собирается без `repository/`, `handlers/`, `models/`,
  `schema.sql`.
- Все e2e-тесты (бывшие 16 Go-тестов, переписанные на конфиг) — зелёные.
- `specs/config.example.json` — единственный источник правды о том,
  как настроить data-service для вуза.
- Существующие MCP-тесты (которые стучатся к data-service по HTTP) —
  продолжают работать без изменений.

---

### Фаза 3.4 — MCP на Go + generic tool registry (3–4 недели)

**Цель:** MCP-сервер переписан на Go, инструменты генерируются из
конфига data-service.

**Что делаем:**

1. Новый сервис `mcp-gateway/` (Go) на основе `mark3labs/mcp-go`
   или аналога. Порт 8083 (как сейчас).
2. При старте читает `config.json` (тот же, что у data-service) и
   регистрирует MCP-инструменты по `cfg.mcp_tools[]`.
3. Для каждого инструмента: имя, описание, JSON Schema параметров
   (генерируется из endpoint'а в data-service), делегирование вызовов
   через HTTP к data-service.
4. `internal/runtime/openapi_gen.go` — генерирует OpenAPI из runtime.
   Это даёт нам одновременно описание для MCP-tools и для Swagger UI.
5. Существующие 8 тулов в `mcp_server/server.py` остаются как
   Python-fallback в течение переходного периода, но помечаются
   `deprecated` в OpenAPI/UI.
6. `mcp_server/` (Python) — переводим в режим «legacy», удаляем
   после того, как все тесты переехали.

**Что НЕ делаем:** SDK, web, RAG — не трогаем.

**Критерии готовности:**

- 8 существующих MCP-инструментов доступны через Go-сервис.
- `mcp-go-server` показывает их описание через `tools/list`.
- Все 4 файла тестов в `mcp_server/tests/unit/` либо переписаны на
  Go, либо остаются как Python-обёртки над Go-сервером и проходят.
- Latency tool-вызова через Go-MCP ≤ latency через Python-MCP
  (нагрузочный smoke-тест, не формальный бенчмарк).

---

### Фаза 3.5 — Generic SDK контракты (2–3 недели)

**Цель:** Pydantic-модели в SDK generic'ифицируются. Старые имена — алиасы.

**Что делаем:**

1. `agent_tutor_sdk/contracts/entity.py` — новый generic `Entity`
   (`BaseModel` с `fields: dict[str, Any]` + `meta: EntityMeta`).
2. Старые модели (`Student`, `Teacher`, ...) — `class Student(Entity)`,
   помечены `deprecated`. `model_config["deprecated"] = True`.
3. `data_client.py` — generic-метод `request(method, path, params, body)` +
   типизированные обёртки `get(entity, id)`, `find(entity, field, value)`,
   `list(entity, filters)`.
4. Старые методы (`get_student`, `find_student_by_name`, ...) остаются
   как deprecated-обёртки над generic-методом.
5. Drift-тесты: проверка теперь — что JSON Schema из runtime
   (`GET /openapi.json` у data-service) совпадает с эталонной
   `specs/openapi.json` (генерируется из конфига).

**Что НЕ делаем:** web-UI, RAG.

**Критерии готовности:**

- Существующие 113 Python-тестов продолжают проходить (через deprecated
  обёртки).
- Новый generic-клиент используется в новых тестах.
- `agent_tutor_sdk` версионируется (новая мажорная версия — из-за
  добавления generic-слоя).

---

### Фаза 3.6 — Generic web UI (2–3 недели)

**Цель:** фронтенд перестаёт рендерить «вкладку Студенты». Рендерит
универсальную таблицу по метаданным из data-service.

**Что делаем:**

1. `demo/web/static/app.js` — заменяем хардкод-таблицы на рендер по
   схеме endpoint'а. Один компонент `EntityTable` принимает метаданные
   колонок из первого ответа (или из `GET /entities/{name}/schema`).
2. `demo/web/server.py` — заменяем захардкоженные маршруты
   `/api/data/students` на generic `/api/data/{entity}` с whitelist
   по конфигу.
3. `demo/web/static/index.html` — вкладки генерируются по списку
   entities из конфига (отдаётся через `/api/data/entities`).
4. Чат с агентом остаётся как есть, но вызовы к MCP-tools идут через
   generic-механизм.

**Что НЕ делаем:** RAG, fixtures.

**Критерии готовности:**

- UI работает с **любым** конфигом data-service (подмена `config.example.json`
  на другой → UI перерисовывается).
- 18 тестов в `demo/web/tests/unit/test_proxy.py` — переписаны на generic.

---

### Фаза 3.7 — Multi-tenancy, admin API, hot reload (2 недели)

**Цель:** платформа готова к SaaS-режиму.

**Что делаем:**

1. `cfg.auth.row_filters[]` — `WHERE tenant_id = :tenant_id` применяется
   на уровне query builder.
2. `POST /admin/config` (защищён admin-токеном) — PUT новый конфиг,
   валидация по schema, hot reload без рестарта.
3. `GET /admin/config` — текущий конфиг (без секретов).
4. `GET /admin/config/versions` — история конфигов.
5. Watcher (`fsnotify`) на config-файл в dev-режиме.
6. Конфиг-стор: переключение с файла на platform-БД (отдельная БД
   платформы, не клиентская) с миграцией.

**Что НЕ делаем:** полноценная авторизация пользователей (логин/пароль).

**Критерии готовности:**

- Два тенанта с разными конфигами работают на одном data-service.
- Изменение конфига через admin API применяется без рестарта.
- Существующие тесты + новые multi-tenant тесты — зелёные.

---

### Фаза 3.8 — Generic dev-инструментарий (опционально, 1–2 недели)

**Цель:** `seedgen`, `cli_docgen`, `document_generator` generic'ифицируются.

**Что делаем:**

1. `agent-seedgen` принимает на вход конфиг (или endpoint `/entities`)
   и генерирует seed для **любого** конфига, а не для university-БД.
2. `agent-rag-docgen` итерирует по `entities` из конфига, генерирует
   тематические документы.

**Критерии готовности:**

- `agent-seedgen --config <other>.json` создаёт seed для произвольного
  конфига.
- Не блокирует выход на прод (если не успеваем — откладываем).

---

### Фаза 3.9 — UI-конфигуратор (отдельный roadmap)

**Цель:** клиент сам правит конфиг через Web UI.

> Эта фаза — за пределами core-платформы. Она требует отдельного
> дизайна (визард, drag-and-drop схема, live-превью, версионирование).
> Архитектурные заделы для неё уже есть в фазе 3.7 (admin API).

**Что нужно сделать до старта фазы 3.9:**

- Решить, будет ли это отдельный фронтенд или часть `demo/web`.
- Решить, где хранить UI-проекты конфигов (в platform-БД, рядом с
  версиями конфигов).
- Нарисовать UX на 1–2 примерах (Postgres-БД и SQLite-БД).

---

## 9. Технологические развилки (зафиксировать в начале)

### Postgres driver в Go: `pgx` vs `lib/pq`

Рекомендация: **`jackc/pgx/v5`** через `stdlib` (`database/sql`-совместимый
драйвер). Причины: лучшая производительность, нативная поддержка
`LISTEN/NOTIFY` для инвалидации кеша, активно поддерживается.

Альтернатива — `lib/pq` в режиме maintenance. Отвергаем: проект в
maintenance mode с 2024.

### MCP-библиотека для Go

Кандидаты: `mark3labs/mcp-go`, `mcp-sdk-go` (в процессе),
`metoro-io/mcp-golang`. Выбор фиксируем на этапе 3.4 после spike'а.

### JSON Schema validator в Go

`xeipuuv/gojsonschema` (аккуратный, стабильный) или
`kaptinlin/gojsonvalidator` (быстрее, но менее зрелый).
Рекомендация: `gojsonschema`, в фазе 3.4 — пересмотр.

### Платформа для admin-БД

SQLite по умолчанию (как сейчас), опционально PostgreSQL через
`DATABASE_URL` (как сейчас в data-service). Ничего нового.

---

## 10. Метрики успеха фазы 3

После завершения фаз 3.0–3.7 платформа должна удовлетворять:

1. **Time-to-first-query.** Подключение новой БД клиента (с известной
   схемой) до первого работающего `/students`-подобного endpoint'а —
   ≤ 30 минут ручной работы (без UI).
2. **Zero-code новый домен.** Для клиента с PostgreSQL-БД, где таблицы
   называются по-человечески (`customers`, `orders`, `products`),
   платформа выдаёт работающий API **без единой строки кода** —
   только конфиг и/или auto-discovery.
3. **Backward-compat.** Существующий demo-сценарий (university-БД с
   агентом, чатом, RAG, web-UI) продолжает работать без изменений
   в пользовательском опыте.
4. **Тесты.** Все ранее зелёные тесты остаются зелёными (через
   backward-compat обёртки или новый generic-путь). Покрытие generic-слоя
   ≥ 70%.
5. **Документация.** `NEW_ROADMAP.md` (этот файл) + ADR по ключевым
   решениям + обновлённый `README.md` отражают новое устройство.

---

## 11. Что **точно не делаем** в этой фазе

- Не переписываем `rag/` на Go (он стабилен, generic'ификация
  не требуется для нашей задачи).
- Не строим полноценный UI-конфигуратор (фаза 3.9).
- Не подключаем CRM-адаптеры (это следующая дорожная карта после 3.9).
- Не делаем авторизацию пользователей с логином/паролем.
- Не уходим в k8s/Helm/Istio.
- Не пишем свой MCP-протокол — используем существующие SDK.
- Не меняем LiteLLM-цикл агента (он работает через абстрактный MCP,
  generic-ификация MCP покрывает его автоматически).

---

## 12. Сводка по этапам (timeline)

| Фаза | Содержание | Зависимости | Длительность |
|---|---|---|---|
| 3.0 | Контракты конфига, baseline OpenAPI | — | 1–2 нед |
| 3.1 | Postgres driver, introspector (обе БД) | 3.0 | 2–3 нед |
| 3.2 | Config loader, query/endpoint builder | 3.1 | 3–4 нед |
| 3.3 | Удаление domain-specific кода в data-service | 3.2 | 1–2 нед |
| 3.4 | MCP на Go, generic tool registry | 3.2 (конфиг стабилен) | 3–4 нед |
| 3.5 | Generic SDK контракты | 3.2 | 2–3 нед |
| 3.6 | Generic web UI | 3.5 | 2–3 нед |
| 3.7 | Multi-tenancy, admin API, hot reload | 3.6 | 2 нед |
| 3.8 | Generic dev-инструментарий (опц.) | 3.5 | 1–2 нед |
| 3.9 | UI-конфигуратор | 3.7 | отдельный roadmap |

**Параллельность:**

- 3.5 и 3.6 могут идти параллельно с 3.4 (после 3.2 — конфиг стабилен).
- 3.7 ждёт 3.6.
- 3.8 — фоновая, не блокирует.

**Общая оценка:** ~4–5 месяцев работы в режиме full-time (1 разработчик).
С учётом ревью, интеграционных тестов, документирования — 5–7 месяцев.

---

## 13. Контрольные точки (checkpoints)

| Чекпоинт | Что должно работать |
|---|---|
| После 3.0 | `specs/config.schema.json` валиден. Существующая система не сломана. |
| После 3.1 | `cmd/discover` читает Postgres и SQLite, выдаёт generic Schema. |
| После 3.2 | data-service в режиме `RUNTIME_MODE=config` отвечает на 11 старых endpoint'ов через конфиг. |
| После 3.3 | data-service собирается без repository/handlers/models/schema.sql. |
| После 3.4 | 8 старых MCP-инструментов работают через Go-сервис. |
| После 3.5 | SDK имеет generic `Entity` + deprecated алиасы. |
| После 3.6 | Web UI работает с любым конфигом data-service. |
| После 3.7 | Два тенанта с разными конфигами сосуществуют. |
| После 3.9 | Клиент правит конфиг через Web UI без знания Go/Python. |

---

## 14. Открытые вопросы (требуют решения до старта фаз)

| # | Вопрос | Где решается |
|---|---|---|
| Q1 | `pgx/v5` vs другие Postgres-драйверы | ADR перед фазой 3.1 |
| Q2 | Имя нового сервиса: `mcp-gateway`, `mcp-server`, `gateway`? | Перед фазой 3.4 |
| Q3 | Версионирование конфигов: в platform-БД или в git? | Перед фазой 3.7 |
| Q4 | Multi-tenancy через `X-Tenant-ID` или отдельные инстансы data-service? | Перед фазой 3.7 (влияет на query builder) |
| Q5 | Где живёт generic `Entity`-модель: SDK или общий `pkg/`? | Перед фазой 3.5 |
| Q6 | Куда деваются существующие `mcp_server/tests/unit/test_*_tools.py`? | В фазе 3.4 |
| Q7 | Объединять ли когда-нибудь data-service и mcp-gateway в один бинарник? | После 3.7, по данным профилирования |
