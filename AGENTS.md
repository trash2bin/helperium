# AGENTS.md

Управление проектом для разработчиков и AI-агентов.

## Проект

- Полноценный агент с 5 long-running сервисами: `data-service` (Go, :8084), `mcp-gateway` (Go, :8083), `rag` (Python, :8082), `api` (Python, :8081), `web` (Python, :8080), и CLI-утилитами `agent-rag-ingest`, `agent-rag-docgen`, `agent-seedgen` как one-shot командами.
- Управление зависимостями и запуском — через `uv` + `pyproject.toml` (Python) и `go.work` (Go: 3 модуля).
- Все сервисы запускаются независимо и общаются друг с другом по HTTP.
- Университетские данные — ТОЛЬКО через `data-service` (Go, config-driven, без доменного хардкода). Никакой Python-код не содержит SQL университетской схемы.
- База данных: **SQLite** (по умолчанию) или **PostgreSQL** (через `DATABASE_URL`). Абстракция в `data-service/internal/datasource/` (Go-адаптеры: sqlite + postgres, equivalence-тесты).
- **Архитектурная гибкость**: каждый сервис — self-contained единица с собственным HTTP-контрактом.
  Сервис можно перепис��ть на другом языке, не трогая соседей — достаточно реализовать тот же
  HTTP-контракт (OpenAPI-спецификация). data-service генерирует OpenAPI runtime из конфига.
  CLI-утилиты (`rag/fixtures/`) — это dev-инструментарий, не production-сервисы.

## Базовые команды

```bash
# Python-сервисы
uv sync                                 # установка всех зависимостей
uv run --package rag python -m rag.service              # RAG HTTP-сервис (порт 8082)
uv run --package demo-api python -m demo.api.server     # API сервер с агентом (порт 8081)
uv run --package demo-web python -m demo.web.server     # Веб-сервер (порт 8080)

# CLI-утилиты
uv run agent-rag-ingest --help      # CLI для работы с RAG-документами (импорт PDF/DOCX)
uv run agent-rag-docgen --help      # CLI для генерации учебных материалов через Ollama
uv run agent-seedgen --help         # CLI для генерации seed.json (фейковые данные вуза)
uv run agent-rag --help             # RAG-сервис как CLI (альтернатива python -m rag.service)

# Тесты
uv run pytest rag/tests/            # Python-тесты RAG
uv run pytest demo/api/tests/       # Python-тесты API
uv run pytest demo/web/tests/       # Python-тесты WEB
uv run pytest agent-tutor-sdk/tests/ # Python-тесты SDK
```

### Go-сервисы (3 модуля в `go.work`)

```bash
# Общий модуль (переиспользуется data-service и mcp-gateway)
# agent-tutor-go/config/ — типы конфига, loader, валидация, envsubst

# Data-service (config-driven HTTP к произвольной БД, :8084)
cd data-service
go build -o bin/data-service ./cmd/server/       # сборка
./bin/data-service --config specs/config.example.json  # запуск с конфигом
./bin/data-service --discover > specs/my-config.json   # генерация конфига из схемы БД
go test ./internal/...                           # unit-тесты + integration

# Сидинг (dev-only, отдельный бинарник)
go run ./cmd/seed-cli/ --seed-path ../specs/fixtures/seed.json

# MCP-gateway (MCP-сервер на Go, :8083)
cd mcp-gateway
go build -o mcp-gateway ./cmd/                   # сборка
./mcp-gateway --config ../specs/config.example.json  # запуск
go test ./...                                    # тесты
```

После изме��ений в логике API или RAG — запускай тесты для проверки регрессий.

## 📖 README по сервисам

Каждый сервис имеет свой `README.md` с детальным описанием архитектуры, конфигурации и примеров:

| Файл | Что описывает |
|---|---|
| `data-service/README.md` | Config-driven архитектура, `--discover` (генерация конфига из БД), адаптеры SQLite/PG, сценарии БД (фабрика тестовых БД), тестирование, `--materialize` |
| `mcp-gateway/README.md` | Go MCP-сервер на mark3labs/mcp-go v0.8.3, авто-генерация инструментов из конфига, RAG-тулы, SSE + JSON-RPC транспорт |
| `agent-tutor-sdk/README.md` | Python SDK: generic Entity, AsyncDataServiceClient, RagClient, seed_models |
| `demo/api/agent/AGENT_WORKFLOW.md` | **Подробнейшая** документация агента: оркестратор, LLM-клиент, MCP-клиент, парсер tool calls, conversation manager, типы SSE-событий, flow вызовов |
| `rag/README.md` | RAG-сервис: пайплайн (парсинг → чанкинг → embeddings → ChromaDB), конфигурация, HTTP-контракт |
| `specs/fixtures/README.md` | CLI-утилиты: `agent-rag-ingest`, `agent-rag-docgen`, `agent-seedgen` — полная документация по командам |
| `doc/NEW_ROADMAP.md` | **План развития**: от MVP к B2B SaaS платформе, карта хардкода, целевая архитектура, выполненные и pending фазы |
| `.env.example` | **Все переменные окружения** (180 строк): БД, LLM, RAG, MCP, API, Web, генерация, бекапы — с комментариями и дефолтами |

## Способы запуска

