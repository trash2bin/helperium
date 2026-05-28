# agent-tutor

MCP-сервер для университетского ассистента на базе LLM. Даёт языковой модели доступ к данным об учебном процессе через набор инструментов — студенты, расписание, дисциплины, учебные материалы.

## Что умеет

Модель может вызывать инструменты:

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
| `import_document(path, discipline_id?, title?)` | Импорт документа в локальный RAG-индекс |
| `list_documents(discipline_id?)` | Список документов в RAG-индексе |
| `search_documents(query, discipline_id?, limit?)` | Поиск релевантных фрагментов документов |
| `get_rag_context(query, discipline_id?, limit?)` | Готовый контекст для ответа по документам |

## Стек

- Python 3.12+
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — транспортный слой MCP
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
│   └── models.py       # Pydantic-модели
├── tools/
│   ├── student.py      # StudentTools
│   ├── disciplines.py  # DisciplineTools
│   └── rag.py          # Импорт документов, чанкинг, embeddings, retrieval
├── fixtures/
│   ├── generate.py     # Генератор тестовых данных
│   ├── ingest.py       # CLI agent-ingest для RAG-документов и генерации материалов
│   └── document_generator.py # Генерация PDF/DOCX-материалов для фикстур
└── fixtures.json       # Тестовые данные
```

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

- `agent-tutor` — MCP-сервер из `server.py`.
- `agent-ingest` — CLI для импорта, поиска, удаления и генерации документов.

Синхронизировать зависимости в локальное окружение `.venv`:

```bash
uv sync
```

Запускать команды без активации окружения:

```bash
uv run agent-ingest --help
uv run agent-tutor
```

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

`agent-ingest` находится в `fixtures/ingest.py`. Он работает с локальной SQLite-базой, ChromaDB-индексом и генератором учебных материалов.

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

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `DOCGEN_MODEL` | `qwen2.5:0.5b` | Ollama-модель для генерации текста |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Базовый адрес Ollama, если не задан `DOCGEN_OLLAMA_URL` |
| `DOCGEN_OLLAMA_URL` | `$OLLAMA_HOST/api/generate` | Полный endpoint Ollama generate API |
| `DOCGEN_NUM_PREDICT` | `4500` | Лимит генерируемых токенов |
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
- Проверено в MCP Inspector и Goose, Cluade-code, Pi
