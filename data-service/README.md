# Data Service

HTTP-сервис доступа к произвольной БД через config-driven REST API. Написан на Go.

**Архитектура:** сервис читает JSON-конфиг, на его основе строит эндпоинты и query builder.
Никакого захардкоженного знания о домене — вся семантика в конфиге.

## Принципиальная схема

```
                    ┌──────────────────┐
                    │   config.json    │  ← ручной / --discover
                    │  (entities,      │
                    │   endpoints,     │
                    │   custom queries)│
                    └──────┬───────────┘
                           ▼
data-service ──JSON-конфиг──▶ chi-роутер ──▶ runtime handlers
   │                                              │
   │  ┌─────────────────────────────────┐         │
   ├──│ Introspect (sqlite_master /     │         │
   │  │  information_schema)            │         ▼
   │  └────────┬────────────────────────┘      query_builder
   │           │                                (SELECT + prepared)
   ▼           ▼                                    │
[клиентская БД]                                      ▼
(SQLite / PG)                                  database/sql
```

## Чем отличается от предыдущей версии

| Было (хардкод) | Стало (config-driven) |
|---|---|
| SQL-запросы в `internal/repository/` | SQL в `config.example.json` (`custom_queries`) |
| 7 Go-структур в `internal/models/` | 1 generic `Entity{Fields}` |
| 6 domain-хендлеров в `internal/handlers/` | 6 runtime-хендлеров (generic: `get_by_id`, `find`, `list`, `custom_query`) |
| `/openapi.json` зашит `//go:embed` | **Runtime-генерация** на каждый запрос |
| Конфиг пишется руками | `--discover` / `GET /admin/discover` / `POST /admin/config/rewrite` |
| Только university-схема | **Любая БД** — конфиг описывает что угодно |

## Быстрый старт

```bash
# 1. Собрать
cd data-service && go build -o bin/data-service ./cmd/server/

# 2. Сгенерировать конфиг из существующей БД
DB_PATH=../university.db ./bin/data-service --discover > ../specs/config.generated.json

# 3. Запустить
./bin/data-service --config ../specs/config.generated.json

# 4. Проверить
curl http://127.0.0.1:8084/students/ | head -c 200
```

## Архитектура

```
cmd/server/main.go            ← точка входа, graceful shutdown, флаги
  │
  ├── --discover              ← прочитать схему БД → вывести config.json в stdout
  ├── --config <path>         ← путь к config.json (по умолчанию $DS_CONFIG или specs/config.example.json)
  ├── --seed [path]           ← dev-only: залить тестовые данные
  └── DS_DISCOVER=true        ← env-вариант --discover

internal/
├── config/                    ← загрузка, валидация, envsubst
│   ├── loader.go              ← Load(path) → *Config
│   ├── validate.go            ← JSON Schema validation
│   ├── types.go               ← Config, Entity, Endpoint, CustomQuery, ...
│   ├── envsubst.go            ← ${ENV:-default} подстановка
│   └── store.go               ← FileStore / DbStore (фаза 3.7+)
│
├── datasource/                ← адаптеры БД
│   ├── adapter.go             ← Adapter interface {Connect, Introspect, ...}
│   ├── sqlite_adapter.go      ← SQLite (modernc.org/sqlite)
│   ├── postgres_adapter.go    ← PostgreSQL (pgx/v5)
│   └── registry.go            ← реестр драйверов
│
├── runtime/                   ← generic query builder + хендлеры
│   ├── types.go               ← Entity, CustomQuery, AdapterSubset
│   ├── query_builder.go       ← BuildGetByID, BuildFind, BuildList, BuildCustomQuery
│   ├── response_mapper.go     ← MapRow, MapCustomQueryRow, MapRows
│   ├── entity_resolver.go     ← Resolve(entityName) → Entity
│   ├── converter.go           ← Config → runtime types
│   └── handlers/              ← generic HTTP-хендлеры
│       ├── get_by_id.go       ← GET /{entity}/{id}
│       ├── find.go            ← GET /{entity}?search=...
│       ├── list.go            ← GET /{entity} (fallback)
│       ├── custom_query.go    ← произвольный SELECT из конфига
│       ├── health.go          ← GET /health
│       ├── stats.go           ← GET /stats
│       ├── context.go         ← Context {DB, Builder, Resolver, ...}
│       └── default.go         ← 404, 405
│
├── configgen/                 ← генерация конфига из интроспекции
│   └── configgen.go           ← Generate(schema, ds) → *Config
│
├── openapigen/                ← runtime-генерация OpenAPI
│   └── openapigen.go          ← Generate(cfg, host, title, version, hasAdmin) → spec
│
├── server/                    ← HTTP-сервер
│   ├── server.go              ← middleware (recovery, request ID, structured logging)
│   ├── endpoint_builder.go    ← NewRouterFromConfig + discover/rewrite handlers
│   └── swagger.go             ← /docs (Swagger UI), /openapi.json (runtime)
│
├── seedgen/                   ← dev-only: загрузка seed.json
│   └── seedgen.go             ← Load, Apply, TestSeed
│
└── db/                        ← legacy connector (только для тестов)
    └── connector.go           ← DB interface + New()
```