| Способ | Платформа | Команда |
|---|---|---|
| **Нативный** (через `uv` + Go-бинарники) | Mac (Apple Silicon), Linux | `./scripts/dev.sh start` |
| **Docker** | Linux / предпрод / прод | `docker compose up -d` |
| **Docker + HTTPS** | Прод | `docker compose --profile prod up -d` |

Код сервисов один и тот же, разница только в оркестрации.

### 🖥️ Нативный запуск (Mac)

**Скрипт**: `scripts/dev.sh` — главная точка входа для нативного запуска.

```bash
# Основные команды
./scripts/dev.sh start         # Поднять все 5 сервисов (data→rag→mcp→api→web)
./scripts/dev.sh status        # Healthcheck всех сервисов
./scripts/dev.sh logs api      # tail -f .data/logs/api.log
./scripts/dev.sh logs all      # tail -f логов всех сервисов сразу
./scripts/dev.sh stop          # Остановить всё
./scripts/dev.sh restart       # stop + start
```

**Флаги и переменные, влияющие на запуск**:
- `DATABASE_URL` — если задан, `dev.sh` автоматически переключает data-service на PostgreSQL (флаг `--config specs/config.postgres.json`)
- `DS_CONFIG` — переопределяет путь к конфигу data-service (по умолчанию `specs/config.example.json` для SQLite)
- `MCP_PORT`, `RAG_PORT`, `API_PORT`, `WEB_PORT`, `DATA_PORT` — переопределение портов (из `.env`)
- Dev-режим (по умолчанию): встроен MCP Playground на `http://127.0.0.1:8083/debug`
- Все пути к логам: `.data/logs/{data,rag,mcp,api,web}.log`, PID-файлы: `.data/pids/*.pid`

**Сценарии БД (фабрика тестовых БД для data-service)** — под-команда `db`:

```bash
./scripts/dev.sh db list                       # список сценариев + метаданные
./scripts/dev.sh db materialize <name> [--force]  # создать/пересоздать БД из сценария
./scripts/dev.sh db serve <name>               # только data-service на сценарии (foreground)
./scripts/dev.sh db test [all|<name>]          # прогнать Go-тесты на сценарии
./scripts/dev.sh db drop <name>                # удалить материализованную БД
./scripts/dev.sh db help                       # подробная справка
```

**Что это даёт**:
- Готовая фабрика БД из `config.json` + `seed.json` (cross-driver: SQLite и PG).
- 4 встроенных сценария: `sqlite-testseed`, `postgres-testseed`, `shop` (сторонняя БД), `big-testseed` (4680 entities — для load-tests).
- Тесты на каждом сценарии: `big_test.go` (нагрузка), `edge_cases_test.go` (malformed inputs), `concurrency_test.go` (heavy load), `custom_queries_test.go` (FK-lookups), `fuzz_test.go` (fuzzing), `benchmark_test.go` (8 бенчмарков).
- Подробнее: `data-service/README.md` § "Сценарии — фабрика тестовых БД" + § "Тестирование".

Логи: `.data/logs/{data,rag,mcp,api,web}.log`. PID-файлы: `.data/pids/*.pid`. `.env` грузится автоматически.

**Порядок ожидания**: `data → rag → mcp → api → web` (каждый ждёт `/health` предыдущего, таймаут 60с).

### 🐳 Docker-запуск (Linux / предпрод / прод)

**Файлы контейнеризации**:

| Файл | Назначение |
|---|---|
| `data-service/Dockerfile` | Образ data-service (Go) |
| `mcp-gateway/Dockerfile` | Образ MCP-сервера (Go) |
| `rag/Dockerfile` | Образ RAG-сервиса |
| `demo/api/Dockerfile` | Образ API-сервера |
| `demo/web/Dockerfile` | Образ WEB-сервера |

| `docker-compose.yml` | Оркестрация: 7 сервисов, healthchecks, volumes |
| `Caddyfile` | HTTPS-прокси через Caddy (профиль prod) |
| `.env.example` | Полный список переменных с дефолтами |
| `.dockerignore` | Исключения для Docker build |

```bash
# Dev-режим: 5 long-running сервисов (data, mcp, rag, api, web)
docker compose up -d

# Prod-режим: + Caddy (HTTPS через Let's Encrypt)
docker compose --profile prod up -d

# CLI-утилиты (через uv, не Docker)
uv run agent-rag-ingest list
uv run agent-rag-docgen generate -d "cs-101"

# Сборка образов
docker compose build
```

**Healthchecks**: `data-service` (start_period=5s), `rag` (start_period=120s — cold start embedding), `mcp` → ждёт `rag` + `data`, `api` → ждёт `mcp`, `web` → ждёт `api`.

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
seed-cli  (Go, отдельный бинарник, cmd/seed-cli/)
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

**Шаг 2 — залить в БД** (отдельная утилита `cmd/seed-cli/`, не часть data-service сервера):

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
# Терминал 1: Data-service (Go)
cd data-service && ./bin/data-service --config ../specs/config.example.json

# Терминал 2: MCP-gateway (Go)
cd mcp-gateway && ./mcp-gateway --config ../specs/config.example.json

# Терминал 3: RAG (Python)
RAG_PORT=8082 uv run python -m rag.service

