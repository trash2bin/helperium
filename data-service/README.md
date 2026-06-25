# Data Service

HTTP-сервис доступа к данным университета. Написан на Go.

**Это единственный сервис в проекте, который знает схему БД** — имена таблиц, колонок, JOIN'ы, типы ключей.  
Все остальные сервисы (MCP, API, CLI) получают данные через HTTP и не имеют SQL-запросов.

## Зачем

### Проблема

До появления data-service каждый сервис имел прямой доступ к БД через Python SDK (`agent_tutor_sdk/db/`). SQL-запросы были размазаны по всему проекту:

```
mcp_server (Python) ──SQL──→ SQLite
demo/api (Python)    ──SQL──→ SQLite
fixtures (Python)    ──SQL──→ SQLite
```

При смене схемы БД (реальная БД вуза вместо фикстур) нужно было править все три места одновременно.

### Решение

Единственный источник SQL — `internal/repository/`. Никто больше не содержит SQL, таблиц или колонок:

```
mcp_server (Python) ──HTTP──→ data-service (Go) ──SQL──→ любая БД
                                          ↑                (SQLite / PG / Oracle / ...)
                                    internal/repository/
                                    (единственное место с SQL)
```

**При смене БД переписывается только `internal/repository/`**. HTTP-контракт, модели, хендлеры — не трогаются.

## Архитектура

```
cmd/server/main.go         ← точка входа, graceful shutdown
│
└── internal/
    ├── db/
    │   ├── connector.go   ← интерфейс DB {QueryRowContext, QueryContext, Ping}
    │   └── sqlite.go      ← реализация для SQLite (modernc.org/sqlite)
    │
    ├── repository/        ← ⚡ ЕДИНСТВЕННОЕ МЕСТО С SQL ⚡
    │   ├── students.go    ← SELECT ... FROM students JOIN groups ...
    │   ├── teachers.go    ← SELECT ... FROM teachers ...
    │   ├── grades.go      ← SELECT ... FROM grades LEFT JOIN disciplines ...
    │   ├── schedule.go    ← (встроен в students.go)
    │   ├── disciplines.go ← SELECT ... FROM disciplines ...
    │   ├── stats.go       ← SELECT COUNT(*) FROM ...
    │   └── helpers.go     ← парсинг lessons_json, group-хелперы
    │
    ├── handlers/          ← HTTP-обработчики (НЕ знают SQL!)
    │   ├── students.go    ← GET /students/{id} → repo.GetByID → JSON
    │   ├── teachers.go
    │   ├── grades.go
    │   ├── schedule.go
    │   ├── disciplines.go
    │   └── stats.go
    │
    ├── models/models.go  ← доменные модели (семантические поля: full_name, value)
    │                         Поля не зависят от имён колонок в БД.
    │
    └── server/
        ├── server.go      ← chi-роутер, регистрация всех маршрутов
        ├── middleware.go  ← structured JSON-логи, correlation-id, recovery
        └── swagger.go     ← /docs (Swagger UI), /openapi.json
```

## API

| Путь | Описание |
|---|---|
| `GET /health` | Статус сервиса и БД |
| `GET /stats` | Количество записей во всех таблицах |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | OpenAPI 3.1.0 спецификация |
| `GET /students/:id` | Карточка студента |
| `GET /students?name=...` | Поиск студента по ФИО |
| `GET /students/:id/disciplines` | Дисциплины студента |
| `GET /students/:id/grades?discipline_id=` | Оценки студента (опционально по дисциплине) |
| `GET /teachers?name=...` | Поиск преподавателя по ФИО |
| `GET /teachers/:name/schedule?day=` | Расписание преподавателя |
| `GET /groups/:id/schedule?day=` | Расписание группы |
| `GET /disciplines` | Все дисциплины |

Все ответы соответствуют JSON Schema из `specs/schemas/*.schema.json`.

## Запуск

