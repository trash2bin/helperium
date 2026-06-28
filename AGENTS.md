# AGENTS.md

Управление проектом для разработчиков и AI-агентов.

## Проект

- Полноценный агент с разделёнными сервисами: `mcp`, `rag`, `api`, `web` как long-running сервисы, и CLI-утилиты `agent-rag-ingest`, `agent-rag-docgen`, `agent-seedgen` как one-shot команды.
- Управление зависимостями и запуском — через `uv` и `pyproject.toml`.
- Все сервисы запускаются независимо и общаются друг с другом по HTTP.
- CLI для документов и генерации: `agent-rag-ingest` (RAG-документы), `agent-rag-docgen` (генерация материалов). Детали в `specs/fixtures/README.md`
- База данных: **SQLite** (по умолчанию) или **PostgreSQL** (через `DATABASE_URL`). Абстракция в `db/connector.py`.
- **Архитектурная гибкость**: каждый сервис — self-contained единица с собственным HTTP-контрактом.
  Сервис можно переписать на другом языке, не трогая соседей — достаточно реализовать тот же
  HTTP-контракт (OpenAPI-спецификация). База данных переключается через `DATABASE_URL` без
  изменения логики сервисов. CLI-утилиты (`rag/fixtures/`) — это dev-инструментарий, не production-сервисы.

## Базовые команды

```bash
uv sync
uv run agent-tutor              # MCP-сервер (порт 8083, HTTP-транспорт)
uv run agent-rag                # RAG HTTP-сервис (порт 8082)
uv run agent-chat-api           # API сервер с агентом (порт 8081)
uv run agent-demo-web           # Веб-сервер (порт 8080)
uv run agent-rag-ingest --help      # CLI для работы с RAG-документами
uv run agent-rag-docgen --help    # CLI для генерации учебных материалов
uv run pytest                   # Запуск тестов (unit и integration)
```

### Data-service (Go)

```bash
cd data-service
go build -o bin/data-service ./cmd/server/      # сборка
./bin/data-service                                   # запуск с дефолтным конфигом
./bin/data-service --config path/to/config.json      # кастомный конфиг
./bin/data-service --discover > config.json          # генерация конфига из схемы БД

go run ./cmd/seed-cli/                              # заливка seed-данных (dev-only)
go run ./cmd/seed-cli/ --seed-path path/to/seed.json
```

После изменений в логике API или RAG — запускай тесты для проверки регрессий.

## Способы запуска

| Способ | Платформа | Команда |
|---|---|---|
| **Нативный** (через `uv`) | Mac (Apple Silicon), Linux, наверное и Windows | `./scripts/dev.sh start` |
| **Docker** | Linux / предпрод / прод | `docker compose up -d` |
| **Docker + HTTPS** | Прод | `docker compose --profile prod up -d` |

Код сервисов один и тот же, разница только в оркестрации.

### 🖥️ Нативный запуск (Mac)

**Скрипт**: `scripts/dev.sh` — поднимает все 4 long-running сервиса в фоне, ждёт `/health` каждого.

```bash
./scripts/dev.sh start         # Поднять все сервисы
./scripts/dev.sh status        # Проверить статус
./scripts/dev.sh logs api      # tail -f .data/logs/api.log
./scripts/dev.sh logs all      # tail -f всех сразу
./scripts/dev.sh stop          # Остановить всё
./scripts/dev.sh restart       # Перезапустить
```

Логи: `.data/logs/{rag,mcp,api,web}.log`. PID-файлы: `.data/pids/*.pid`. `.env` грузится автоматически.

**Порядок ожидания**: `rag → mcp → api → web` (каждый ждёт `/health` предыдущего, таймаут 60с).

### 🐳 Docker-запуск (Linux / предпрод / прод)

**Файлы контейнеризации**:

| Файл | Назначение |
|---|---|
| `rag/Dockerfile` | Образ RAG-сервиса |
| `mcp_server/Dockerfile` | Образ MCP-сервера |
| `demo/api/Dockerfile` | Образ API-сервера |
| `demo/web/Dockerfile` | Образ WEB-сервера |

| `docker-compose.yml` | Оркестрация: 7 сервисов, healthchecks, volumes |
| `Caddyfile` | HTTPS-прокси через Caddy (профиль prod) |
| `.env.example` | Полный список переменных с дефолтами |
| `.dockerignore` | Исключения для Docker build |

