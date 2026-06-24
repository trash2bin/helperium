# agent-tutor

LLM-агент для университетского ассистента. Даёт языковой модели доступ к учебным данным (студенты, расписание, оценки, материалы) через MCP-инструменты и семантический поиск по документам.

Четыре независимых HTTP-сервиса + CLI-утилиты. Работает с Ollama, Mistral, OpenAI и любым провайдером через LiteLLM.

## Архитектура

```
web:8080 → api:8081 → mcp:8083 → rag:8082
                ↓
         LLM-провайдер (Ollama / Mistral / OpenAI / …)
```

| Сервис | Стек | Порт | Назначение |
|---|---|---|---|
| `mcp` | FastMCP | 8083 | MCP-сервер, инструменты доступа к данным |
| `rag` | FastAPI | 8082 | Поиск по документам (ChromaDB + SQLite/PostgreSQL) |
| `api` | FastAPI + LiteLLM | 8081 | Оркестратор агента, SSE-стриминг |
| `web` | FastAPI | 8080 | Веб-интерфейс, reverse-proxy к API |

База данных: **SQLite** (по умолчанию) или **PostgreSQL** (через `DATABASE_URL`).  
Векторный индекс: **ChromaDB**. Embeddings: **Sentence Transformers** (локально).

## Быстрый старт

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
uv sync

# Запустить все 4 сервиса (Mac / нативный)
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

### Docker

```bash
docker compose up -d                              # 4 long-running сервиса
docker compose --profile prod up -d               # + Caddy (HTTPS)
```

## CLI

```bash
uv run --package fixtures python -m fixtures.ingest --help   # RAG-документы
uv run --package fixtures python -m fixtures.agent_generate --help   # Генерация
```

## Команды разработчика

```bash
uv sync                           # Установить / обновить зависимости
uv run pytest                     # 109 тестов (unit + integration)
uv run ruff check .
uv run ruff format .
```

## Структура проекта

```
├── mcp_server/          # MCP-сервер (FastMCP, :8083)
│   └── tools/           # student, teacher, grades, disciplines
├── rag/                 # RAG HTTP-сервис (FastAPI, :8082)
├── agent-tutor-sdk/     # Shared SDK (db, rag client, models)
├── demo/
│   ├── api/             # API-сервер + агент (FastAPI, :8081)
│   │   └── agent/       # orchestrator, llm_client, mcp_client, tool_parser
│   └── web/             # Веб-интерфейс (FastAPI, :8080)
├── fixtures/            # Генерация тестовых данных и CLI-утилиты для этого
├── scripts/             # dev.sh, backup.py, init-db.sql
├── doc/                 # Makrdown
├── docker-compose.yml
└── .env.example         # Все переменные окружения
```

## Документация

- **AGENTS.md** — детали для разработчиков и AI-агентов: архитектура сервисов, инструменты MCP, RAG-пайплайн, переменные окружения, способы запуска
- **ROADMAP.md** — полный план развития (Этапы 0–5) с критериями готовности
- **.env.example** — все переменные окружения с дефолтами

## Стек

Python 3.12+ · uv · FastAPI · FastMCP · LiteLLM · ChromaDB · Sentence Transformers · SQLite · PostgreSQL · pytest · ruff · Docker