```bash
# Dev (SQLite, из корня проекта)
cd data-service
DB_PATH=../university.db go run ./cmd/server/

# С другими портом
PORT=8085 DB_PATH=../university.db go run ./cmd/server/
```

Переменные окружения:

| Переменная | Дефолт | Описание |
|---|---|---|
| `DB_DRIVER` | `sqlite` | Драйвер БД (`sqlite` или `postgres`) |
| `DB_PATH` | `university.db` | Путь к файлу SQLite |
| `DATABASE_URL` | — | Строка подключения PostgreSQL |
| `PORT` | `8084` | Порт HTTP |
| `LOG_LEVEL` | `info` | Уровень лога (`info` или `debug`) |

## Docker

```bash
# Сборка
docker build -t agent-tutor-data -f data-service/Dockerfile .

# Запуск
docker run -p 8084:8084 -v $(pwd)/university.db:/university.db \
  -e DB_PATH=/university.db agent-tutor-data
```

Образ собирается в two-stage (`golang:1.22-alpine` → `scratch`).  
Бинарник ~15 МБ.

## Как переписать под новую БД

Это главная причина существования data-service. Вот аккуратная процедура:

### 1. Меняете только `internal/repository/`

SQL-запросы живут только здесь. При замене БД:

1. **Правите SQL** в файлах `students.go`, `teachers.go`, `grades.go`, `disciplines.go`, `stats.go`
2. **Меняете маппинг** в `helpers.go` (если новая БД не использует `lessons_json`)
3. **Ничего больше не трогаете**

Модели (`internal/models/`) не меняются — они отражают HTTP-контракт, а не схему хранения.  
Handlers (`internal/handlers/`) не меняются — они вызывают репозиторий и возвращают JSON.  
OpenAPI (`/openapi.json`) не меняется — контракт стабилен.

### 2. Если нужен PostgreSQL вместо SQLite

Добавить реализацию `db.DB` в `internal/db/postgres.go`:

```go
package db

type PostgresDB struct { conn *sql.DB }

func (p *PostgresDB) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
    return p.conn.QueryRowContext(ctx, query, args...)
}
// ... и т.д.
```

И зарегистрировать в `connector.go`:

```go
case "postgres":
    return NewPostgres()
```

Репозитории не трогаются — они работают через интерфейс `DB`.

### 3. Если схема полностью другая

Пример: текущая БД хранит `schedule` как JSON-поле `lessons_json`, а новая БД использует нормализованную таблицу `lessons`.

Меняется только `internal/repository/students.go`:

```go
// Было:
func (r *StudentRepo) GetSchedule(ctx context.Context, groupID string, day *string) {
    // SELECT lessons_json FROM schedule WHERE group_id = ?
    // парсинг JSON → models.Lesson
}

// Стало:
func (r *StudentRepo) GetSchedule(ctx context.Context, groupID string, day *string) {
    // SELECT l.discipline_id, l.room, t.name
    // FROM lessons l
    // JOIN teachers t ON t.id = l.teacher_id
    // WHERE l.group_id = $1
}
```

Handlers, модели, HTTP-статусы — не меняются. Потребители (MCP, API, CLI) не знают, что внутри что-то изменилось.

### 4. Проверка

```bash
# Unit-тесты (с in-memory SQLite)
go test ./internal/server/ -v

# E2E: Python-тесты через data-service
USE_DATA_SERVICE=1 DATA_SERVICE_URL=http://127.0.0.1:8084 \
  uv run pytest mcp_server/tests/
```

## Принципы

- **SQL только в `internal/repository/`** — ни один другой пакет не содержит SQL
- **Handlers не знают DB** — они вызывают `repo.GetByID()` и получают готовые модели
- **Модели семантичны** — `full_name`, а не `name`; `value`, а не `grade`
- **OpenAPI — контракт** — стабилен независимо от схемы БД
- **Кодогенерация моделей** (в перспективе): `datamodel-codegen --input specs/schemas/ --output models/`