```bash
# Dev-режим: 4 long-running сервиса
docker compose up -d

# Prod-режим: + Caddy (HTTPS через Let's Encrypt)
docker compose --profile prod up -d

# CLI-утилиты (через uv, не Docker)
uv run agent-rag-ingest list
uv run agent-rag-docgen generate -d "cs-101"

# Сборка образов
docker compose build
```

**Healthchecks**: `rag` (start_period=120s — cold start embedding), `mcp` → ждёт `rag`, `api` → ждёт `mcp`, `web` → ждёт `api`.

**Тома (в `./.data/`)**:

| Том | Контейнер-путь | Содержимое |
|---|---|---|
| `app_data` | `/data/app` | `university.db`, `demo_sessions.sqlite`, `backlog/` |
| `rag_data` | `/data/rag` | `chroma_db/` |
| `hf_cache` | `/home/app/.cache/huggingface` | embedding-модели |
| `pg_data` | `/var/lib/postgresql/data` | PostgreSQL-данные |

### 🐘 PostgreSQL (опционально, вместо SQLite)

По умолчанию — SQLite (`university.db`). Для PostgreSQL задай `DATABASE_URL`:

```bash
# Локальный PostgreSQL через Docker
docker compose up -d db

# Запуск сервисов с PostgreSQL
DATABASE_URL=postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor ./scripts/dev.sh start

# Или через .env
echo 'DATABASE_URL=postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor' >> .env
```

**Важно**: сессии чата (`demo_sessions.sqlite`) пока на SQLite — это кэш, а не основные данные.

### 🌱 Сидинг university.db (dev-only)

Проект держит **две независимые БД** — см. раздел «Архитектурная гибкость».

**Pipeline сидинга** (Python faker → JSON → Go seed-cli):

```
agent-seedgen  (Python + faker)
       │
       ▼  specs/fixtures/seed.json
seed-cli  (Go, отдельный бинарник)
       │
       ▼
university.db (SQLite) или PostgreSQL
```

**Шаг 1 — сгенерировать seed.json**:

```bash
uv run agent-seedgen                              # дефолт: 8 групп, 40 студентов
uv run agent-seedgen --students 80 --grades 200 --seed 42
uv run agent-seedgen --out /tmp/my-seed.json
```

**Шаг 2 — залить в БД** (отдельная утилита, не часть data-service):

```bash
cd data-service

# SQLite
go run ./cmd/seed-cli/ --seed-path ../specs/fixtures/seed.json

# PostgreSQL
DATABASE_URL=postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor \
  go run ./cmd/seed-cli/ --driver postgres --seed-path ../specs/fixtures/seed.json
```

**Защита от перезаписи**: `seed-cli` отказывается, если `groups` уже содержит
записи (`ErrDatabaseNotEmpty`). Сначала очисти БД:

```bash
rm -f university.db && go run ./cmd/seed-cli/
# или для PostgreSQL: DROP DATABASE agent_tutor; CREATE DATABASE agent_tutor;
```

**Полная очистка и пересоздание** с нуля:

```bash
./scripts/dev.sh stop
rm -f university.db rag_documents.db
rm -rf chroma_db/ generated_materials/

uv run agent-seedgen --students 80
cd data-service && go run ./cmd/seed-cli/ --seed-path ../specs/fixtures/seed.json

./scripts/dev.sh start

# Импортировать тестовый PDF
uv run agent-rag-ingest import ~/Documents/some.pdf -d <discipline-id>
```

### 🧪 Запуск по одному (ручная отладка)

```bash
# Терминал 1: RAG
RAG_PORT=8082 uv run python -m rag.service

# Терминал 2: MCP
RAG_SERVICE_URL=http://127.0.0.1:8082 MCP_PORT=8083 uv run python -m mcp_server.server

# Терминал 3: API
MCP_SERVICE_URL=http://127.0.0.1:8083/mcp DEMO_API_PORT=8081 uv run python -m demo.api.server

# Терминал 4: WEB
DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=8081 DEMO_WEB_PORT=8080 uv run python -m demo.web.server
```

## Подробная структура проекта

