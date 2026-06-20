# AGENTS.md

Управление проектом

## Проект

- Это полноценный агент с разделёнными сервисами: `mcp`, `rag`, `api`, `web` как long-running сервисы, и CLI-утилиты `agent-ingest`, `agent-generate` как one-shot команды.
- Управление зависимостями и запуском идёт через `uv` и `pyproject.toml`.
- Все сервисы запускаются независимо и общаются друг с другом по HTTP.
- CLI для документов и генерации: `agent-ingest` (RAG-документы), `agent-generate` (генерация материалов). Детали в `fixtures/README.md`

## Базовые команды

```bash
uv sync
uv run agent-tutor              # MCP-сервер (порт 8083, HTTP-транспорт)
uv run agent-rag                # RAG HTTP-сервис (порт 8082)
uv run agent-chat-api           # API сервер с агентом (порт 8081)
uv run agent-demo-web           # Веб-сервер (порт 8080)
uv run agent-ingest --help       # CLI для работы с RAG-документами
uv run agent-generate --help    # CLI для генерации учебных материалов
uv run pytest                   # Запуск тестов (unit и integration)
```

Используй `uv run ...` для запуска и отладки частей приложения. После внесения изменений в логику API или RAG всегда запускай тесты для проверки регрессий.

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

> **Примечание:** `import_document` доступен только через CLI `agent-ingest`, не через MCP-сервер.

## Структура проекта

Подробно описана в `README.md` в корне проекта. Сервисы разделены по пакетам:
- `mcp_server/` — MCP-сервер (FastMCP, HTTP-транспорт на порту 8083)
- `rag/` — RAG HTTP-сервис (Starlette, порт 8082)
- `demo/api/` — API сервер с агентом (FastAPI, порт 8081)
- `demo/web/` — Веб-интерфейс (FastAPI, порт 8080)
- `fixtures/` — CLI-утилиты и генераторы тестовых данных

## Демо-часть

Для демонстрации работы системы доступны два компонента:

- **API сервер** (`demo/api/server.py`) — обрабатывает запросы к LLM-провайдерам через LiteLLM и MCP-серверу, обеспечивает вызов инструментов и управление контекстом агента
- **Веб-сервер** (`demo/web/server.py`) — отдаёт статические файлы интерфейса и проксирует запросы к API (reverse-proxy + SSE-прокси)

Архитектура:
- `demo/api/agent/` — ядро: вызов моделей через LiteLLM, подключение к MCP, вызов инструментов, валидация вызовов, рекурсивные вызовы моделей, память контекста, стриминг ответов, обработка reasoning. Ошибки логики работы агента всегда находятся в этой директории.
- `demo/api/agent/llm_client.py` — клиент для работы с LLM через LiteLLM
- `demo/api/agent/mcp_client.py` — HTTP-клиент для MCP-сервера
- `demo/api/agent/orchestrator.py` — оркестратор работы агента
- `demo/api/backlog.py` — полный бэклог модели: JSONL-файл на сессию со всеми запросами/ответами/токенами/таймингами/вызовами инструментов
- `demo/api/data.py` — репозиторий данных для демонстрации
- `demo/api/server.py` — HTTP API endpoints: `GET /health`, `GET /api/data`, `POST /api/chat` (SSE), `GET /api/backlog`, `GET /api/backlog/{session_id}`
- `demo/web/server.py` — FastAPI reverse-proxy с SSE-проксированием, статика

### Ключевые особенности агента

- **LiteLLM интеграция**: Замена HTTP API на LiteLLM для унифицированной работы с различными LLM-провайдерами (**Ollama**, OpenAI, Anthropic, **Mistral**, Groq, HuggingFace и др.)
- **Режим мышления (Thinking Mode)**: Поддержка `reasoning_content` через параметр `think: true` — модель возвращает свои рассуждения, которые сохраняются в бэклоге и помогают улучшать ответы только до полноценного ответа пользователю
- **Стриминг ответов**: Потоковая передача токенов и событий (token, tool_call, tool_result, final, error) через Proxy Server-Sent Events (SSE)
- **Память сессий**: Хранение истории диалога для каждой сессии с настраиваемым количеством хранимых ходов (`DEMO_HISTORY_TURNS`, по умолчанию 8) и ограничением длины содержимого (`DEMO_HISTORY_CONTENT_CHARS`, по умолчанию 6000)
- **Полный бэклог**: Автоматическое логирование всех взаимодействий с моделью в JSONL-файлы, включая запросы, ответы, вызов инструментов, использование токенов и тайминги

