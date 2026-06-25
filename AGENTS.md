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

Проект держит **две независимые БД** — см. раздел «Архитектурная гибкость» ниже.
Здесь — только инструкция как их наполнить фейковыми данными.

**Pipeline сидинга** (Python faker → JSON → Go data-service):

```
agent-seedgen  (Python + faker)
       │
       ▼  specs/fixtures/seed.json  (плоские UUID-id, storage shape)
data-service --seed  (Go, читает JSON и пишет в БД)
       │
       ▼
university.db (SQLite) или PostgreSQL
```

**Шаг 1 — сгенерировать seed.json** (Python, `agent-seedgen` CLI из workspace `rag`):

```bash
# Дефолт: 8 групп, 40 студентов, 60 оценок, seed=42
uv run agent-seedgen

# Кастомный размер
uv run agent-seedgen --students 80 --grades 200 --seed 42

# В другой файл
uv run agent-seedgen --out /tmp/my-seed.json
```

**Шаг 2 — залить в БД** (Go, `data-service` workspace):

```bash
# SQLite (по умолчанию, university.db в корне)
DB_PATH=./university.db \
  go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json

# PostgreSQL
DATABASE_URL=postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor \
  DB_DRIVER=postgres \
  go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json
```

**Защита от перезаписи prod-БД**: `data-service --seed` паникует, если
`groups` уже содержит записи (`ErrDatabaseNotEmpty`). Сначала удали БД:

```bash
rm -f university.db && go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json
# или для PostgreSQL: DROP DATABASE agent_tutor; CREATE DATABASE agent_tutor;
```

**Полная очистка и пересоздание** с нуля (все 5 сервисов и обе БД):

```bash
./scripts/dev.sh stop
rm -f university.db rag_documents.db
rm -rf chroma_db/ generated_materials/

# Пересоздать university.db
uv run agent-seedgen --students 80
DB_PATH=./university.db go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json

# Перезапустить всё
./scripts/dev.sh start

# Импортировать тестовый PDF
uv run agent-rag-ingest import ~/Documents/some.pdf -d <discipline-id>
```

Подробнее про **rag_documents.db** (вторая БД) — см. секцию `## Документы и RAG`
и `specs/fixtures/README.md` (через `agent-rag-ingest import`).

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

## Структура проекта

```
agent-tutor/
├── data-service/              # Go-сервис доступа к БД (:8084)
│   ├── cmd/
│   │   ├── server/main.go     # точка входа
│   │   └── schema-gen/main.go # генератор JSON Schema из Go-моделей
│   ├── internal/
│   │   ├── db/                # интерфейс DB + драйверы (SQLite)
│   │   ├── handlers/          # HTTP-обработчики (не знают SQL)
│   │   ├── models/            # доменные модели (source of truth → JSON Schema)
│   │   ├── repository/        # ← единственное место с SQL университета
│   │   └── server/            # chi-роутер, middleware, Swagger UI
│   │   └── seedgen/           # Go-сидер: применяет seed.json к БД (--seed)
│   ├── Dockerfile
│   └── README.md              # как переписать под новую БД
├── mcp_server/                # MCP-сервер (FastMCP, HTTP :8083)
│   ├── server.py              # роутер инструментов
│   ├── tools_via_http.py      # инструменты → HTTP к data-service (нет SQL)
│   ├── tools_rag.py           # RAG-инструменты → HTTP к rag
│   └── tests/unit/            # тесты через DataServiceClient (HTTP-моки)
├── rag/                       # RAG HTTP-сервис (FastAPI, :8082)
│   ├── service.py             # /health /search /context /documents/*
│   ├── db.py                  # свой SQLite-менеджер (rag_documents.db)
│   ├── documents_schema.py    # DDL только для documents + document_chunks
│   ├── repository.py          # CRUD документов (свой SQL, не зависит от SDK)
│   ├── pipeline.py            # парсинг → чанкинг → embedding → ChromaDB
│   ├── ... (parser, chunker, embeddings, vector_store, config)
│   └── tests/
├── agent-tutor-sdk/           # Shared SDK
│   ├── src/agent_tutor_sdk/
│   │   ├── contracts/         # Pydantic-модели (контракт, семантические поля)
│   │   ├── data_client.py     # HTTP-клиент к data-service
│   │   └── rag/               # HTTP-клиент к RAG + RAG-модели
│   └── tests/
├── demo/
│   ├── api/                   # API-сервер + агент (FastAPI, :8081)
│   │   ├── agent/             # orchestrator, llm_client, mcp_client, tool_parser
│   │   ├── data.py            # обзор данных через HTTP к data-service
│   │   └── tests/
│   └── web/                   # Веб-интерфейс (FastAPI, :8080)
├── specs/                     # Контракты (source of truth для API)
│   ├── schemas/               # JSON Schema (генерируются из Go-моделей)
│   ├── data-service.openapi.yaml
│   ├── rag.openapi.yaml
│   ├── api.openapi.yaml
│   └── README.md
├── rag/fixtures/              # CLI-утилиты (agent-rag-ingest, agent-rag-docgen, agent-seedgen)
├── scripts/                   # dev.sh, init-db.sql
├── doc/                       # ROADMAP.md
├── docker-compose.yml         # 5 long-running сервисов + db + caddy
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
- Университетские данные — через `data-service` (Go). Схема БД изолирована в `data-service/internal/repository/`.
- При смене реальной БД вуза переписывается только Go-код репозиториев, HTTP-контракт не меняется.
- `agent_tutor_sdk/db/connector.py` — для тестов, CLI-утилит и RAG (отдельная `rag_documents.db`).
- Сессии чата (`demo_sessions.sqlite`) — на SQLite, это кэш, не основные данные.

### Смена LLM-провайдера
- LiteLLM — единый клиент под Ollama, OpenAI, Mistral, Anthropic, Groq и др.
- Меняется только `OLLAMA_URL` / `MISTRAL_API_KEY` / `OPENAI_API_KEY`

### Замена любого сервиса
- Каждый long-running сервис имеет HTTP-контракт (OpenAPI/Swagger на `/docs`)
- `data-service` (Go) — контракт в `specs/data-service.openapi.yaml` + JSON Schema в `specs/schemas/`
- Если переписать `rag` на Go — `mcp_server` продолжает ходить к `http://rag:8082`
- Если переписать `mcp_server` на Rust — `api` продолжает слать MCP-over-HTTP
- Если переписать `data-service` на Rust — все потребители видят тот же JSON Schema контракт
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