# Терминал 4: API (Python)
MCP_SERVICE_URL=http://127.0.0.1:8083/mcp DEMO_API_PORT=8081 uv run python -m demo.api.server

# Терминал 5: WEB (Python)
DEMO_API_HOST=127.0.0.1 DEMO_API_PORT=8081 DEMO_WEB_PORT=8080 uv run python -m demo.web.server
```

## Подробная структура проекта

```
agent-tutor/
├── agent-tutor-go/             # Общая Go-библиотека (переиспользуется data-service + mcp-gateway)
│   └── config/                 # типы конфига, loader, валидация, envsubst
│       ├── types.go            # Config, Entity, Endpoint, CustomQuery, ...
│       ├── loader.go           # Load(path) → *Config + JSON Schema validation
│       ├── validate.go         # семантическая валидация конфига
│       ├── envsubst.go         # подстановка ${ENV_VAR} в строках
│       └── errors.go           # типы ошибок
├── data-service/               # Config-driven Go-сервис доступа к произвольной БД (:8084)
│   ├── cmd/
│   │   ├── server/main.go      # точка входа: graceful shutdown, --config, --discover, --materialize
│   │   └── seed-cli/main.go    # dev-only: заливка seed-данных (не часть прод-сервера)
│   ├── internal/
│   │   ├── datasource/         # адаптеры БД: sqlite, postgres
│   │   │   ├── adapter.go      # Adapter interface {Connect, Introspect, ...}
│   │   │   ├── sqlite_adapter.go / postgres_adapter.go
│   │   │   ├── registry.go     # реестр драйверов
│   │   │   └── equivalence_test.go # кросс-СУБД контрактные тесты
│   │   ├── runtime/            # generic query builder + хендлеры
│   │   │   ├── query_builder.go     # BuildGetByID, BuildFind, BuildList
│   │   │   ├── entity_resolver.go   # Resolve(entityName) → Entity
│   │   │   ├── response_mapper.go   # MapRow, MapRows
│   │   │   ├── converter.go         # Config → runtime types
│   │   │   └── handlers/       # generic HTTP-хендлеры
│   │   │       ├── get_by_id.go / find.go / list.go
│   │   │       ├── custom_query.go / health.go / stats.go
│   │   │       ├── context.go / default.go / mcp_manifest.go
│   │   ├── configgen/          # генерация конфига из интроспекции БД
│   │   │   └── configgen.go    # Generate(schema, ds) → *Config
│   │   ├── openapigen/         # runtime-генерация OpenAPI
│   │   │   └── openapigen.go   # Generate(cfg, host, title, version) → spec
│   │   ├── server/             # chi-роутер, middleware, Swagger UI
│   │   │   ├── server.go       # middleware (Recovery, RequestID, StructuredLogging)
│   │   │   ├── endpoint_builder.go # NewRouterFromConfig(cfg, db, adapter, ...)
│   │   │   ├── swagger.go      # runtime /openapi.json + Swagger UI
│   │   │   └── server_test.go / router_config_test.go # config-driven e2e
│   │   └── seedgen/            # Go-сидер (dev-only, вызывается из cmd/seed-cli)
│   │       ├── seedgen.go      # Load/Apply, FK-порядок, ErrDatabaseNotEmpty
│   │       └── testdata.go     # TestSeed для in-memory тестов
│   ├── tests/integration/      # test_with_faker.py
│   ├── Dockerfile
│   └── README.md               # config-driven архитектура, --discover, примеры
├── mcp-gateway/                # MCP-сервер (Go, mark3labs/mcp-go v0.8.3, HTTP :8083)
│   ├── cmd/main.go             # точка входа: SSE + JSON-RPC + debug
│   ├── internal/
│   │   ├── httpclient/         # HTTP-клиент к data-service
│   │   ├── ragclient/          # HTTP-клиент к RAG (search/list/context)
│   │   └── tools/              # авто-генерация data-тулов из конфига + статические RAG-тулы
│   ├── Dockerfile
│   ├── go.mod / go.sum
│   └── README.md               # config-driven MCP, auto-generated tools, RAG
├── rag/                        # RAG HTTP-сервис (FastAPI, :8082)
│   ├── service.py              # FastAPI app: /health /search /context /documents/*, ServiceState singleton
│   ├── db.py                   # RagDB singleton над sqlite3 (WAL, check_same_thread=False)
│   ├── documents_schema.py     # DDL для documents + document_chunks (изолирован от доменной БД)
│   ├── repository.py           # DocumentRepository CRUD + транзакции с откатом
│   ├── pipeline.py             # RAGPipeline: import_document / search_documents / build_rag_context
│   ├── parser.py               # DocumentParser: txt/md напрямую, PDF/DOCX через docling
│   ├── chunker.py              # TextChunker + Semantic/Recursive/Sentence стратегии
│   ├── embeddings.py           # SentenceTransformerEmbedding (ленивая загрузка, batch)
│   ├── vector_store.py         # ChromaDBVectorStore (cosine, PersistentClient)
│   ├── config.py               # RagConfig + from_env() (RAG_*, CHROMA_*)
│   ├── interfaces.py           # Protocol'ы EmbeddingProtocol, VectorStoreProtocol
│   ├── _types.py               # внутренние TypedDict (PageDict, ChunkDict, ...)
│   ├── http_models.py          # Pydantic-DTO HTTP-контракта (SearchRequest, ListDocumentsResponse, ...)
│   ├── pyproject.toml          # манифест + entrypoints (agent-rag, agent-rag-docgen, agent-rag-ingest, agent-seedgen)
│   ├── fixtures/               # CLI-утилиты (dev-инструментарий)
│   │   ├── cli_ingest.py       # CLI обёртка над RagClientSync к RAG-сервису
│   │   ├── cli_docgen.py       # CLI генерации материалов через Ollama
│   │   ├── document_generator.py # MaterialDocumentGenerator: Ollama → DOCX/MD → RAG индекс
│   │   ├── seedgen.py          # генератор seed.json с валидацией через StorageSeed
│   │   ├── catalog.py          # статический каталог фиктивных данных
│   │   └── _material.py        # dev-only Pydantic-модель Material
│   └── tests/
│       ├── unit/               # pipeline / embeddings / repository / vector_store / service / openapi_spec
│       └── integration/        # test_e2e_pipeline (real SQLite + ChromaDB)
├── agent-tutor-sdk/            # Shared Python SDK
│   ├── pyproject.toml          # манифест (hatchling, pydantic>=2, httpx>=0.28)
│   ├── src/agent_tutor_sdk/
│   │   ├── api/
│   │   │   └── models.py       # Generic Entity + Pydantic-модели для data-service ответов
│   │   ├── models.py           # корневые модели SDK
│   │   ├── data_client.py      # AsyncDataServiceClient + DataServiceClientSync к data-service:8084
│   │   ├── rag/                # RagClient (async) + RagClientSync + HTTP-модели RAG
│   │   │   ├── client.py
│   │   │   └── models.py
│   │   └── seed_models.py      # Storage-форма Pydantic (name, group_id) для seed.json — extra="forbid"
│   └── tests/unit/             # test_client.py + test_entity_model.py + test_seedgen_validation.py
├── demo/
│   ├── api/                    # API-сервер + агент (FastAPI, :8081)
│   │   ├── server.py           # FastAPI app: /health, /api/chat (SSE), /api/backlog*, /api/session/history
│   │   ├── http_models.py      # ChatRequest, HealthResponse, Backlog*, SessionHistory*
│   │   ├── backlog.py          # ModelBacklog: трассировка всех взаимодействий с LLM (jsonl per session)
│   │   ├── sessions.py         # SessionStore: история чатов в SQLite + миграция из agent_memory.json
│   │   ├── agent/              # подмодуль агента
│   │   │   ├── orchestrator.py # LLMAgent + SYSTEM_PROMPT: главный цикл итераций
│   │   │   ├── llm_client.py   # LLMClient поверх LiteLLM (Mistral/Ollama), stream_completion
│   │   │   ├── mcp_client.py   # MCPClient: долгоживущая сессия к MCP через streamable_http_client
│   │   │   ├── tool_parser.py  # ToolCallParser: нативные + JSON-блоки tool-вызовы
│   │   │   ├── conversation.py # ConversationManager (sync/async) с per-session asyncio.Lock
│   │   │   └── types.py        # TypedDict: Message, ToolCall, *EventData, SessionId/TurnId
│   │   ├── pyproject.toml      # манифест demo-api
│   │   └── tests/unit/         # agent/* + backlog + sessions + openapi_api drift
│   └── web/                    # Веб-интерфейс (FastAPI, :8080)
│       ├── server.py           # reverse-proxy к api / data-service / rag + статика
│       ├── pyproject.toml      # манифест demo-web
│       ├── Dockerfile          # multi-stage python:3.12-slim, non-root app user
│       ├── static/             # vanilla JS фронт
│       │   ├── app.js          # таблицы вкладок + чат с SSE + localStorage (⚠️ доменный)
│       │   ├── index.html
│       │   └── styles.css
│       └── tests/unit/         # test_proxy.py: тесты reverse-proxy через respx
├── specs/                      # Контракты (source of truth для API)
│   ├── config.schema.json      # JSON Schema для конфига data-service
│   ├── config.schema.md        # Документация схемы конфига
│   ├── config.example.json     # пример конфига для university.db (SQLite)
│   ├── config.postgres.json    # пример конфига для PostgreSQL
│   ├── rag.openapi.yaml        # OpenAPI-контракт RAG
│   ├── api.openapi.yaml        # OpenAPI-контракт API
│   ├── README.md
│   └── fixtures/               # pipeline сидинга: agent-seedgen → seed.json → seed-cli
│       ├── README.md           # инструкция по сидингу БД
│       └── seed.json           # детерминированный seed (--seed 42), gitignored
├── scripts/
│   └── dev.sh                  # нативный запуск всех 5 сервисов + подкоманда db
├── doc/
│   └── NEW_ROADMAP.md          # План развития: от MVP к B2B SaaS платформе
├── docker-compose.yml          # 7 сервисов (5 long-running + db + caddy)
├── go.work                     # Go workspace: agent-tutor-go, data-service, mcp-gateway
└── .env.example                # Все переменные окружения
```

## Инструменты MCP-сервера

MCP-инструменты генерируются **автоматически** из конфига data-service (runtime `/mcp/manifest`)
+ статические RAG-инструменты в `mcp-gateway/internal/tools/`. Никакого ручного описания тулов.

Для демо-БД университета генерируются следующие инструменты:

| Инструмент | Тип | Что делает |
|---|---|---|
| `get_student(id)` | auto-gen | Карточка студента |
| `find_student_by_name(name)` | auto-gen | Поиск студента по ФИО |
| `get_schedule(group_id, day?)` | auto-gen | Расписание группы |
| `get_disciplines(id)` | auto-gen | Дисциплины студента |
| `get_student_grades(id, discipline_id?)` | auto-gen | Оценки студента |
| `get_teacher_by_name(name)` | auto-gen | Поиск преподавателя |
| `get_teacher_schedule(name, day?)` | auto-gen | Расписание преподавателя |
| `search_documents(query, discipline_id?, limit?)` | static (RAG) | Поиск релевантных фрагментов документов |
| `list_documents(discipline_id?)` | static (RAG) | Список документов в RAG-индексе |
| `get_rag_context(query, discipline_id?, limit?)` | static (RAG) | Готовый контекст для ответа по документам |

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
   через `go run ./cmd/seed-cli/` (Go, `internal/seedgen/`).

**Важно**: `rag/fixtures/` — не микросервис и не часть production-архитектуры.
Это утилиты для разработки и тестирования, которые работают с сервисами через HTTP
(как и любой внешний клиент). Если завтра RAG-сервис переписан на Go — `agent-rag-ingest`
продолжает работать, потому что ходит по HTTP, а не через Python-импорты.

Полная документация по командам — в `specs/fixtures/README.md`.

## Архитектурная гибкость

Приложение спроектировано так, чтобы адаптироваться к разным бэкендам и сценариям
без переписыван��я ядра:

### Смена базы данных
- Университетские данные — через `data-service` (Go). Сервис config-driven: схема БД описывается в конфиге, SQL генерируется runtime query builder'ом.
- При смене реальной БД вуза достаточно написать конфиг (или запустить `--discover`). Никакой Go-код не переписывается.
- Python-код вообще не содержит SQL университетских данных — всё через HTTP к data-service.
- Сессии чата (`demo_sessions.sqlite`) — на SQLite, это кэш, не основные данные.

### Смена LLM-провайдера
- LiteLLM — единый клиент под Ollama, OpenAI, Mistral, Anthropic, Groq и др.
- Меняется только `OLLAMA_URL` / `MISTRAL_API_KEY` / `OPENAI_API_KEY`

### Замена любого сервиса
- Каждый long-running сервис имеет HTTP-контракт (OpenAPI/Swagger на `/docs`)
- `data-service` (Go) — контракт генерируется runtime из конфига (`/openapi.json`)
- Если переписать `rag` на Go — `mcp-gateway` продолжает ходить к `http://rag:8082`
- Если переписать `mcp-gateway` на Rust — `api` продолжает слать MCP-over-HTTP
- Если переписать `data-service` на Rust — все потребители видят тот же HTTP-контракт
- CLI-утилиты (`rag/fixtures/`) не привязаны к Python-коду сервисов — работают по HTTP

