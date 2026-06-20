# agent-tutor

LLM-агент для университетского ассистента. Даёт языковой модели доступ к учебным данным (студенты, расписание, оценки, материалы) через MCP-инструменты и семантический поиск по документам.

Четыре независимых HTTP-сервиса + CLI-утилиты. Работает с Ollama, Mistral, OpenAI и любым провайдером через LiteLLM.

## Архитектура

```
web:8080 → api:8081 → mcp:8083 → rag:8082
                ↓
         LLM-провайдер (Ollama / Mistral / OpenAI / …)
```

| Сервис | Стек | Стандартный порт | Назначение |
|---|---|---|---|
| `mcp` | FastMCP | 8083 | MCP-сервер, инструменты доступа к данным |
| `rag` | FastAPI | 8082 | Поиск по документам (ChromaDB + SQLite) |
| `api` | FastAPI + LiteLLM | 8081 | Оркестратор агента, SSE-стриминг |
| `web` | FastAPI | 8080 | Веб-интерфейс, reverse-proxy к API |

## Быстрый старт

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
uv sync
```

Запуск (4 терминала, порядок важен):

```bash
uv run agent-rag          # Терминал 1 — RAG-сервис :8082
uv run agent-tutor        # Терминал 2 — MCP-сервер :8083
uv run agent-chat-api     # Терминал 3 — API-сервер :8081
uv run agent-demo-web     # Терминал 4 — Веб-сервер :8080
```

Откройте `http://127.0.0.1:8080`.

По умолчанию агент ожидает Ollama на `http://127.0.0.1:11434`. Другие провайдеры:

```bash
# Mistral
MISTRAL_API_KEY=<token> MISTRAL_MODEL=mistral-medium uv run agent-chat-api

# OpenAI
OPENAI_API_KEY=<token> uv run agent-chat-api
```

## MCP-инструменты

| Инструмент | Что делает |
|---|---|
| `get_student(student_id)` | Карточка студента |
| `find_student_by_name(name)` | Поиск студента по ФИО |
| `get_schedule(group_id, day?)` | Расписание группы |
| `get_disciplines(student_id)` | Дисциплины студента |
| `get_materials(discipline_id, type?)` | Список файлов по дисциплине |
| `search_materials(query, discipline_id?)` | Поиск по содержимому материалов |
| `get_student_grades(student_id, discipline_id?)` | Оценки студента |
| `get_teacher_by_name(name)` | Поиск преподавателя |
| `get_teacher_schedule(teacher_name, day?)` | Расписание преподавателя |
| `list_documents(discipline_id?)` | Список документов в RAG-индексе |
| `search_documents(query, discipline_id?, limit?)` | Поиск релевантных фрагментов |
| `get_rag_context(query, discipline_id?, limit?)` | Контекст для ответа по документам |

> `import_document` доступен только через CLI `agent-ingest`, не через MCP.

## Стек

- **Python 3.12 / 3.13**, `uv` — управление зависимостями
- **FastMCP** — MCP-транспорт (HTTP и stdio)
- **FastAPI + uvicorn** — HTTP-сервисы с OpenAPI
- **LiteLLM** — единый клиент для Ollama, OpenAI, Mistral, Anthropic, Groq и др.
- **SQLite** — хранилище студентов, дисциплин, документов
- **ChromaDB** — векторный индекс для семантического поиска
- **Sentence Transformers** — локальные embeddings (`paraphrase-multilingual-MiniLM-L12-v2`)
- **pytest + pytest-asyncio + respx** — тестовая инфраструктура

## Структура проекта