**Граница**: если вуз сменит свою реальную БД — меняется **только** `data-service/internal/repository/`. RAG-слой и агенты это не замечают.

### Почему это важно
- База вуза может быть PostgreSQL, MySQL, Oracle — меняется только `data-service/internal/repository/`
- JSON Schema в `specs/schemas/` — язык-независимый контракт, можно генерировать клиентов на любом языке
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
- **Этап 2.x**: Тесты разнесены по сервисам (rag/tests/, mcp_server/tests/, demo/api/tests/, agent-tutor-sdk/tests/),
  Dockerfile'ы копируют только нужные исходники, а не весь проект целиком
- **Этап 2.7 (выполнен)**: Data-service на Go — изоляция схемы БД. `mcp_server` и `demo/api` не содержат SQL университетских данных. `specs/schemas/` с JSON Schema, авто-генерация из Go-моделей.
- **Этап 2.7.x (выполнен)**: RAG отделён в `rag_documents.db`. Репозитории SDK удалены. `agent_tutor_sdk/db/` — только connector + schema + fixtures для тестов и CLI.
- **Сидинг через единый pipeline (выполнен)**: `agent-seedgen` (Python+faker) → `specs/fixtures/seed.json` → `data-service --seed` (Go) → SQLite/PostgreSQL. CLI `agent-rag-ingest` и `agent-rag-docgen` живут в `rag/fixtures/` и ходят к сервисам по HTTP-контракту. Старый Python-пакет `fixtures/` удалён.

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

## Генерация JSON Schema

JSON Schema в `specs/schemas/` — это контракт между data-service (Go) и его потребителями (Python).
Go-модели — **source of truth**. JSON Schema генерируется из них автоматически через
`invopop/jsonschema` (рефлексия Go-структур).

### Принцип

```
data-service/internal/models/models.go    ← source of truth
    │  jsonschema:"description=..." теги
    │
    ▼  go generate (cmd/schema-gen)
specs/schemas/*.schema.json               ← генерируются автоматически
    │
    ▼  go test (TestJSONSchemaUpToDate)
    │  сравнивает сгенерированное ≡ закоммиченное → FAIL если расходятся
```

### Как изменить схему данных

1. **Отредактировать Go-модель** в `data-service/internal/models/models.go`
   - Добавить/удалить/переименовать поле
   - Обновить `jsonschema:"description=..."` тег

2. **Сгенерировать JSON Schema**:
   ```bash
   cd data-service
   go generate ./internal/models/
   ```
   Это запустит `cmd/schema-gen/main.go` и перезапишет файлы в `specs/schemas/`.

3. **Проверить что схемы актуальны**:
   ```bash
   go test ./internal/models/ -run TestJSONSchema
   ```
   Тест сравнивает сгенерированные схемы с закоммиченными. Если кто-то изменил
   Go-модель но забыл перегенерировать — тест упадёт с понятным сообщением.

4. **Обновить Python-модели** в `agent_tutor_sdk/contracts/__init__.py`:
   - Пока вручную (скопировать поля из JSON Schema).
   - В будущем: `datamodel-codegen --input specs/schemas/ --output contracts/`.

5. **Обновить SQL-запросы** в `data-service/internal/repository/`:
   - Это единственное место, которое нужно править при изменении схемы БД.
   - Handlers и HTTP-контракт не трогаются.

6. **Прогнать тесты**:
   ```bash
   go test ./...                             # Go-тесты (18)
   uv run pytest                             # Python-тесты (105)
   ```

### Структура specs/

```
specs/
├── schemas/                     # JSON Schema — source of truth для доменных типов
│   ├── student.schema.json      # генерируется из Go-модели Student
│   ├── teacher.schema.json      # генерируется из Go-модели Teacher
│   ├── discipline.schema.json
│   ├── grade.schema.json
│   ├── schedule-entry.schema.json
│   └── lesson.schema.json
├── data-service.openapi.yaml    # OpenAPI-контракт data-service (ссылается на schemas/)
├── rag.openapi.yaml             # OpenAPI-контракт RAG
├── api.openapi.yaml             # OpenAPI-контракт API
└── README.md                    # Как обновлять spec, генерировать клиент
```

## Осторожность

- Не удаляй `university.db`, `chroma_db/`, `generated_materials/` без явной необходимости.
- **Не удаляй `./backlog/`** — там истории чатов и трассировки взаимодействий с моделью.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай без просьбы.
- Не коммить изменения без прямой просьбы пользователя.