```
agent-tutor/
├── data-service/              # Config-driven Go-сервис доступа к произвольной БД (:8084)
│   ├── cmd/
│   │   ├── server/main.go     # точка входа: graceful shutdown, --config, --discover
│   │   └── seed-cli/main.go   # dev-only: заливка seed-данных (не часть прод-сервера)
│   ├── internal/
│   │   ├── config/            # загрузка, валидация, envsubst, типы конфига
│   │   │   ├── loader.go      # Load(path) → *Config + JSON Schema validation
│   │   │   ├── store.go       # FileStore / DbStore (фаза 3.7+)
│   │   │   └── types.go       # Config, Entity, Endpoint, CustomQuery, ...
│   │   ├── datasource/        # адаптеры БД: sqlite, postgres
│   │   │   ├── adapter.go     # Adapter interface {Connect, Introspect, ...}
│   │   │   ├── sqlite_adapter.go / postgres_adapter.go
│   │   │   ├── registry.go    # реестр драйверов
│   │   │   └── equivalence_test.go # кросс-СУБД контрактные тесты
│   │   ├── runtime/           # generic query builder + хендлеры
│   │   │   ├── query_builder.go    # BuildGetByID, BuildFind, BuildList
│   │   │   ├── entity_resolver.go  # Resolve(entityName) → Entity
│   │   │   ├── response_mapper.go  # MapRow, MapRows
│   │   │   ├── converter.go        # Config → runtime types
│   │   │   └── handlers/      # generic HTTP-хендлеры
│   │   │       ├── get_by_id.go / find.go / list.go
│   │   │       ├── custom_query.go / health.go / stats.go
│   │   │       ├── context.go / default.go
│   │   ├── configgen/         # генерация конфига из интроспекции БД
│   │   │   └── configgen.go   # Generate(schema, ds) → *Config
│   │   ├── openapigen/        # runtime-генерация OpenAPI
│   │   │   └── openapigen.go  # Generate(cfg, host, title, version) → spec
│   │   ├── server/            # chi-роутер, middleware, Swagger UI
│   │   │   ├── server.go      # middleware (Recovery, RequestID, StructuredLogging)
│   │   │   ├── endpoint_builder.go  # NewRouterFromConfig(cfg, db, adapter, ...)
│   │   │   ├── swagger.go     # runtime /openapi.json + Swagger UI
│   │   │   └── server_test.go / router_config_test.go # config-driven e2e
│   │   ├── db/                # legacy connector (только для тестов)
│   │   │   └── connector.go   # DB interface + New() по env
│   │   └── seedgen/           # Go-сидер (dev-only, вызывается из cmd/seed-cli)
│   │       ├── seedgen.go     # Load/Apply, FK-порядок, ErrDatabaseNotEmpty
│   │       └── testdata.go    # TestSeed для in-memory тестов
│   ├── Dockerfile
│   └── README.md              # config-driven архитектура, --discover, примеры
├── mcp_server/                # MCP-сервер (FastMCP, HTTP :8083)
│   ├── server.py              # FastMCP-роутер + /health + uvicorn (:8083)
│   ├── tools_via_http.py      # тонкие async-обёртки над AsyncDataServiceClient
│   ├── tools_rag.py           # обёртки над RagClient (list_documents, search_documents, ...)
│   └── tests/unit/            # 4 файла: discipline/grade/student/teacher_tools через respx-моки
├── rag/                       # RAG HTTP-сервис (FastAPI, :8082)
│   ├── service.py             # FastAPI app: /health /search /context /documents/*, ServiceState singleton
│   ├── db.py                  # RagDB singleton над sqlite3 (WAL, check_same_thread=False)
│   ├── documents_schema.py    # DDL для documents + document_chunks (изолирован от доменной БД)
│   ├── repository.py          # DocumentRepository CRUD + транзакции с откатом
│   ├── pipeline.py            # RAGPipeline: import_document / search_documents / build_rag_context
│   ├── parser.py              # DocumentParser: txt/md напрямую, PDF/DOCX через docling
│   ├── chunker.py             # TextChunker + Semantic/Recursive/Sentence стратегии
│   ├── embeddings.py          # SentenceTransformerEmbedding (ленивая загрузка, batch)
│   ├── vector_store.py        # ChromaDBVectorStore (cosine, PersistentClient)
│   ├── config.py              # RagConfig + from_env() (RAG_*, CHROMA_*)
│   ├── interfaces.py          # Protocol'ы EmbeddingProtocol, VectorStoreProtocol
│   ├── _types.py              # внутренние TypedDict (PageDict, ChunkDict, ...)
│   ├── http_models.py         # Pydantic-DTO HTTP-контракта (SearchRequest, ListDocumentsResponse, ...)
│   ├── pyproject.toml         # манифест + entrypoints (agent-rag, agent-rag-docgen, agent-rag-ingest, agent-seedgen)
│   └── tests/
│       ├── unit/              # pipeline / embeddings / repository / vector_store / service / openapi_spec
│       └── integration/       # test_e2e_pipeline (real SQLite + ChromaDB)
├── agent-tutor-sdk/           # Shared SDK
│   ├── pyproject.toml         # манифест (hatchling, pydantic>=2, httpx>=0.28)
│   ├── src/agent_tutor_sdk/
│   │   ├── contracts/         # Pydantic-модели API-форма (full_name, group-object) — extra="forbid"
│   │   ├── data_client.py     # AsyncDataServiceClient + DataServiceClientSync к data-service:8084
│   │   ├── rag/               # RagClient (async) + RagClientSync + 5 Pydantic-моделей HTTP-контракта RAG
│   │   └── seed_models.py     # Storage-форма Pydantic (name, group_id) для seed.json — extra="forbid"
│   └── tests/unit/            # test_client.py + test_contracts_drift.py + test_seedgen_validation.py
├── demo/
│   ├── api/                   # API-сервер + агент (FastAPI, :8081)
│   │   ├── server.py          # FastAPI app: /health, /api/chat (SSE), /api/backlog*, /api/session/history
│   │   ├── http_models.py     # ChatRequest, HealthResponse, Backlog*, SessionHistory*
│   │   ├── backlog.py         # ModelBacklog: трассировка всех взаимодействий с LLM (jsonl per session)
│   │   ├── sessions.py        # SessionStore: история чатов в SQLite + миграция из agent_memory.json
│   │   ├── agent/             # подмодуль агента
│   │   │   ├── orchestrator.py # LLMAgent + SYSTEM_PROMPT: главный цикл итераций
│   │   │   ├── llm_client.py  # LLMClient поверх LiteLLM (Mistral/Ollama), stream_completion
│   │   │   ├── mcp_client.py  # MCPClient: долгоживущая сессия к MCP через streamable_http_client
│   │   │   ├── tool_parser.py # ToolCallParser: нативные + JSON-блоки tool-вызовы
│   │   │   ├── conversation.py # ConversationManager (sync/async) с per-session asyncio.Lock
│   │   │   └── types.py       # TypedDict: Message, ToolCall, *EventData, SessionId/TurnId
│   │   └── tests/unit/        # agent/* + backlog + sessions + openapi_api drift
│   └── web/                   # Веб-интерфейс (FastAPI, :8080)
│       ├── server.py          # reverse-proxy к api / data-service / rag + статика
│       ├── pyproject.toml     # манифест demo-web (entrypoint agent-demo-web)
│       ├── Dockerfile         # multi-stage python:3.12-slim, non-root app user
│       ├── static/app.js      # vanilla JS фронт: таблицы вкладок + чат с SSE + localStorage
│       └── tests/unit/        # test_proxy.py: 18 тестов reverse-proxy через respx
├── specs/                     # Контракты (source of truth для API)
│   ├── schemas/               # JSON Schema (генерируются из Go-моделей через schema-gen)
│   ├── data-service.openapi.yaml
│   ├── rag.openapi.yaml
│   ├── api.openapi.yaml
│   └── fixtures/              # pipeline сидинга: agent-seedgen → seed.json → data-service --seed
│       ├── README.md          # инструкция по сидингу БД
│       └── seed.json          # детерминированный seed (--seed 42), gitignored
├── rag/fixtures/              # CLI-утилиты (agent-rag-ingest, agent-rag-docgen, agent-seedgen)
│   ├── cli_ingest.py          # CLI обёртка над RagClientSync к RAG-сервису
│   ├── cli_docgen.py          # CLI генерации материалов через Ollama
│   ├── document_generator.py  # MaterialDocumentGenerator: Ollama → DOCX/MD → RAG индекс
│   ├── rag_tools.py           # HTTP-фасад RagClientSync (shim для обратной совместимости)
│   ├── seedgen.py             # генератор seed.json с валидацией через StorageSeed
│   ├── catalog.py             # статический каталог фиктивных данных (TEXTS, SPECIALITIES, CURRICULUM, ...)
│   └── _material.py           # dev-only Pydantic-модель Material
├── scripts/                   # dev.sh (нативный запуск всех 5 сервисов), init-db.sql
├── doc/                       # ROADMAP.md
├── docker-compose.yml         # 5 long-running сервисов + db + caddy (prod-профиль)
└── .env.example               # Все переменные окружения
```