```
agent-tutor/
├── mcp_server/              # MCP-сервер (FastMCP, HTTP :8083)
│   ├── server.py
│   └── tools/
│       ├── student.py
│       ├── disciplines.py
│       ├── grades.py
│       ├── teacher.py
│       └── rag.py           # Фасад → RagClient (обратная совместимость)
├── rag/                     # RAG HTTP-сервис (FastAPI, :8082)
│   ├── service.py           # /health /search /context /documents/*
│   ├── client.py            # HTTP-клиент для MCP и других сервисов
│   ├── http_models.py       # Pydantic DTO для HTTP-контракта
│   ├── pipeline.py          # парсинг → чанкинг → embedding → ChromaDB
│   ├── embeddings.py        # SentenceTransformerEmbedding
│   ├── vector_store.py      # ChromaDBVectorStore
│   ├── repository.py        # CRUD документов в SQLite
│   ├── models.py            # Pydantic-модели домена
│   ├── interfaces.py        # EmbeddingProtocol, VectorStoreProtocol
│   ├── config.py            # RagConfig из env
│   ├── parser.py            # DocumentParser (PDF, DOCX, TXT, MD, HTML)
│   └── chunker.py           # TextChunker (semantic, recursive, sentence)
├── db/
│   ├── database.py          # SQLite, схема, загрузка фикстур
│   └── models.py            # Pydantic-модели (реэкспорт из rag.models)
├── demo/
│   ├── settings.py          # Все env-переменные demo-части
│   ├── api/
│   │   ├── server.py        # FastAPI — /health /api/data /api/chat (SSE) /api/backlog
│   │   ├── backlog.py       # JSONL-бэклог всех взаимодействий с моделью
│   │   ├── data.py          # Репозиторий данных
│   │   ├── http_models.py   # Pydantic DTO для API
│   │   └── agent/
│   │       ├── orchestrator.py  # Оркестратор агента
│   │       ├── llm_client.py    # Клиент LiteLLM
│   │       ├── mcp_client.py    # HTTP-клиент к MCP
│   │       └── tool_parser.py   # Парсер вызовов инструментов
│   └── web/
│       ├── server.py        # FastAPI reverse-proxy + SSE-прокси + статика
│       └── static/          # HTML/CSS/JS
├── fixtures/
│   ├── generate.py          # Генератор fixtures.json (Faker)
│   ├── ingest.py            # CLI agent-ingest
│   └── document_generator.py  # Генерация PDF/DOCX через Ollama
├── tests/
│   ├── conftest.py
│   ├── unit/                # rag/, db/, tools/, demo/
│   └── integration/         # rag/, mcp/, api/
├── fixtures.json            # Тестовые данные
└── pyproject.toml
```

## RAG

1. `agent-ingest import <file>` читает PDF / DOCX / TXT / MD / HTML.
2. Текст разбивается на чанки (тип — `RAG_CHUNKER_TYPE`: `semantic`, `recursive`, `sentence`).
3. Для каждого чанка считается embedding через `paraphrase-multilingual-MiniLM-L12-v2`.
4. Векторы → ChromaDB, метаданные → SQLite.
5. `search_documents` ищет ближайшие фрагменты по cosine similarity.
6. `get_rag_context` возвращает готовый контекст с инструкцией для модели.

```bash
# Загрузить документы
uv run agent-ingest import ./lectures/lec01.pdf -d "cs-101" -t "Лекция 1: Введение"
uv run agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки"

# Проверить
uv run agent-ingest list
uv run agent-ingest search "быстрая сортировка" -n 3

# Удалить
uv run agent-ingest delete --document-id <id>
```

> `agent-ingest` принудительно выставляет `RAG_LOCAL_FILES_ONLY=1` — embedding-модель должна быть в локальном кэше или задана через `RAG_EMBEDDING_MODEL`.

## Демо-часть

Сайт: `http://127.0.0.1:8080`
Swagger UI: `http://127.0.0.1:8082/docs` (RAG), `http://127.0.0.1:8081/docs` (API), `http://127.0.0.1:8080/docs` (Web)

API-эндпоинты:
- `GET /health` — статус сервиса
- `GET /api/data` — учебные данные для витрины
- `POST /api/chat` — SSE-стриминг ответа агента
- `GET /api/backlog` / `GET /api/backlog/{session_id}` — история запросов
- `GET /api/session/history` — история текущей сессии

Особенности агента:
- **LiteLLM** — Ollama, OpenAI, Mistral, Anthropic, Groq и др. без смены кода
- **Стриминг** — SSE с событиями `token`, `tool_call`, `tool_result`, `final`, `error`
- **Память сессий** — хранит `DEMO_HISTORY_TURNS` последних ходов (по умолчанию 8)
- **Режим мышления** — `ENABLE_THINK=true` передаёт `reasoning_content` модели
- **Бэклог** — все запросы / ответы / инструменты / токены / тайминги в JSONL (`./backlog/`)

## CLI

### `agent-ingest`

```bash
uv run agent-ingest <command> [options]
```

| Команда | Опции | Назначение |
|---|---|---|
| `import <path>` | `-d <discipline-id>`, `-t <title>` | Загрузить документ в RAG-индекс |
| `list` | `-d <discipline-id>` | Список документов |
| `search <query>` | `-d <discipline-id>`, `-n <limit>` | Семантический поиск |
| `delete` | `--document-id <id>` или `--path <path>` | Удалить документ |
| `clear-generated` | `-d <discipline-id>` | Удалить сгенерированные материалы |

### `agent-generate`

```bash
uv run agent-generate <command> [options]
```

| Команда | Опции | Назначение |
|---|---|---|
| `generate` | `-d <discipline-id>`, `--force`, `-m <model>` | Сгенерировать материалы дисциплины |
| `generate-all` | `--force`, `-m <model>` | Сгенерировать материалы всех дисциплин |

Генерация требует Ollama. Проверка: `curl http://127.0.0.1:11434/api/tags`

## Переменные окружения

### RAG