## API

### Пользовательские эндпоинты (из конфига)

Эндпоинты определяются в `config.json` → `endpoints[]`. Типовой набор:

| Метод | Путь | Описание | Тип |
|---|---|---|---|
| GET | `/health` | Статус сервиса и БД | builtin |
| GET | `/stats` | Количество записей во всех сущностях | builtin |
| GET | `/{entity}/{id}` | Одна запись по ID | `get_by_id` |
| GET | `/{entity}?field=...` | Поиск по полю или список всех | `find` |
| GET | `/{entity}/{id}/...` | Произвольные связанные данные | `custom_query` |

Точный список — в `/openapi.json` живого сервиса или в `specs/config.example.json`.

### Админские эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/admin/discover` | Прочитать схему БД, сгенерировать и отдать конфиг |
| GET | `/admin/discover?raw=true` | То же, но чистый JSON (можно сохранить в файл) |
| POST | `/admin/config/rewrite` | Прочитать схему, сгенерировать, **сохранить в config-файл** |

> Админские эндпоинты доступны только если адаптер данных передан в роутер.

### Системные

| Путь | Описание |
|---|---|
| `/docs` | Swagger UI |
| `/openapi.json` | OpenAPI 3.1.0 — **runtime-генерация из текущего конфига** |

## Конфигурация

### Формат

Конфиг — JSON, валидируется по `specs/config.schema.json`. Ключевые секции:

```jsonc
{
  "version": 1,
  "data_source": {
    "driver": "sqlite",                    // sqlite | postgres
    "dsn": "${DB_PATH:-university.db}",    // поддержка ${ENV}
    "pool_size": 10,
    "read_only": true
  },
  "entities": [                            // описание таблиц
    {
      "name": "student",                   // публичное имя
      "table": "students",                 // имя в БД
      "id_column": "id",
      "fields": [
        { "name": "full_name", "column": "name", "type": "string" },
        { "name": "course",   "column": "course", "type": "int" }
      ]
    }
  ],
  "endpoints": [                           // какие эндпоинты публикуем
    { "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" },
    { "method": "GET", "path": "/students", "op": "find", "entity": "student", "search_field": "full_name" },
    { "method": "GET", "path": "/students/{id}/grades", "op": "custom_query", "query_id": "student_grades" }
  ],
  "custom_queries": {                      // whitelist SELECT-запросов
    "student_grades": {
      "sql": "SELECT g.id, g.grade, d.name AS discipline_name FROM grades g LEFT JOIN disciplines d ON d.id = g.discipline_id WHERE g.student_id = ?",
      "params": ["id"],
      "result_mapping": { "id": {"type": "string"}, "grade": {"type": "string"}, "discipline_name": {"type": "string"} },
      "max_rows": 500
    }
  },
  "stats": {
    "counters": [{ "name": "students", "entity": "student" }]
  }
}
```

### Генерация конфига (--discover)