## Инструменты MCP-сервера

Модель может вызывать инструменты для доступа к данным об учебном процессе:

| Инструмент | Что делает |
|---|---|
| `get_student(student_id)` | Карточка студента |
| `find_student_by_name(name)` | Поиск студента по ФИО |
| `get_schedule(group_id, day?)` | Расписание группы, опционально по дню |
| `get_disciplines(student_id)` | Дисциплины студента через его группу |
| `get_materials(discipline_id, type?)` | Список файлов по дисциплине |
| `search_materials(query, discipline_id?)` | Поиск по содержимому материалов |
| `get_student_grades(student_id, discipline_id?)` | Оценки студента, опционально по одной дисциплине |
| `get_teacher_by_name(name)` | Поиск преподавателя |
| `get_teacher_schedule(teacher_name, day?)` | Расписание преподавателя |
| `list_documents(discipline_id?)` | Список документов в RAG-индексе |
| `search_documents(query, discipline_id?, limit?)` | Поиск релевантных фрагментов документов |
| `get_rag_context(query, discipline_id?, limit?)` | Готовый контекст для ответа по документам |

> `import_document` доступен только через CLI `agent-rag-ingest`, не через MCP.

## CLI-утилиты (rag/fixtures/) — назначение и философия

`rag/fixtures/` — это **dev-инструментарий**, не production-сервис. Он существует для:

1. **Импорта документов в RAG** (`agent-rag-ingest`): PDF/DOCX/TXT/MD/HTML →
   чанки → embeddings → ChromaDB. Нужен для наполнения базы тестовыми лекциями
   и методичками, чтобы проверять качество RAG-поиска и ответов агента.

2. **Генерации учебных материалов** (`agent-rag-docgen`): создание лекций, методичек
   и лабораторных работ через Ollama. Нужен для user-тестирования — чтобы у агента
   были реальные документы для поиска, а не только голые записи из `seed.json`.

3. **Сидинга БД вуза** (`agent-seedgen`): генерация `specs/fixtures/seed.json`
   с фейковыми студентами, группами, расписанием, оценками. Затем заливается
   через `data-service --seed` (Go, `internal/seedgen/`).

**Важно**: `rag/fixtures/` — не микросервис и не часть production-архитектуры.
Это утилиты для разработки и тестирования, которые работают с сервисами через HTTP
(как и любой внешний клиент). Если завтра RAG-сервис переписан на Go — `agent-rag-ingest`
продолжает работать, потому что ходит по HTTP, а не через Python-импорты.

Полная документация по командам — в `specs/fixtures/README.md`.

## Архитектурная гибкость

Приложение спроектировано так, чтобы адаптироваться к разным бэкендам и сценариям
без переписывания ядра:

### Смена базы данных
- Университетские данные — через `data-service` (Go). Сервис config-driven: схема БД описывается в конфиге, SQL генерируется runtime query builder'ом.
- При смене реальной БД вуза достаточно написать конфиг (или запустить `--discover`). Никакой Go-код не переписывается.
- `agent_tutor_sdk/db/connector.py` — для тестов, CLI-утилит и RAG (отдельная `rag_documents.db`).
- Сессии чата (`demo_sessions.sqlite`) — на SQLite, это кэш, не основные данные.

### Смена LLM-провайдера
- LiteLLM — единый клиент под Ollama, OpenAI, Mistral, Anthropic, Groq и др.
- Меняется только `OLLAMA_URL` / `MISTRAL_API_KEY` / `OPENAI_API_KEY`

### Замена любого сервиса
- Каждый long-running сервис имеет HTTP-контракт (OpenAPI/Swagger на `/docs`)
- `data-service` (Go) — контракт генерируется runtime из конфига (`/openapi.json`)
- Если переписать `rag` на Go — `mcp_server` продолжает ходить к `http://rag:8082`
- Если переписать `mcp_server` на Rust — `api` продолжает слать MCP-over-HTTP
- Если переписать `data-service` на Rust — все потребители видят тот же HTTP-контракт
- CLI-утилиты (`rag/fixtures/`) не привязаны к Python-коду сервисов — работают по HTTP