| Переменная | По умолчанию | Описание |
|---|---|---|
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | HF-id или локальный путь |
| `RAG_EMBEDDING_BATCH_SIZE` | `64` | Батч для embeddings |
| `RAG_DEVICE` | `cpu` | `cpu`, `cuda`, `mps` |
| `RAG_CHUNKER_TYPE` | `semantic` | `semantic`, `recursive`, `sentence` |
| `RAG_CHUNK_SIZE` | `512` | Целевой размер чанка |
| `RAG_CHUNK_OVERLAP` | `80` | Перекрытие чанков |
| `RAG_PAGE_OVERLAP_TOKENS` | `50` | Перекрытие между страницами |
| `CHROMA_PATH` | `./chroma_db` | Папка ChromaDB |
| `CHROMA_COLLECTION` | `university_documents` | Имя коллекции |
| `RAG_CONTEXT_MAX_TOKENS` | `8000` | Лимит токенов в `get_rag_context` |
| `RAG_LOCAL_FILES_ONLY` | — | `1` — не скачивать модель из сети |

### MCP

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MCP_TRANSPORT` | `http` | `http` или `stdio` (только для `uv run mcp dev`) |
| `MCP_HOST` | `127.0.0.1` | Хост MCP-сервера |
| `MCP_PORT` | `8083` | Порт MCP-сервера |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | URL RAG-сервиса |

### API

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MCP_SERVICE_URL` | `http://127.0.0.1:8083/mcp` | URL MCP-сервиса |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Адрес Ollama |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `MISTRAL_API_KEY` | — | Ключ Mistral API |
| `MISTRAL_MODEL` | — | Модель Mistral |
| `DEMO_REQUEST_TIMEOUT` | `600` | Таймаут turn агента (сек); в prod: `90` |
| `ENABLE_THINK` | `true` | Режим мышления; в prod: `false` |
| `DEMO_HISTORY_TURNS` | `8` | Ходов в истории сессии |
| `DEMO_HISTORY_CONTENT_CHARS` | `6000` | Лимит символов истории |
| `BACKLOG_DIR` | `./backlog/` | Папка JSONL-бэклога |
| `BACKLOG_RETENTION_DAYS` | `30` | Дней хранить бэклог |
| `DB_PATH` | `./university.db` | Путь к SQLite |
| `DEMO_DEBUG` | — | `1` — отладочный режим |
| `LITELLM_DEBUG` | — | `true` — отладочный режим LiteLLM |

### Web

| Переменная | По умолчанию | Описание |
|---|---|---|
| `API_BEARER_TOKEN` | — | Bearer-токен (обязателен в prod) |
| `WEB_ORIGIN` | `*` | CORS origin (ограничить в prod) |

### Генерация (`agent-generate`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DOCGEN_MODEL` | `qwen2.5:0.5b` | Ollama-модель |
| `DOCGEN_OLLAMA_URL` | `$OLLAMA_HOST/api/generate` | Endpoint Ollama |
| `DOCGEN_OUTPUT_DIR` | `./generated_materials` | Папка результатов |
| `DOCGEN_NUM_PREDICT` | `4500` | Лимит генерируемых токенов |
| `DOCGEN_FAKE_SEED` | — | Seed для воспроизводимого Faker-каркаса |

## Разработка

```bash
# Зависимости
uv sync

# Линтер и форматирование
uv run ruff check .
uv run ruff format .

# Тесты
uv run pytest                            # unit + integration
uv run pytest -m unit                    # только unit
uv run pytest -m integration             # только integration
uv run pytest --cov --cov-fail-under=40  # с проверкой покрытия

# Сгенерировать тестовую базу данных
uv run python fixtures/generate.py
```

MCP Inspector (проверка инструментов):
```bash
# Запустить RAG-сервис
uv run agent-rag

# Запустить MCP-сервер в другом терминале
RAG_SERVICE_URL=http://127.0.0.1:8082 uv run agent-tutor

# Подключиться через Inspector: http://127.0.0.1:8083/mcp
```

Команды `uv`:

| Команда | Что делает |
|---|---|
| `uv sync` | Создать / обновить `.venv` по `uv.lock` |
| `uv run <cmd>` | Запустить команду в окружении проекта |
| `uv add <pkg>` | Добавить зависимость |
| `uv remove <pkg>` | Удалить зависимость |
| `rm -rf .venv && uv sync` | Пересоздать окружение |

## Статус проекта

| Этап | Статус | Что сделано |
|---|---|---|
| 0 · Разделение сервисов | ✅ | 4 независимых HTTP-сервиса, MCP на HTTP-транспорте, CLI-утилиты вынесены |
| 1 · Тестирование | ✅ | unit + integration тесты, покрытие ≥ 40%, OpenAPI-контракты, ruff |

Полный план с критериями готовности — в [ROADMAP.md](ROADMAP.md).
Детали для разработчиков и агентов — в [AGENTS.md](AGENTS.md).