Переменные окружения: можно узнать в `demo/settings.py`

## Виртуальное окружение

- `uv sync` создаёт/обновляет `.venv`.
- `uv venv --python 3.13` явно создаёт окружение на Python 3.13.
- `source .venv/bin/activate` активирует окружение в shell.
- `deactivate` выходит из активированного окружения.
- `rm -rf .venv && uv sync` пересоздаёт окружение с нуля.
Управление проектом осуществляется через `uv` использовать системный python — **антипаттерн** (не надо)

## Архитектура RAG-пакета

Пакет `rag/` не зависит от `db/` — циклическая зависимость разорвана.  
`DocumentRepository` принимает сырой `sqlite3.Connection`, а не `Database`.

RAG выделен в **отдельный HTTP-сервис** (`rag/service.py` на FastAPI, порт 8082) с HTTP-клиентом (`rag/client.py`) для вызовов из MCP и других компонентов.

- `rag/interfaces.py` — протоколы (`EmbeddingProtocol`, `VectorStoreProtocol`) для подмены реализаций
- `rag/embeddings.py` → `SentenceTransformerEmbedding` (реализует `EmbeddingProtocol`)
- `rag/vector_store.py` → `ChromaDBVectorStore` (реализует `VectorStoreProtocol`)
- Pydantic-модели (`Document`, `Material`, `RagSearchResult`, ...) в `rag/models.py`; `db/models.py` их реэкспортирует
- `rag/pipeline.py` — оркестрация парсинг → чанкинг → сохранение
- `rag/service.py` — HTTP-сервис с endpoint'ами: `/health`, `/search`, `/context`, `/documents/list`, `/documents/import`, `/documents/delete`
- `rag/client.py` — HTTP-клиент для MCP-сервера
- `rag/http_models.py` — Pydantic-модели для HTTP-контракта
- `mcp_server/tools/rag.py` — фасад для обратной совместимости с `fixtures/document_generator.py`, использует `RagClient`

### Стандартизация API (OpenAPI/Swagger)

Все основные HTTP-сервисы (`rag`, `api`, `web`) теперь используют FastAPI и следуют строгому OpenAPI-контракту.

- **Документация**: Каждый сервис автоматически генерирует Swagger UI. Для просмотра контракта запусти соответствующий сервис и перейди по адресу:
  - RAG: `http://127.0.0.1:8082/docs`
  - API: `http://127.0.0.1:8081/docs`
  - Web: `http://127.0.0.1:8080/docs`
- **Контракты**: Модели запросов и ответов описаны в `rag/http_models.py` и `demo/api/http_models.py`. При изменении API обязательно обновляй эти Pydantic-модели, чтобы документация оставалась актуальной.
- **Валидация**: FastAPI автоматически валидирует входящие данные по этим моделям.

## Документы и RAG

RAG-слой работает локально через SQLite + ChromaDB:

1. `import_document` читает файл.
2. Текст разбивается на чанки.
3. Для каждого чанка считается embedding моделью `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
4. Векторы и тексты чанков сохраняются в ChromaDB.
5. Метаданные документов сохраняются в SQLite.
6. `search_documents` ищет похожие фрагменты в ChromaDB.
7. `get_rag_context` возвращает фрагменты и инструкцию для модели: отвечать только по найденным источникам.

Основные команды для работы с RAG:
- `agent-ingest import <path>` импортирует документы в SQLite + ChromaDB
- `agent-ingest list` показывает документы
- `agent-ingest search <query>` проверяет семантический поиск без MCP-сервера
- `agent-ingest delete --document-id <id>` или `agent-ingest delete --path <path>` удаляет документ

`agent-ingest` принудительно выставляет `RAG_LOCAL_FILES_ONLY=1`, поэтому embedding-модель должна быть в локальном кэше или задана локальным путём через `RAG_EMBEDDING_MODEL`.

Пример использования:

```bash
# Загрузил лекции
uv run agent-ingest import ./lectures/lec01.pdf -d "cs-101" -t "Лекция 1: Введение"
uv run agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки"