### Две независимые БД в проекте

В проекте **сознательно** живут две разные БД, потому что они отвечают за разные вещи:

| БД | Владелец | Что хранит | Наполняется через |
|---|---|---|---|
| `university.db` (или PostgreSQL) | `data-service` (Go) | Доменная модель вуза: студенты, группы, преподаватели, дисциплины, расписание, оценки | `agent-seedgen` (Python+faker) → `specs/fixtures/seed.json` → `data-service --seed` |
| `rag_documents.db` (или PostgreSQL) | `rag` (Python) | Метаданные RAG-индекса: документы + чанки + embedding-мета | `agent-rag-ingest import <file>` (CLI через HTTP) или генерация через `agent-rag-docgen` |
| `chroma_db/` (директория) | `rag` (Python) | Векторный индекс (embeddings) | автоматически при импорте документа в rag |

**Почему нельзя объединить в одну БД**:

- `university.db` — это **данные вуза**. В проде сюда подключается реальная БД вуза (Oracle/PostgreSQL/...), которую трогать нельзя. Менять схему = ломать интеграцию с вузом.
- `rag_documents.db` — это **артефакты нашего приложения** (загруженные PDF, чанки, embeddings). В проде она пуста, пока пользователь не загрузит документы. Схема эволюционирует вместе с приложением.
- Связь между ними только по `discipline_id` (UUID), без FK на уровне БД — каждая БД самостоятельна.

**Граница**: если вуз сменит свою реальную БД — меняется **только** конфиг data-service (или запускается `--discover` заново). RAG-слой и агенты это не замечают.

### Почему это важно
- База вуза может быть PostgreSQL, MySQL, Oracle — data-service поддерживает любую через адаптеры и конфиг
- LLM-провайдер может меняться каждый семестр — LiteLLM проксирует любой API
- Если вуз хочет свой кастомный RAG на Go — не надо переписывать агента

## Демо-часть

**API сервер** (`demo/api/server.py`) — обрабатывает запросы к LLM-провайдерам через LiteLLM и MCP-серверу, обеспечивает вызов инструментов и управление контекстом агента.  
**Веб-сервер** (`demo/web/server.py`) — отдаёт статические файлы интерфейса и проксирует запросы к API (reverse-proxy + SSE-прокси).

Ядро агента — `demo/api/agent/`:
- `orchestrator.py` — оркестратор: вызов моделей, подключение к MCP, рекурсивные вызовы, стриминг
- `llm_client.py` — клиент для работы с LLM через LiteLLM
- `mcp_client.py` — HTTP-клиент для MCP-сервера
- `tool_parser.py` — парсер вызовов инструментов (native + JSON-форматы)
- `types.py` — типы SSE-событий
- `conversation.py` — управление памятью диалога

### Ключевые особенности агента

- **LiteLLM**: единый клиент для Ollama, OpenAI, Mistral, Anthropic, Groq и др.
- **Режим мышления**: `reasoning_content` через `ENABLE_THINK=true`
- **Стриминг SSE**: события `token`, `tool_call`, `tool_result`, `final`, `error`
- **Память сессий**: `DEMO_HISTORY_TURNS` последних ходов (по умолчанию 8), ограничение `DEMO_HISTORY_CONTENT_CHARS` (6000)
- **Бэклог**: JSONL-файлы всех запросов/ответов/инструментов/токенов/таймингов в `./backlog/`

## Архитектура RAG

Пакет `rag/` не зависит от `db/` — циклическая зависимость разорвана.  
`DocumentRepository` принимает сырой `sqlite3.Connection` / `psycopg.Connection`.

RAG — отдельный HTTP-сервис (`rag/service.py` на FastAPI, порт 8082) с HTTP-клиентом (`rag/client.py`).

- `rag/interfaces.py` — протоколы `EmbeddingProtocol`, `VectorStoreProtocol`
- `rag/embeddings.py` → `SentenceTransformerEmbedding`
- `rag/vector_store.py` → `ChromaDBVectorStore`
- `rag/pipeline.py` — оркестрация парсинг → чанкинг → сохранение
- `rag/repository.py` — CRUD документов
- `rag/service.py` — эндпоинты: `/health`, `/search`, `/context`, `/documents/*`
- `rag/http_models.py` — Pydantic-модели HTTP-контракта