Если подключиться к БД — конфиг можно сгенерировать автоматически:

```bash
# CLI
./data-service/bin/data-service --discover > config.json

# Env
DS_DISCOVER=true ./data-service/bin/data-service > config.json

# HTTP (на живом сервисе)
curl http://localhost:8084/admin/discover?raw=true > config.json

# HTTP — переписать конфиг на диске (сохраняется в тот же путь что и текущий)
curl -X POST http://localhost:8084/admin/config/rewrite
```

Генерируется:
- Entities для каждой таблицы (колонки, PK, типы)
- `get_by_id` для таблиц с одной PK
- `find` для таблиц с name-полем
- `/health`, `/stats`

Не генерируется (дописывается руками):
- `custom_queries` (JOIN'ы, вложенные объекты)
- `params` для path/query параметров
- MCP tools

### Пример: смена БД

```bash
# PostgreSQL
cat > pg-config.json << 'EOF'
{
  "version": 1,
  "data_source": {
    "driver": "postgres",
    "dsn": "postgres://user:pass@host:5432/mydb?sslmode=disable",
    "pool_size": 25
  }
}
EOF

# Сгенерировать entities из реальной схемы
DS_CONFIG=pg-config.json ./data-service/bin/data-service --discover > full-config.json

# Запустить
./data-service/bin/data-service --config full-config.json
```

## Ключевые принципы безопасности

1. **Подготовленные выражения** — все параметры через `?`/`$1`, никогда не конкатенируются
2. **Whitelist операций** — только SELECT, обязателен `max_rows` для custom_query
3. **Чужая БД — read-only** — data-service не пишет в клиентскую БД
4. **Read-only режим** — `read_only: true` по умолчанию, принудительно
5. **Валидация конфига** — JSON Schema (`specs/config.schema.json`) при загрузке

## Запуск

```bash
# Из корня проекта
# Сборка
cd data-service && go build -o bin/data-service ./cmd/server/

# Dev (SQLite)
./bin/data-service

# Dev (PostgreSQL)
DATABASE_URL=postgresql://... ./bin/data-service --config pg-config.json

# Seed-режим (dev-only, залить тестовые данные)
./bin/data-service --seed

# Кастомный порт
PORT=8085 ./bin/data-service
```

### Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `DS_CONFIG` | `specs/config.example.json` | Путь к конфигу |
| `PORT` | `8084` | Порт HTTP |
| `LOG_LEVEL` | `info` | `info` или `debug` |
| `DS_DISCOVER` | — | Включить режим --discover (генерировать конфиг из БД и выйти) |
| `DB_DRIVER` | `sqlite` | Для --seed режима |
| `DB_PATH` | `university.db` | Для --seed режима |
| `DATABASE_URL` | — | Для --seed режима (PostgreSQL) |
| `CONFIG_SCHEMA` | `specs/config.schema.json` | Путь к JSON Schema |

### Docker

```bash
docker build -t agent-tutor-data -f data-service/Dockerfile .
docker run -p 8084:8084 -v $(pwd)/config.json:/config.json \
  -e DS_CONFIG=/config.json agent-tutor-data
```

## Тестирование

```bash
# Все Go-тесты
go test ./... -count=1

# Только unit
go test ./internal/config/... ./internal/runtime/... ./internal/configgen/...

# С реальной БД
DB_PATH=../university.db go test ./internal/configgen/... -run TestGenerate_RealDB
```

Python-тесты (MCP, API, Web) стучатся к data-service по HTTP и не знают о его внутренней архитектуре.

## Roadmap (следующие шаги)

Следующие фазы описаны в `doc/NEW_ROADMAP.md`. Кратко:

- **Фаза 3.4** — MCP-сервер на Go, инструменты генерируются из конфига
- **Фаза 3.5** — Generic SDK контракты (Entity вместо конкретных моделей)
- **Фаза 3.6** — Generic Web UI (рендер по схеме endpoint'а)
- **Фаза 3.7** — Multi-tenancy, admin API, hot reload
- **Фаза 3.9** — UI-конфигуратор