### Две независимые БД в проекте

В проекте **сознательно** живут две разные БД, потому что они отвечают за разные вещи:

| БД | Владелец | Что хранит | Наполняется через |
|---|---|---|---|
| `university.db` (или PostgreSQL) | `data-service` (Go) | Доменная модель вуза: студенты, группы, преподаватели, дисциплины, расписание, оценки | `agent-seedgen` (Python+faker) → `specs/fixtures/seed.json` → `go run ./cmd/seed-cli/` |
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
**Веб-сервер** (`demo/web/server.py`) — отдаёт статические файлы интерфейса и проксирует запросы к API, data-service и RAG напрямую (reverse-proxy + SSE-прокси).

Ядро агента — `demo/api/agent/`:
- `orchestrator.py` — оркестратор: вызов моделей, подключение к MCP, рекурсивные вызовы, стриминг
- `llm_client.py` — клиент для работы с LLM через LiteLLM
- `mcp_client.py` — HTTP-клиент для MCP-сервера (долгоживущая сессия)
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

RAG — отдельный HTTP-сервис (`rag/service.py` на FastAPI, порт 8082). Не зависит от университетской БД.

- `rag/interfaces.py` — протоколы `EmbeddingProtocol`, `VectorStoreProtocol`
- `rag/embeddings.py` → `SentenceTransformerEmbedding`
- `rag/vector_store.py` → `ChromaDBVectorStore`
- `rag/pipeline.py` — оркестрация парсинг → чанкинг → сохранение
- `rag/repository.py` — CRUD документов
- `rag/service.py` — эндпоинты: `/health`, `/search`, `/context`, `/documents/*`
- `rag/http_models.py` — Pydantic-модели HTTP-контракта

