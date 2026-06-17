# AGENTS.md

Управление проектом

## Проект

- Это Python MCP-сервер для университетского ассистента.
- Управление зависимостями и запуском идёт через `uv` и `pyproject.toml`.
- Основной серверный entrypoint: `agent-tutor = server:main`.
- CLI для документов и генерации: `agent-ingest = fixtures.ingest:main`.

## Базовые команды

```bash
uv sync
uv run agent-ingest --help
uv run mcp dev server.py
uv tool install . --reinstall
```

Используй `uv run ...` для разработки. `uv tool install . --reinstall` нужен, когда нужно обновить глобально установленную CLI-команду после изменения кода.

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

```
agent-tutor/
├── server.py           # MCP-сервер, точка входа
├── db/
│   ├── database.py     # SQLite, создание таблиц, загрузка фикстур
│   └── models.py       # Pydantic-модели (реэкспорт RAG-моделей из rag.models)
├── tools/
│   ├── student.py      # StudentTools
│   ├── disciplines.py  # DisciplineTools (get_materials/search_materials через doc_repo)
│   ├── grades.py       # GradeTools
│   ├── teacher.py      # TeacherTools
│   └── rag.py          # Устаревший фасад, заглушка для обратной совместимости
├── rag/                # RAG-слой (не зависит от db пакета)
│   ├── __init__.py     # create_rag_pipeline(connection, config)
│   ├── config.py       # RagConfig из переменных окружения
│   ├── interfaces.py   # EmbeddingProtocol, VectorStoreProtocol
│   ├── embeddings.py   # SentenceTransformerEmbedding (реализует EmbeddingProtocol)
│   ├── vector_store.py # ChromaDBVectorStore (реализует VectorStoreProtocol)
│   ├── parser.py       # DocumentParser (PDF, DOCX, TXT, MD, HTML)
│   ├── chunker.py      # TextChunker (semantic, recursive, sentence)
│   ├── repository.py   # DocumentRepository — CRUD документов/чанков в SQLite
│   ├── pipeline.py     # RAGPipeline — оркестрация парсинг → чанкинг → сохранение
│   └── models.py       # Pydantic-модели (Document, Material, RagSearchResult, ...)
├── fixtures/
│   ├── generate.py     # Генератор тестовых данных
│   ├── ingest.py       # CLI agent-ingest для RAG-документов и генерации материалов
│   └── document_generator.py # Генерация PDF/DOCX-материалов для фикстур
├── demo/               # Демо-часть: API и веб-интерфейс
│   ├── settings.py     # Конфигурация demo (порты, Ollama URL, таймауты)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── agent.py    # Логика агента: вызов LiteLLM, MCP, инструментов, память контекста
│   │   ├── backlog.py  # Бэклог взаимодействий с моделью (JSONL)
│   │   ├── data.py     # DemoDataRepository — данные для демонстрации
│   │   └── server.py   # REST/SSE API сервер (Health, Data, Chat, Backlog)
│   └── web/
│       ├── __init__.py
│       ├── server.py   # Статический веб-сервер
│       └── static/     # Статические файлы (HTML, CSS, JS)
└── fixtures.json       # Тестовые данные
```

## Демо-часть

Для демонстрации работы системы доступны два компонента:

- **API сервер** (`demo/api/server.py`) — обрабатывает запросы к Ollama и MCP-серверу, обеспечивает вызов инструментов и управление контекстом агента
- **Веб-сервер** (`demo/web/server.py`) — отдаёт статические файлы интерфейса и подключается к API

Архитектура:
- `demo/api/agent.py` — ядро: вызов моделей через LiteLLM, подключение к MCP, вызов инструментов, валидация вызовов, рекурсивные вызовы моделей, память контекста, стриминг ответов, обработка reasoning
- `demo/api/backlog.py` — полный бэклог модели: JSONL-файл на сессию со всеми запросами/ответами/токенами/таймингами/вызовами инструментов
- `demo/api/data.py` — репозиторий данных для демонстрации
- `demo/api/server.py` — HTTP API endpoints: `/health`, `/api/data`, `/api/chat` (SSE), `/api/backlog`, `/api/backlog/{session_id}`
- `demo/web/server.py` — статический сервер с индексной страницей и статикой

### Ключевые особенности агента

- **LiteLLM интеграция**: Замена HTTP API на LiteLLM для унифицированной работы с различными LLM-провайдерами (Ollama, OpenAI, Anthropic, Mistral, Groq, Together AI, HuggingFace и др.)
- **Режим мышления (Thinking Mode)**: Поддержка `reasoning_content` через параметр `think: true` — модель возвращает свои рассуждения, которые сохраняются в бэклоге и помогают улучшать ответы
- **Стриминг ответов**: Потоковая передача токенов и событий (token, tool_call, tool_result, final, error) через Server-Sent Events (SSE)
- **Память сессий**: Хранение истории диалога для каждой сессии с настраиваемым количеством хранимых ходов (`DEMO_HISTORY_TURNS`, по умолчанию 8) и ограничением длины содержимого (`DEMO_HISTORY_CONTENT_CHARS`, по умолчанию 6000)
- **Полный бэклог**: Автоматическое логирование всех взаимодействий с моделью в JSONL-файлы, включая запросы, ответы, вызов инструментов, использование токенов и тайминги

