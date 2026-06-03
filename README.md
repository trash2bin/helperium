# agent-tutor

Полноценный агент с MCP-сервером для университетского ассистента на базе LLM. Даёт языковой модели доступ к данным об учебном процессе через набор инструментов — студенты, расписание, дисциплины, учебные материалы. А также прикрученным RAG для поиска учебных материалов по содержимому.

| Часть | Описание |
|---|---|
| MCP-сервер | Сервер для взаимодействия с базой данной и RAG|
| RAG | Поиск учебных материалов по содержимому (Пока что часть MCP-сервера) |
| API с агентом | Взаимодействия с агентом и сам кастомный клиент агента: [Ядро агента](demo/api/agent.py) |
| Web UI | Сайт визитка с чатом для взаимодействия с агентом и презентации |

## Что умеет

Чем может пользоватся агент:

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


## Стек

- Python 3.12 и 3.13
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — транспортный слой MCP
- [LiteLLM](https://github.com/BerriAI/litellm) — унифицированный интерфейс для работы с различными LLM-провайдерами
- SQLite — локальное хранилище
- Pydantic — схемы ответов
- Faker — генерация тестовых данных
- python-docx — генерация DOCX-материалов для фикстур
- встроенный PDF-генератор — генерация PDF-лекций для фикстур без внешних утилит
- Sentence Transformers — локальная embedding-модель
- ChromaDB — локальная векторная база для RAG-поиска
- Ollama — локальная генерация текста материалов через `agent-ingest`

## Структура

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
│   ├── embeddings.py   # SentenceTransformerEmbedding
│   ├── vector_store.py # ChromaDBVectorStore
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
│   │   ├── agent.py    # Логика агента: LiteLLM, MCP, инструменты, контекст
│   │   ├── backlog.py  # Бэклог взаимодействий с моделью (JSONL)
│   │   ├── data.py     # Управление базой данных
│   │   └── server.py   # REST/SSE API сервер
│   └── web/
│       ├── __init__.py
│       ├── server.py   # Статический веб-сервер
│       └── static/     # Статические файлы (HTML, CSS, JS)
└── fixtures.json       # Тестовые данные
```

## Архитектура RAG-пакета

Пакет `rag/` не зависит от `db/`.
`DocumentRepository` принимает сырой `sqlite3.Connection`.

- `rag/interfaces.py` — протоколы (`EmbeddingProtocol`, `VectorStoreProtocol`) для подмены реализаций
- `rag/embeddings.py` → `SentenceTransformerEmbedding`, `rag/vector_store.py` → `ChromaDBVectorStore`
- Pydantic-модели (`Document`, `Material`, `RagSearchResult`, ...) переехали в `rag/models.py`; `db/models.py` реэкспортирует
- `server.py` использует `create_rag_pipeline(db.connector)` напрямую (вместо `RagTools`)

## RAG по документам

RAG-слой работает локально через SQLite + ChromaDB:

1. `import_document` читает файл.
2. Текст разбивается на чанки.
3. Для каждого чанка считается embedding моделью `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
4. Векторы и тексты чанков сохраняются в ChromaDB.
5. Метаданные документов сохраняются в SQLite.
6. `search_documents` ищет похожие фрагменты в ChromaDB.
7. `get_rag_context` возвращает фрагменты и инструкцию для модели: отвечать только по найденным источникам.

По умолчанию ChromaDB хранит индекс в папке `chroma_db/`.

При первом использовании RAG embedding-модель может скачаться из Hugging Face, если запуск идёт через MCP-сервер и `RAG_LOCAL_FILES_ONLY` не включён. CLI `agent-ingest` сейчас принудительно выставляет `RAG_LOCAL_FILES_ONLY=1`, поэтому для него модель должна быть уже в локальном кэше или задана локальным путём через `RAG_EMBEDDING_MODEL`.

```bash
RAG_LOCAL_FILES_ONLY=1
RAG_DEVICE=cuda
```

Пример:

```bash
uv run agent-ingest import ~/Documents/test.pdf
```

```text
Найди задание под номером 11 в Методичка по базам данных
```

## Быстрый старт

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
```

Проект управляется через `uv` и описан в `pyproject.toml`. Основные entrypoint-команды:

- `agent-tutor` — MCP-сервер, точка входа `server:main`.
- `agent-ingest` — CLI для документов и генерации, точка входа `fixtures.ingest:main`.

Базовые команды:

```bash
uv sync
uv run agent-ingest --help
uv run mcp dev server.py
uv tool install . --reinstall
```

Используй `uv run ...` для разработки. `uv tool install . --reinstall` нужен, когда нужно обновить глобально установленную CLI-команду после изменения кода.

## Демо-сайт и чат

Для демонстрации есть два компонента, разделённые на API и веб-часть:

- **API сервер** (`demo/api/server.py`) — REST/SSE API над моделькой, отвечает за вызов моделей через LiteLLM, работу с инструментами и управление контекстом
- **Веб-сервер** (`demo/web/server.py`) — статический сайт с витриной данных и плавающим окном чата

Архитектура API:
- `demo/api/agent.py` — логика агента: вызов модели через LiteLLM, подключение к MCP-серверу, вызов инструментов, валидация вызовов, рекурсивные вызовы моделей, память контекста, стриминг ответов, обработка reasoning
- `demo/api/backlog.py` — полный бэклог взаимодействий: JSONL-файл на каждую сессию с запросами, ответами, вызовами инструментов, токенами и таймингами (хранит данные в `backlog/`)
- `demo/api/server.py` — HTTP API с endpoints: `GET /health`, `GET /api/data`, `POST /api/chat` (Server-Sent Events), `GET /api/backlog`, `GET /api/backlog/{session_id}`

### Ключевые особенности

- **LiteLLM интеграция**: Агент использует LiteLLM для унифицированной работы с различными LLM-провайдерами
- **Режим мышления**: Модель может рассуждать в несколько этап приходя к конкретному ответу пользователю
- **Стриминг ответов**: Потоковая передача токенов и событий через Server-Sent Events (SSE)
- **Память сессий**: Автоматическое хранение истории диалога для каждой сессии
- **Полный бэклог**: Все взаимодействия с моделью логируются в JSONL-файлах в папке `backlog/`

Запуск:

```bash
# API сервер (порт по умолчанию 8081)
uv run python -m demo.api.server

# Веб-сервер (порт по умолчанию 8080)
uv run python -m demo.web.server
```

По умолчанию сайт доступен на `http://127.0.0.1:8080`, API — на `http://127.0.0.1:8081`.

Переменные окружения:
- `DEMO_API_HOST`/`DEMO_API_PORT` — хост/порт API сервера (по умолчанию `127.0.0.1:8081`)
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
- `DEMO_DEBUG` — 1 включить отладочный режим demo

Пример запуска с кастомной моделью:
```bash
OLLAMA_MODEL=carstenuhlig/omnicoder-9b:latest uv run python -m demo.api.server
```

> **Примечание:** Для работы демо требуется запущенный локальный Ollama (или другой провайдер, поддерживаемый LiteLLM).

Синхронизировать зависимости в локальное окружение `.venv`:

```bash
uv sync
```

Запускать команды без активации окружения:

```bash
uv run agent-ingest --help
uv run agent-tutor
```

## Виртуальное окружение

Чаще всего достаточно `uv sync`: он создаёт `.venv`, если его ещё нет, и ставит зависимости из `pyproject.toml`/`uv.lock`.

Полезные команды для работы с виртуальным окружением:

| Команда | Что делает |
|---|---|
| `uv sync` | Создать/обновить `.venv` по `uv.lock` |
| `uv run <command>` | Запустить команду внутри проектного окружения |
| `uv venv --python 3.12` | Явно создать `.venv` на Python 3.12 |
| `source .venv/bin/activate` | Активировать окружение в текущем shell |
| `deactivate` | Выйти из активированного окружения |
| `uv pip list` | Показать пакеты в `.venv` |
| `uv add <package>` | Добавить runtime-зависимость в `pyproject.toml` |
| `uv remove <package>` | Удалить зависимость из проекта |
| `rm -rf .venv && uv sync` | Пересоздать окружение с нуля |

Для разработки обычно удобнее использовать `uv run ...`, так как команда всегда использует текущий код и зависимости из проекта. Установка через `uv tool install . --reinstall` нужна, когда требуется обновить глобально установленную CLI-команду после изменения кода.

Если нужна тестовая база данных (`fixtures.json`):

```bash
uv run python fixtures/generate.py
```

Установить CLI-команды как `uv tool`:

```bash
uv tool install .
```

Пересобрать пакет после изменения кода:

```bash
uv tool install . --reinstall
```

`uv tool install` ставит команду в отдельное tool-окружение. Для разработки обычно удобнее `uv run ...`, потому что команда всегда использует текущий код и зависимости из проекта.

## Виртуальное окружение uv

Чаще всего достаточно `uv sync`: он создаёт `.venv`, если его ещё нет, и ставит зависимости из `pyproject.toml`/`uv.lock`.

Полезные команды:

| Команда | Что делает |
|---|---|
| `uv sync` | Создать/обновить `.venv` по `uv.lock` |
| `uv run <command>` | Запустить команду внутри проектного окружения |
| `uv venv --python 3.12` | Явно создать `.venv` на Python 3.12 |
| `source .venv/bin/activate` | Активировать окружение в текущем shell |
| `deactivate` | Выйти из активированного окружения |
| `uv pip list` | Показать пакеты в `.venv` |
| `uv add <package>` | Добавить runtime-зависимость в `pyproject.toml` |
| `uv remove <package>` | Удалить зависимость из проекта |
| `rm -rf .venv && uv sync` | Пересоздать окружение с нуля |

## CLI `agent-ingest`

`agent-ingest` находится в `fixtures/ingest.py`. Он работает с локальной SQLite-базой, ChromaDB-индексом и генератором учебных материалов. Принудительно выставляет `RAG_LOCAL_FILES_ONLY=1`, поэтому embedding-модель должна быть в локальном кэше или задана локальным путём через `RAG_EMBEDDING_MODEL`.

Общий формат:

```bash
uv run agent-ingest <command> [options]
```

Если пакет установлен через `uv tool install . --reinstall`, можно запускать без `uv run`:

```bash
agent-ingest <command> [options]
```

Команды:

| Команда | Назначение |
|---|---|
| `import <path>` | Загрузить PDF/DOCX/TXT/MD/HTML в RAG-индекс |
| `list` | Показать документы в индексе |
| `search <query>` | Проверить семантический поиск без MCP-сервера |
| `generate -d <discipline-id>` | Сгенерировать материалы для одной дисциплины |
| `generate-all` | Сгенерировать материалы для всех дисциплин |
| `clear-generated` | Удалить сгенерированные материалы из SQLite, ChromaDB и с диска |
| `delete` | Удалить один документ из индекса |

Опции команд:

| Команда | Опция | Что делает |
|---|---|---|
| `import` | `path` | Путь к документу: PDF, DOCX, TXT, MD или HTML |
| `import` | `--discipline-id`, `-d` | Привязать документ к дисциплине |
| `import` | `--title`, `-t` | Задать человекочитаемое название документа |
| `list` | `--discipline-id`, `-d` | Показать документы только одной дисциплины |
| `search` | `query` | Поисковый запрос |
| `search` | `--discipline-id`, `-d` | Искать только в документах одной дисциплины |
| `search` | `--limit`, `-n` | Количество результатов, по умолчанию `5` |
| `generate` | `--discipline-id`, `-d` | ID дисциплины, обязательная опция |
| `generate` | `--force` | Пересоздать файлы дисциплины |
| `generate` | `--model`, `-m` | Переопределить Ollama-модель для запуска |
| `generate-all` | `--force` | Пересоздать файлы всех дисциплин |
| `generate-all` | `--model`, `-m` | Переопределить Ollama-модель для запуска |
| `clear-generated` | `--discipline-id`, `-d` | Удалить генерацию только одной дисциплины |
| `delete` | `--path` | Удалить документ по точному исходному пути |
| `delete` | `--document-id` | Удалить документ по ID из `agent-ingest list` |

Загрузить документ в RAG:

```bash
# Загрузил лекции
uv run agent-ingest import ./lectures/lec01.pdf -d "cs-101" -t "Лекция 1: Введение"
uv run agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки"
uv run agent-ingest import ./textbook.docx -t "Учебник Алохи"

# Проверил что загрузилось
uv run agent-ingest list

# Протестировал поиск
uv run agent-ingest search "как работает быстрая сортировка" -n 3

# Если результат не устраивает
uv run agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки (исправленная)"

# Удаление
uv run agent-ingest delete --document-id e077bb64-31e6-4d37-98c6-d2f350d7a947
```

## Переменные окружения

`agent-ingest` при старте сам выставляет:

| Переменная | Значение | Зачем |
|---|---|---|
| `RAG_LOCAL_FILES_ONLY` | `1` | Не скачивать embedding-модель из сети при CLI-импорте |
| `HF_HUB_DISABLE_TELEMETRY` | `1` | Отключить telemetry Hugging Face |
| `TOKENIZERS_PARALLELISM` | `false` | Убрать лишний parallelism/warning tokenizer-библиотек |

Настройки базы и RAG:

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `DB_PATH` | `./university.db` | Путь к SQLite-базе |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | HF-id или локальный путь к embedding-модели |
| `RAG_EMBEDDING_BATCH_SIZE` | `64` | Размер батча для embeddings |
| `RAG_DEVICE` | `cpu` | Устройство для embeddings: `cpu`, `cuda`, `mps` |
| `RAG_CHUNKER_TYPE` | `semantic` | Тип чанкинга: `semantic`, `recursive`, `sentence` |
| `RAG_CHUNK_SIZE` | `512` | Целевой размер чанка |
| `RAG_CHUNK_OVERLAP` | `80` | Перекрытие чанков |
| `RAG_PAGE_OVERLAP_TOKENS` | `50` | Перекрытие текста между страницами |
| `CHROMA_PATH` | `./chroma_db` | Папка ChromaDB-индекса |
| `CHROMA_COLLECTION` | `university_documents` | Имя коллекции ChromaDB |
| `RAG_CONTEXT_MAX_TOKENS` | `8000` | Максимальный размер контекста для `get_rag_context` |

Настройки генерации материалов:

Генерация материалов требует локальную Ollama. Проверка доступности: `curl ${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags`.

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `DOCGEN_MODEL` | `qwen2.5:0.5b` | Ollama-модель для генерации текста |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Базовый адрес Ollama, если не задан `DOCGEN_OLLAMA_URL` |
| `DOCGEN_OLLAMA_URL` | `$OLLAMA_HOST/api/generate` | Полный endpoint Ollama generate API |
| `DOCGEN_NUM_PREDICT` | `4500` | Лимит генерируемых токенов |
| `DOCGEN_MAX_ATTEMPTS` | `2` | Количество попыток получить не слишком короткий ответ |
| `DOCGEN_MIN_RESPONSE_CHARS` | `120` | Минимальный желательный размер ответа модели |
| `DOCGEN_FAKE_SEED` | не задан | Seed для воспроизводимого Faker-каркаса |
| `DOCGEN_OUTPUT_DIR` | `./generated_materials` | Папка для PDF/DOCX-файлов |

Пример изолированного запуска:

```bash
DB_PATH=/tmp/agent-tutor.db \
CHROMA_PATH=/tmp/agent-tutor-chroma \
uv run agent-ingest list
```

Запустить через MCP Inspector для проверки инструментов:

```bash
uv run mcp dev server.py
```

В браузере выбрать транспорт **STDIO**. Если подключаешься вручную, укажи команду `uv`, аргументы `run python server.py`, затем нажми Connect.


### Пример запроса к модели:

```
Какие материалы доступны студенту с id "456c4e68-290a-4f8b-b0d0-545534adaf3e" по его дисциплинам?
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
- Сайт доступен для демонстрации
- Кстомный клиент на для сайта уже ведет логическое повествование и вызывает инструменты для решения задачи пользователя