### Стандартизация API (OpenAPI/Swagger)

Все HTTP-сервисы (`rag`, `api`, `web`) на FastAPI со Swagger UI:
- RAG: `http://127.0.0.1:8082/docs`
- API: `http://127.0.0.1:8081/docs`
- Web: `http://127.0.0.1:8080/docs`

При изменении API обновляй Pydantic-модели в `rag/http_models.py` и `demo/api/http_models.py`.

## Документы и RAG

RAG-слой работает через SQLite/PostgreSQL + ChromaDB:

1. `agent-rag-ingest import <file>` — читает PDF / DOCX / TXT / MD / HTML
2. Текст разбивается на чанки (`RAG_CHUNKER_TYPE`: `semantic`, `recursive`, `sentence`)
3. Для каждого чанка считается embedding (`paraphrase-multilingual-MiniLM-L12-v2`)
4. Векторы → ChromaDB, метаданные → SQLite/PostgreSQL
5. `search_documents` — поиск ближайших фрагментов по cosine similarity
6. `get_rag_context` — готовый контекст с инструкцией для модели

```bash
uv run agent-rag-ingest import ./lectures/lec01.pdf -d "cs-101" -t "Лекция 1: Введение"
uv run agent-rag-ingest list
uv run agent-rag-ingest search "быстрая сортировка" -n 3
uv run agent-rag-ingest delete --document-id <id>
```

> `agent-rag-ingest` принудительно выставляет `RAG_LOCAL_FILES_ONLY=1` — embedding-модель должна быть в локальном кэше.

## Генерация материалов

```bash
uv run agent-rag-docgen generate -d <discipline-id>   # Материалы одной дисциплины
uv run agent-rag-docgen generate-all                   # Всех дисциплин
uv run agent-rag-ingest clear-generated                  # Удалить сгенерированное
```

Требует Ollama. Проверка: `curl ${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags`.

## Важные переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `DATABASE_URL` | (пусто → SQLite) | `postgresql://user:pass@host:port/dbname` |
| `DB_PATH` | `./university.db` | Путь к SQLite |
| `CHROMA_PATH` | `./chroma_db` | Папка ChromaDB |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | HF-id или локальный путь |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | URL RAG-сервиса |
| `MCP_SERVICE_URL` | `http://127.0.0.1:8083/mcp` | URL MCP-сервиса |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Адрес Ollama |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `MISTRAL_API_KEY` | — | Ключ Mistral API |
| `DEMO_REQUEST_TIMEOUT` | `600` | Таймаут turn агента (сек) |
| `BACKLOG_DIR` | `./backlog/` | Папка JSONL-бэклога |
| `ENABLE_THINK` | `true` | Режим мышления |

Полный список — в `.env.example`.

## Пример запроса к модели

```json
{
  "tool_name": "find_student_by_name",
  "parameters": {
    "name": "Иван Петров Иванович"
  }
}
```

Пример вопроса пользователя:
```
Какие материалы доступны студенту Ивану Петрову Ивановичу по его дисциплинам?
```

## Текущее состояние

Проект на стадии **pre-prod прототипа**. **Выполнены этапы 0–2 из ROADMAP**:

- **Этап 0** (0.0–0.5): Разделение на 4 независимых HTTP-сервиса, FastAPI, CLI-утилиты, HTTP-транспорт MCP
- **Этап 1**: Тестовая инфраструктура (84% покрытие, 109 тестов, ruff, OpenAPI/Swagger)
- **Этап 2**: Контейнеризация (5 Dockerfile'ов, docker-compose с 7 сервисами, Caddy, healthchecks)
- **Этап 2.x**: Тесты разнесены по сервисам (rag/tests/, mcp_server/tests/, demo/api/tests/, demo/web/tests/, agent-tutor-sdk/tests/),
  Dockerfile'ы копируют только нужные исходники, а не весь проект целиком
- **Этап 2.7 (выполнен)**: Data-service на Go — изоляция схемы БД. `mcp_server` и `demo/api` не содержат SQL университетских данных. `specs/schemas/` с JSON Schema, авто-генерация из Go-моделей.
- **Этап 2.7.x (выполнен)**: RAG отделён в `rag_documents.db`. Репозитории SDK удалены. `agent_tutor_sdk/db/` — только connector + schema + fixtures для тестов и CLI.
- **Сидинг через единый pipeline (выполнен)**: `agent-seedgen` (Python+faker) → `specs/fixtures/seed.json` → `data-service --seed` (Go) → SQLite/PostgreSQL. CLI `agent-rag-ingest` и `agent-rag-docgen` живут в `rag/fixtures/` и ходят к сервисам по HTTP-контракту. Старый Python-пакет `fixtures/` удалён.
- **Contract sync: Go → JSON Schema → Pydantic (выполнен)**: drift-тесты в `agent-tutor-sdk/tests/unit/test_contracts_drift.py` ловят расхождения между Go-моделями, JSON Schema и Pydantic. seedgen валидирует выход через `StorageSeed` из SDK (`agent_tutor_sdk/seed_models.py`).
- **Web-frontend сам ходит к data-service (выполнен)**: `demo/api` освобождён от data passthrough. `demo/web` делает reverse-proxy напрямую к `data-service:8084` (`/api/data/*`) и `rag:8082` (`/api/rag/documents`). Агент-эндпойнты (`/api/chat`, `/api/backlog`, `/api/session/history`) идут через `demo/api`.

Полный план — в `doc/ROADMAP.md`.

Работает:
- Data-service (Go, chi, modernc/sqlite) — единственный сервис со знанием схемы БД
- MCP-сервер (FastMCP, HTTP-транспорт, `/health` endpoint) — инструменты через HTTP к data-service
- RAG HTTP-сервис (FastAPI, своя `rag_documents.db` + ChromaDB)
- API-сервер с агентом (FastAPI, LiteLLM, SSE-стриминг, память сессий, бэклог)
- Веб-интерфейс (FastAPI, reverse-proxy, SSE-прокси)
- База: SQLite (по умолчанию) или PostgreSQL (через `DATABASE_URL`)
- `agent-tutor-sdk/` — абстракция БД, моделей и RAG-клиента
- `scripts/dev.sh` — нативный запуск всех сервисов
- Docker: 5 образов, docker-compose, Caddy, healthchecks
- 109 тестов, 84% покрытие, ruff чисто
- OpenAPI/Swagger у всех HTTP-сервисов
- Тесты разнесены по пакетам сервисов: `uv run pytest rag/tests/`, `uv run pytest mcp_server/tests/`
- Dockerfile'ы копируют в runtime только нужные исходники (минимальный размер образа)

## Конфиг data-service (новая архитектура)

Data-service больше не содержит захардкоженных Go-моделей и SQL. Он работает
по JSON-конфигу, который описывает сущности, эндпоинты и запросы.

### Генерация конфига из существующей БД

```bash
cd data-service
go build -o bin/data-service ./cmd/server/

# Автоматическая генерация из схемы
DB_PATH=../university.db ./bin/data-service --discover > ../my-config.json

# Запуск с конфигом
./bin/data-service --config ../my-config.json
```

### Формат конфига

```jsonc
{
  "version": 1,
  "data_source": {
    "driver": "sqlite",           // sqlite | postgres
    "dsn": "${DB_PATH:-university.db}"
  },
  "entities": [                    // таблицы → сущности
    { "name": "student", "table": "students", "id_column": "id", "fields": [...] }
  ],
  "endpoints": [                   // какие эндпоинты публикуем
    { "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }
  ],
  "custom_queries": {              // SELECT с JOIN'ами (пишутся руками)
    "student_grades": { "sql": "SELECT ...", "params": [...], "max_rows": 500 }
  }
}
```

Детали — в `specs/config.schema.json` и `data-service/README.md`.

### Структура specs/

```
specs/
├── config.schema.json          # JSON Schema для конфига data-service
├── config.example.json         # автогенерационный пример для university.db
├── rag.openapi.yaml            # OpenAPI-контракт RAG
├── api.openapi.yaml            # OpenAPI-контракт API
└── fixtures/                   # pipeline сидинга
```

## Осторожность

- Не удаляй `university.db`, `chroma_db/`, `generated_materials/` без явной необходимости.
- **Не удаляй `./backlog/`** — там истории чатов и трассировки взаимодействий с моделью.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай без просьбы.
- Не коммить изменения без прямой просьбы пользователя.