Запуск:
```bash
# API сервер (порты из DEMO_API_PORT, по умолчанию 8081)
uv run python -m demo.api.server

# Веб-сервер (порты из DEMO_WEB_PORT, по умолчанию 8080)
uv run python -m demo.web.server
```

Переменные окружения:
- `DEMO_DEBUG` — 1 включить отладочный режим, 0 выключить
- `DEMO_API_HOST`/`DEMO_API_PORT` — хост/порт API (по умолчанию `127.0.0.1:8081`)
- `DEMO_WEB_HOST`/`DEMO_WEB_PORT` — хост/порт веб-сервера (по умолчанию `127.0.0.1:8080`)
- `OLLAMA_URL` — адрес Ollama (по умолчанию `http://127.0.0.1:11434`)
- `OLLAMA_MODEL` — модель Ollama (по умолчанию `qwen2.5:0.5b`)
- `DEMO_REQUEST_TIMEOUT` — таймаут запросов (по умолчанию `600` секунд = 10 минут)
- `ENABLE_THINK` — включить режим мышления/рассуждений (`true`/`false`, по умолчанию `true`)
- `DEMO_HISTORY_TURNS` — количество хранимых ходов в истории сессии (по умолчанию `8`)
- `DEMO_HISTORY_CONTENT_CHARS` — максимальная длина содержимого в истории (по умолчанию `6000`)
- `BACKLOG_DIR` — папка для JSONL-файлов бэклога (по умолчанию `./backlog/`)
- `BACKLOG_RETENTION_DAYS` — дней хранить файлы бэклога (по умолчанию `30`)
- `LITELLM_DEBUG` — включить отладочный режим LiteLLM (`true`/`false`)

## Виртуальное окружение

- `uv sync` создаёт/обновляет `.venv`.
- `uv venv --python 3.12` явно создаёт окружение на Python 3.12.
- `source .venv/bin/activate` активирует окружение в shell.
- `deactivate` выходит из активированного окружения.
- `rm -rf .venv && uv sync` пересоздаёт окружение с нуля.

## Архитектура RAG-пакета

Пакет `rag/` не зависит от `db/` — циклическая зависимость разорвана.  
`DocumentRepository` принимает сырой `sqlite3.Connection`, а не `Database`.

- `rag/interfaces.py` — протоколы (`EmbeddingProtocol`, `VectorStoreProtocol`) для подмены реализаций
- `rag/embeddings.py` → `SentenceTransformerEmbedding` (реализует `EmbeddingProtocol`)
- `rag/vector_store.py` → `ChromaDBVectorStore` (реализует `VectorStoreProtocol`)
- Pydantic-модели (`Document`, `Material`, `RagSearchResult`, ...) переехали в `rag/models.py`; `db/models.py` их реэкспортирует
- `server.py` использует `create_rag_pipeline(db.connector)` напрямую вместо `RagTools`

- `tools/disciplines.py` берёт `DocumentRepository` через конструктор, а не ходит в `Database`

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

- `agent-ingest generate -d <discipline-id>` генерирует материалы одной дисциплины.
- `agent-ingest generate-all` генерирует материалы всех дисциплин.
- `--force` пересоздаёт уже существующие материалы.
- `clear-generated` удаляет сгенерированные материалы из SQLite, ChromaDB и с диска.
- Генерация требует локальную Ollama. Проверка: `curl ${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags`.

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

Проект находится на стадии рабочего прототипа.

Работает:
- MCP-сервер стартует и публикует инструменты
- SQLite-база инициализируется и загружает фикстуры при старте
- Инструменты возвращают типизированные Pydantic-ответы
- RAG-метаданные хранятся в SQLite, а векторный индекс документов — в ChromaDB
- Проверено в MCP Inspector и Goose, Claude Code, Pi
- **Демо-часть**: LiteLLM интеграция, стриминг ответов, память сессий, режим мышления, полный бэклог всех взаимодействий с моделью
- Бэклог сессий автоматически сохраняется в `./backlog/` в формате JSONL с полной историей: запросы, ответы, вызов инструментов, токены, тайминги

## Осторожность

- Не удаляй `university.db`, `chroma_db/` или `generated_materials/`, если задача явно этого не требует.
- **Не удаляй папку `./backlog/`** — там хранятся истории чатов и полные трассировки взаимодействий с моделью.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай их без прямой просьбы.
- Не коммить изменения без прямой просьбы пользователя.