HTTP-клиент для RAG — в SDK: `agent_tutor_sdk/rag/client.py` (`RagClient` async + `RagClientSync`).

### Стандартизация API (OpenAPI/Swagger)

Все HTTP-сервисы (`data-service`, `rag`, `api`, `web`) со Swagger UI:
- Data-service: `http://127.0.0.1:8084/docs` (runtime-generated из конфига)
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

> **Полный список (180 строк) с комментариями и дефолтами — в [`.env.example`](.env.example).**
> Ключевые секции: Database, LLM Provider, RAG Service, MCP Server, API Server, Security, Document Generation, Backups.

Ниже — самые важные переменные (краткий справочник):

| Переменная | Дефолт | Описание |
|---|---|---|
| `DATABASE_URL` | (пусто → SQLite) | `postgresql://user:pass@host:port/dbname` |
| `DB_PATH` | `./university.db` | Путь к SQLite |
| `DS_CONFIG` | `specs/config.example.json` | Конфиг data-service (авто: SQLite example, PG через `config.postgres.json`) |
| `CHROMA_PATH` | `./chroma_db` | Папка ChromaDB |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | HF-id или локальный путь |
| `RAG_CHUNKER_TYPE` | `semantic` | Стратегия чанкинга: `semantic`, `recursive`, `sentence` |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | URL RAG-сервиса |
| `MCP_SERVICE_URL` | `http://127.0.0.1:8083/mcp` | URL MCP-сервера |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | URL data-service |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Адрес Ollama |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `MISTRAL_API_KEY` | — | Ключ Mistral API |
| `DEMO_REQUEST_TIMEOUT` | `600` | Таймаут turn агента (сек) |
| `AGENT_MAX_ITERATIONS` | `5` | Максимум tool-call итераций за turn |
| `AGENT_MAX_TURN_TOKENS` | `8000` | Лимит токенов на turn (эвристика chars/3.5) |
| `DEMO_HISTORY_TURNS` | `8` | Сколько ходов диалога хранить в памяти сессии |
| `BACKLOG_DIR` | `./backlog/` | Папка JSONL-бэклога |
| `ENABLE_THINK` | `true` | Режим мышления (reasoning_content) |
| `API_BEARER_TOKEN` | (пусто) | Bearer-токен для продакшена |

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

