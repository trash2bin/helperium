# agent-tutor

LLM-агент для университетского ассистента. Даёт языковой модели доступ к учебным данным (студенты, расписание, оценки, материалы) через MCP-инструменты и семантический поиск по документам.

Пять независимых HTTP-сервисов + CLI-утилиты. Работает с Ollama, Mistral, OpenAI и любым провайдером через LiteLLM.

## Архитектура

```
                  HTTP (JSON Schema)
mcp:8083 ─────────────────────────→ data-service:8084 (Go) ──SQL──→ БД
    │                                                              (SQLite/PG/...)
    │  HTTP (OpenAPI)
    └─────────────────────────────→ rag:8082 ──────────────→ ChromaDB

web:8080  ─┬→ api:8081 ─→ mcp:8083
  │        └→ LLM-провайдер (Ollama / Mistral / OpenAI / …)
  ├→ data-service:8084 (Go, read-only данные для UI)
  └→ rag:8082 (GET /documents/list для UI)
```

| Сервис | Стек | Порт | Назначение |
|---|---|---|---|
| `data-service` | **Go** (chi, modernc/sqlite) | 8084 | Доступ к БД университета. Единственный сервис со знанием схемы хранения |
| `mcp` | FastMCP (Python) | 8083 | MCP-сервер, инструменты для агента (тонкие HTTP-обёртки) |
| `rag` | FastAPI (Python) | 8082 | Поиск по документам (ChromaDB + SQLite/PostgreSQL) |
| `api` | FastAPI + LiteLLM (Python) | 8081 | Оркестратор агента, SSE-стриминг |
| `web` | FastAPI (Python) | 8080 | Веб-интерфейс, reverse-proxy: `/api/data/*` → data-service, `/api/rag/documents` → rag, `/api/{chat,backlog,session}` → api |

**Ключевое**: `mcp` и `api` больше не содержат SQL-запросов к университетской БД. Все данные — через HTTP к `data-service` (Go). При смене схемы БД переписывается только `data-service/internal/repository/`.

База данных: **SQLite** (по умолчанию) или **PostgreSQL** (через `DATABASE_URL`).  
Векторный индекс: **ChromaDB**. Embeddings: **Sentence Transformers** (локально).

## Быстрый старт

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
uv sync

# Установить Go (для data-service)
brew install go    # macOS
# или: https://go.dev/dl/

# Запустить все 5 сервисов (Mac / нативный)
./scripts/dev.sh start

# Проверить статус
./scripts/dev.sh status

# Открыть в браузере
open http://127.0.0.1:8080
```

По умолчанию агент ожидает Ollama на `http://127.0.0.1:11434`.  
Другие провайдеры — через переменные окружения (см. `.env.example`).

```bash
# Mistral
MISTRAL_API_KEY=<token> MISTRAL_MODEL=mistral-medium ./scripts/dev.sh restart

# OpenAI
OPENAI_API_KEY=<token> ./scripts/dev.sh restart
```

### Тестовая база с нуля

Проект держит **две БД**: `university.db` (данные вуза) и `rag_documents.db` (индекс документов).
Полная инструкция — в `AGENTS.md` → «Сидинг university.db». Короткий путь:

```bash
# 1. Сгенерировать seed.json (Python + faker)
uv run agent-seedgen --students 80 --out specs/fixtures/seed.json

# 2. Залить в SQLite (Go data-service)
DB_PATH=./university.db \
  go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json

# Или в PostgreSQL
DATABASE_URL=postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor \
  DB_DRIVER=postgres \
  go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json

# 3. Поднять всё
./scripts/dev.sh start

# 4. Импортировать PDF в RAG
uv run agent-rag-ingest import ~/Documents/lecture.pdf -d <discipline-id>
```

### Docker

```bash
docker compose up -d                              # 5 long-running сервисов
docker compose --profile prod up -d               # + Caddy (HTTPS)
```

## CLI

```bash
uv run agent-rag-ingest import ~/Documents/lecture.pdf -d <discipline-id>
```

### Сидинг university.db

См. раздел в `AGENTS.md` → «Сидинг university.db». Короткий путь:

```bash
uv run agent-rag-docgen generate -d <discipline-id> --force   # генерация DOCX/PDF в RAG
```

## Команды разработчика

```bash
uv sync                           # Установить / обновить зависимости
uv run pytest                     # 113 тестов (unit + integration)
uv run ruff check .
uv run ruff format .

# data-service (Go)
cd data-service
go test ./...                     # 16 тестов
go build -o /dev/null ./cmd/server/
```

## Структура проекта

```
├── data-service/        # Go-сервис доступа к БД (:8084)
│   ├── internal/
│   │   ├── repository/  # ← единственное место с SQL
│   │   ├── handlers/    # HTTP-обработчики (не знают SQL)
│   │   ├── db/          # интерфейс DB + драйверы
│   │   ├── models/      # доменные модели (семантические поля)
│   │   └── server/      # chi-роутер, middleware, Swagger
│   └── README.md        # как переписать под новую БД
├── mcp_server/          # MCP-сервер (FastMCP, :8083)
│   ├── tools_via_http.py  # инструменты → HTTP к data-service
│   └── tools_rag.py       # RAG-инструменты → HTTP к rag
├── rag/                 # RAG HTTP-сервис (FastAPI, :8082)
├── agent-tutor-sdk/     # Shared SDK (контрактные модели, HTTP-клиенты)
│   └── src/agent_tutor_sdk/
│       ├── contracts/   # Pydantic-модели (JSON Schema)
│       ├── data_client.py  # HTTP-клиент к data-service
│       └── rag/         # HTTP-клиент к RAG
├── demo/
│   ├── api/             # API-сервер + агент (FastAPI, :8081)
│   │   └── agent/       # orchestrator, llm_client, mcp_client, tool_parser
│   └── web/             # Веб-интерфейс (FastAPI, :8080)
├── specs/               # OpenAPI + конфиги (source of truth)
│   ├── rag.openapi.yaml
│   ├── rag.openapi.yaml
│   └── api.openapi.yaml
├── rag/fixtures/      # CLI-утилиты (agent-rag-ingest, agent-rag-docgen, agent-seedgen)
├── scripts/             # dev.sh, init-db.sql
├── doc/                 # ROADMAP.md, планы
├── docker-compose.yml
└── .env.example         # Все переменные окружения
```

## Документация

- **AGENTS.md** — детали для разработчиков и AI-агентов
- **data-service/README.md** — архитектура Go-сервиса и инструкция по смене БД
- **ROADMAP.md** — полный план развития (Этапы 0–5) с критериями готовности
- **.env.example** — все переменные окружения с дефолтами

## Стек

Python 3.12+ · **Go 1.22+** · uv · FastAPI · FastMCP · LiteLLM · ChromaDB · Sentence Transformers · SQLite · PostgreSQL · pytest · ruff · Docker