# Проверил что загрузилось
uv run agent-ingest list

# Протестировал поиск
uv run agent-ingest search "как работает быстрая сортировка" -n 3
```

## Генерация материалов

Генерация учебных материалов (PDF/DOCX) вынесена в отдельную CLI-утилиту `agent-generate`:

- `agent-generate generate -d <discipline-id>` — сгенерировать материалы одной дисциплины
- `agent-generate generate-all` — сгенерировать материалы всех дисциплин
- `--force` — пересоздать уже существующие материалы
- `agent-ingest clear-generated` — удалить сгенерированные материалы из SQLite, ChromaDB и с диска

Генерация требует работающий Ollama. Проверка: `curl ${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags`.

## Важные переменные

- `DB_PATH` — путь к SQLite-базе, по умолчанию `./university.db`.
- `CHROMA_PATH` — папка ChromaDB, по умолчанию `./chroma_db`.
- `RAG_EMBEDDING_MODEL` — HF-id или локальный путь к embedding-модели.
- `RAG_DEVICE` — устройство embeddings: `cpu`, `cuda`, `mps`.
- `DOCGEN_MODEL` — Ollama-модель, по умолчанию `qwen2.5:0.5b`.
- `DOCGEN_OLLAMA_URL` — полный endpoint `/api/generate`.
- `DOCGEN_OUTPUT_DIR` — папка для `generated_materials`.

Более полный справочник команд и переменных находится в `README.md`.

## Пример запроса к модели

Пример запроса с использованием инструментов:

```json
{
  "tool_name": "find_student_by_name",
  "parameters": {
    "name": "Иван Петров Иванович"
  }
}
```

```json
{
  "tool_name": "get_disciplines",
  "parameters": {
    "student_id": "456c4e68-290a-4f8b-b0d0-545534adaf3e"
  }
}
```

```json
{
  "tool_name": "get_materials",
  "parameters": {
    "discipline_id": "1"
  }
}
```

Пример вопроса пользователя:
```
Какие материалы доступны студенту Ивану Петрову Ивановичу по его дисциплинам?
```

## Текущее состояние

Проект находится на стадии рабочего прототипа. **Выполнены этапы 0.0–0.5 из ROADMAP** (разделение сервисов, HTTP-транспорт, FastAPI, CLI-утилиты).

Работает:
- MCP-сервер (`mcp_server/server.py`) — FastMCP с HTTP-транспортом на порту 8083
- RAG HTTP-сервис (`rag/service.py`) — Starlette на порту 8082
- API сервер (`demo/api/server.py`) — FastAPI на порту 8081, использует HTTP-клиент к MCP
- Веб-сервер (`demo/web/server.py`) — FastAPI reverse-proxy на порту 8080
- SQLite-база инициализируется и загружает фикстуры при старте
- Инструменты возвращают типизированные Pydantic-ответы через HTTP к RAG
- Проверено в MCP Inspector и Goose, Claude Code, Pi
- **Демо-часть**: LiteLLM интеграция, стриминг ответов, память сессий, режим мышления, полный бэклог всех взаимодействий с моделью
- Бэклог сессий автоматически сохраняется в `./backlog/` в формате JSONL с полной историей: запросы, ответы, вызов инструментов, токены, тайминги

### Порядок запуска сервисов

```bash
# Терминал 1: RAG HTTP-сервис (порт 8082)
uv run python -m rag.service

# Терминал 2: MCP-сервер (порт 8083, использует RAG по HTTP)
RAG_SERVICE_URL=http://127.0.0.1:8082 uv run python -m mcp_server.server

# Терминал 3: API сервер (порт 8081, использует MCP по HTTP)
MCP_SERVICE_URL=http://127.0.0.1:8083/mcp uv run python -m demo.api.server

# Терминал 4: Веб-сервер (порт 8080, прокси к API)
uv run python -m demo.web.server
```

## Осторожность

- Не удаляй `university.db`, `chroma_db/` или `generated_materials/`, если задача явно этого не требует.
- **Не удаляй папку `./backlog/`** — там хранятся истории чатов и полные трассировки взаимодействий с моделью.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай их без прямой просьбы.
- Не коммить изменения без прямой просьбы пользователя.