Проект на стадии **pre-prod прототипа**, движется от MVP к **B2B SaaS платформе** с автогенерацией API по базе клиента.

### Выполненные этапы

- **Этап 0** (0.0–0.5): Разделение на независимые HTTP-сервисы, FastAPI, CLI-утилиты, HTTP-транспорт MCP
- **Этап 1**: Тестовая инфраструктура (ruff, OpenAPI/Swagger, drift-тесты)
- **Этап 2**: Контейнеризация (5 Dockerfile'ов, docker-compose с 7 сервисами, Caddy, healthchecks)
- **Этап 2.7**: Data-service на Go — изоляция схемы БД. `mcp-gateway` и `demo/api` не содержат SQL университетских данных.
- **Фаза 3.0**: Config schema + datasource adapter interface
- **Фаза 3.1 (a-d)**: Адаптеры SQLite + PostgreSQL, кросс-драйвер equivalence-тесты, Registry
- **Фаза 3.2 (a-d)**: Config loader (envsubst, JSON Schema validation), generic query builder, endpoint builder, handlers, e2e тесты
- **Фаза 3.2–3.4**: Config-driven data-service, mcp-gateway на Go, авто-генерация MCP-тулов из конфига
- **Фаза 3.5**: Generic SDK — удаление `contracts/`, generic `Entity` в `api/models.py`, удаление 16 доменных методов из `data_client.py`
- **Удаление `mcp_server/` (Python)**: заменён на `mcp-gateway/` (Go, mark3labs/mcp-go v0.8.3)
- **Contract sync**: drift-тесты в `agent-tutor-sdk/tests/unit/test_entity_model.py`, seedgen валидирует через `StorageSeed`
- **Web-frontend сам ходит к data-service**: `demo/web` делает reverse-proxy напрямую к `data-service:8084` (`/api/data/*`) и `rag:8082` (`/api/rag/documents`)
- **Сценарии БД**: 4 встроенных (`sqlite-testseed`, `postgres-testseed`, `shop`, `big-testseed`), управление через `./scripts/dev.sh db`
- **Масштабные Go-тесты**: 178+ тестов в data-service (`big_test.go`, `edge_cases_test.go`, `concurrency_test.go`, `custom_queries_test.go`, `fuzz_test.go`, `benchmark_test.go`)

### Что осталось хардкодным (домен вуза)

| Источник хардкода | Статус | План |
|---|---|---|
| `demo/web/static/app.js` | ❌ domain-specific таблицы (students/teachers/grades) | Фаза 3.6 |
| `demo/web/server.py` | ❌ доменные reverse-proxy роуты `/api/data/...` | Фаза 3.6 |
| `rag/fixtures/seedgen.py` | ❌ генерирует студентов/группы/оценки (домен вуза) | Фаза 3.8 |
| `rag/fixtures/document_generator.py` | ❌ доменные промпты для генерации лекций | Фаза 3.8 |

### Что уже config-driven (generic)

| Слой | Состояние |
|---|---|
| `data-service` SQL | generic query builder ✅ |
| `data-service` модели | generic `Entity` через конфиг ✅ |
| `data-service` endpoints | собираются runtime из `config.endpoints[]` ✅ |
| `data-service` адаптеры | sqlite + postgres ✅ |
| MCP tools | auto-gen из конфига + runtime `/mcp/manifest` ✅ |
| SDK контракты | generic `Entity` + `api/models.py` ✅ |
| Общий config-пакет | `agent-tutor-go/config/` через `go.work` ✅ |

Полный план — в `doc/NEW_ROADMAP.md`.

## Известные проблемы и особенности

### 1. Custom_query endpoints без `params` в конфиге

**Симптом**: `arg count mismatch: query expects 1 params, got 0` при вызове
`/students/{id}/grades`, `/students/{id}/disciplines`, `/groups/{id}/schedule`.

**Причина**: в конфигах `config.example.json` / `config.postgres.json` у этих
эндпоинтов отсутствовало поле `params`. `CustomQueryHandler` обходит
`ep.Params` чтобы вытащить значение `{id}` из URL-пути — без `params`
аргументы не биндятся.

**Исправлено** (2024-06-29): добавлены `params: [{name: "id", in: "path", type: "string", required: true}]`
ко всем трём эндпоинтам. `teacher_schedule` не требует params — его SQL без плейсхолдеров.

### 2. Недетерминированный seed между SQLite и PostgreSQL

Каждый вызов `agent-seedgen` (и `seed-cli`) генерирует уникальный набор данных.
Чтобы получить одинаковых студентов на SQLite и PostgreSQL, фиксируй `--seed`:

```bash
uv run agent-seedgen --seed 42 --students 40 --grades 60
```

Переменной окружения для seed нет — передаётся только как аргумент CLI.

### 3. Модель не всегда заполняет аргументы tool calls

Некоторые модели (особенно маленькие, типа `minimax-m3:cloud` или `qwen2.5:0.5b`)
иногда вызывают инструменты с пустыми аргументами: `find_student_by_name({})`.
Наш код не может это контролировать — это зависит от модели и формулировки
system prompt. Рекомендуется использовать модели, которые были протестированы
на function calling (Mistral Large, GPT-4, Claude, Llama 3.1 70B+).

### 4. MAX_TURN_TOKENS — защита от выхода за context window

Каждый turn агента ограничен `AGENT_MAX_TURN_TOKENS` (по умолчанию 8000).
Оценка токенов — эвристика `chars/3.5`. При превышении:
- Накопленный ответ обрезается до `system prompt + последние 2 обмена`
- Агент делает финальную попытку ответить без истории

Изменить лимит: `AGENT_MAX_TURN_TOKENS=16000` в `.env`.

### 5. data-service использует старый процесс при перезапуске

При `./scripts/dev.sh restart` data-service может не перестартовать если порт занят
старым процессом. Форсированный перезапуск:
```bash
lsof -ti :8084 | xargs kill -9
DS_CONFIG=specs/config.postgres.json \
  nohup data-service/bin/data-service --config specs/config.postgres.json \
  > .data/logs/data.log 2>&1 &
echo $! > .data/pids/data-service.pid
```

### 6. SQLite JSON-функции не работают в PostgreSQL

SQL `json_extract(...)`, `json_each(...)` — только SQLite.
Для PostgreSQL нужно переписывать на `jsonb_array_elements(...::jsonb)` и `->>`.
Проверяй оба конфига при изменении `custom_queries`.

Проявляется в `group_schedule` в сценариях testseed: SQLite отдаёт
`lessons_json` (JSON-строка), PG — раскрытый массив `lessons`. Endpoints
возвращают разный формат в зависимости от драйвера. Покрыто тестом
`TestScenario_BigTestseed_OpenAPISpecValid` (SQLite) и кросс-driver проверк��ми
в `data-service/internal/server/` (помечены как `Skip` без PG). TODO для
унификации формата.

### 7. In-memory SQLite плохо для concurrency

При тестах на сценариях используется `?mode=:memory:` — это даёт
SQLITE_BUSY при 100+ параллельных запросах (до 89% 5xx). Задокументировано
в `internal/server/edge_cases_test.go` (`TestEdgeCases_DuplicateInsertions/100_concurrent_same_no_panic`).

**Для production-нагрузки используй**:
- `data-service` с file-based SQLite c WAL (`db_path=data.db` в конфиге),
  как в `testdata/scenarios/sqlite-testseed/data.db`
- или PostgreSQL через `DATABASE_URL` (docker compose up -d db)

Production-тесты на нагрузку: см. `internal/server/concurrency_test.go`
(`TestConcurrency_FileBased_HeavyLoad` — 500 reqs/20 goroutines на file-based
SQLite �� WAL, проверяет ≤10% 5xx).

## Конфиг data-service (новая архитектура)

Data-service больше не содержит захардкоженных Go-моделей и SQL. Он работает
по JSON-конфигу, который описывает сущности, эндпоинты и запросы.

### Генерация конфига из существующей БД

```bash
cd data-service
go build -o bin/data-service ./cmd/server/

# Автоматическая генерация из схемы
DB_PATH=../university.db ./bin/data-service --discover > ../specs/my-config.json

# Запуск с конфигом
./bin/data-service --config ../specs/my-config.json
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
├── config.schema.md            # Документация схемы конфига
├── config.example.json         # пример конфига для university.db (SQLite)
├── config.postgres.json        # пример конфига для PostgreSQL
├── rag.openapi.yaml            # OpenAPI-контракт RAG
├── api.openapi.yaml            # OpenAPI-контракт API
└── fixtures/                   # pipeline сидинга
    ├── README.md
    └── seed.json               # gitignored
```

## Knowledge Graph (Graphify)

Проект использует **graphify** — инструмент построения knowledge graph из исходного кода.
Граф уже построен и лежит в `graphify-out/`: **2430 узлов, 4234 связи, 170 сообществ**.
Он покрывает Go (`data-service`, `mcp-gateway`, `agent-tutor-go`) и Python (`rag`, `demo/api`, `demo/web`, `agent-tutor-sdk`) одновременно.

### Основные инструменты и когда их применять

| Инструмент | Когда использовать | Пример |
|---|---|---|
| **`graphify_query`** (bfs) | Вопрос «что с чем связано в архитектуре?» | `graphify_query({ question: "Как LLMAgent вызывает MCP-инструменты?", mode: "bfs" })` |
| **`graphify_query`** (dfs) | «Как данные текут от A к B?» | `graphify_query({ question: "Как запрос пользователя доходит до ChromaDB?", mode: "dfs" })` |
| **`graphify_path`** | Кратчайший путь между двумя концептами | `graphify_path({ from: "LLMAgent", to: "ChromaDBVectorStore" })` |
| **`graphify_explain`** | Всё, что связано с конкретным узлом/концептом | `graphify_explain({ concept: "orchestrator.py" })` |
| **`graphify_update`** | После правок в коде — обновить граф (без LLM-затрат) | `graphify_update({ path: "." })` |
| **`graphify_add`** | Добавить внешний документ/статью в корпус | `graphify_add({ url: "https://arxiv.org/abs/..." })` |
| **`graphify_export_callflow`** | Визуализировать архитектуру в Mermaid | `graphify_export_callflow({})` → `graphify-out/callflow.html` |
| **`graphify_save_result`** | Сохранить Q&A результат в память графа (обучение) | После удачного/неудачного ответа — сохранить для `reflect` |
| **`graphify_reflect`** | Агрегировать saved results в LESSONS.md | Накопить best-practices после нескольких сессий |

### Стратегия использования

1. **Перед любым вопросом об архитектуре** — сначала `graphify_query`, потом читать файлы. Граф уже знает
   связи между 144 файлами, тратить токены на их чтение вручную — waste.
2. **После любых правок в коде** — `graphify_update .` (дёшево, без API-затрат).
3. **При добавлении новых зависимостей/сервисов** — `graphify_update` с флагом `--force` после крупных
   рефакторингов.
4. **Для поиска ripple-effect** — `graphify_path` или `graphify_explain` вокруг изменяемого узла,
   чтобы увидеть что сломается.
5. **Feedback loop**: если агент дал правильный архитектурный ответ — `save-result` с `useful`.
   Если ошибся — `save-result` с `corrected` + `correction`. После накопления — `graphify reflect`.

### Чего НЕ делать

- **Не читай `graphify-out/graph.json` напрямую** — он 2.3 МБ, используй `graphify_query` / `graphify_path` / `graphify_explain`.
- **Не читай `graphify-out/GRAPH_REPORT.md` напрямую без нужды** — он 51 КБ, лучше `ctx_search` или
  `graphify_query` по конкретному вопросу.
- Не удаляй `graphify-out/` — перестройка стоит API-токенов.
- Не полагайся на граф для answers про переменные окружения, .env или содержимое `scripts/dev.sh` —
  граф покрывает их слабо.

### Сообщества (community hubs) — быстрая навигация

Наиболее полезные сообщества для архитектурных вопросов:

| Соо��щество | Что покрывает |
|---|---|
| `MCP Gateway` | `mcp-gateway/` — Go MCP-сервер, авто-генерация тулов, HTTP/rag клиенты |
| `Data Service Tools` | HTTP-обёртки MCP-инструментов над data-service |
| `Data Service HTTP Client` | `AsyncDataServiceClient` + `RagClient` в SDK |
| `RAG Pipeline Orchestrator` | `rag/pipeline.py` — import → chunk → embed → search |
| `ChromaDB Vector Store` | `rag/vector_store.py` + тесты + интерфейсы |
| `LLM Agent Streaming` | `demo/api/agent/orchestrator.py` — цикл агента, SSE |
| `Agent Type Definitions` | `types.py` — Message, ToolCall, EventData, SessionId |
| `Conversation History Manager` | `conversation.py` — память диалога, блокировки |
| `Tool Call Parser` | `tool_parser.py` — native + JSON tool call parsing |
| `MCP HTTP Client` | `mcp_client.py` — долгоживущая MCP-сессия |
| `Config Loader` | `agent-tutor-go/config/` — общий Go-пакет для data-service и mcp-gateway |

### Актуальность графа

Текущий граф построен от коммита `932288d`. Проверить актуальность:
```bash
git rev-parse HEAD  # сравнить с built_at_commit в graph.json
```
Если коммиты разошлись — `graphify_update .`

## Осторожность

- Не удаляй `university.db`, `chroma_db/`, `generated_materials/` без явной необходимости.
- **Не удаляй `./backlog/`** — там истории чатов и трассировки взаимодействий с моделью.
- Не удаляй `graphify-out/` — перестройка графа стоит API-токенов.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай без просьбы.
- Не коммить изменения без прямой просьбы пользователя.
