# CLI Утилиты agent-tutor

Документация по командным утилитам для работы с RAG-системой и генерацией учебных материалов.

## Обзор

CLI-утилиты были выделены в отдельные entrypoint-ы и работают как **one-shot команды** через HTTP с сервисом `rag`.

## Сервисы и их назначение

| Утилита | EntryPoint | Назначение |
|---|---|---|
| `agent-ingest` | `fixtures.ingest:main` | Управление документами RAG (импорт, поиск, удаление) |
| `agent-generate` | `fixtures.agent_generate:main` | Генерация учебных материалов (PDF/DOCX) |

## agent-ingest — Управление RAG-документами

Работает через HTTP-клиент к сервису `rag`. Все операции синхронные.

### Базовые переменные окружения

```bash
# Обязательно для работы с RAG-сервисом
RAG_SERVICE_URL=http://127.0.0.1:8082

# Опционально (устанавливаются автоматически в утилите)
RAG_LOCAL_FILES_ONLY=1      # Только локальные файлы
HF_HUB_DISABLE_TELEMETRY=1  # Отключить телеметрию HuggingFace
TOKENIZERS_PARALLELISM=false # Отключить параллелизм токенайзеров
```

### Команды

#### `import` — Импорт документа в индекс

```bash
uv run agent-ingest import ПУТЬ_К_ФАЙЛУ [ОПЦИИ]
```

**Аргументы:**
- `path` (обязательно) — путь к файлу для импорта

**Опции:**
- `-d, --discipline-id ID` — ID дисциплины для привязки документа
- `-t, --title ЗАГОЛОВОК` — название документа (если не указано, берётся из файла)

**Примеры:**
```bash
# Импорт с автоопределением дисциплины
uv run agent-ingest import documents/lecture.pdf

# Импорт с указанием дисциплины
uv run agent-ingest import documents/algorithms.pdf -d "алгоритмы-и-структуры-данных" \
  -t "Лекция по алгоритмам"
```

**Вывод:**
```
  done — 15 chunks, 2.3s
```

**Ошибки:**
- `FileNotFoundError` — файл не найден
- `ValueError` — неVALIDный файл или дисциплина

---

#### `list` — Список заиндексированных документов

```bash
uv run agent-ingest list [ОПЦИИ]
```

**Опции:**
- `-d, --discipline-id ID` — фильтр по ID дисциплины

**Примеры:**
```bash
# Все документы
uv run agent-ingest list

# Документы по конкретной дисциплине
uv run agent-ingest list -d "базы-данных"
```

**Вывод:**
```
  abc123  Лекция по БД  (application/pdf)  /path/to/file.pdf
  def456  Методичка  (application/pdf)  /path/to/guide.pdf

Всего: 2
```

---

#### `search` — Поиск по документам

```bash
uv run agent-ingest search ЗАПРОС [ОПЦИИ]
```

**Аргументы:**
- `query` (обязательно) — поисковый запрос

**Опции:**
- `-d, --discipline-id ID` — фильтр по дисциплине
- `-n, --limit N` — максимальное количество результатов (дефолт: 5)

**Примеры:**
```bash
# Поиск по всем документам
uv run agent-ingest search "асимптотическая сложность"

# Поиск с фильтром и лимитом
uv run agent-ingest search "SQL запрос" -d "базы-данных" -n 3
```

**Вывод:**
```
--- [1] score=0.9123  Лекция по алгоритмам  стр.5 ---
Асимптотическая сложность алгоритма определяется как...[усечено до 500 символов]

--- [2] score=0.8765  Методичка по БД  без стр. ---
Введение в реляционную модель данных...

Всего: 2 результата
```

---

#### `delete` — Удаление документа

```bash
uv run agent-ingest delete [ОПЦИИ]
```

**Опции (одна из них обязательна):**
- `--path ПУТЬ` — удалить по пути к файлу
- `--document-id ID` — удалить по ID документа в базе

**Примеры:**
```bash
# Удаление по пути
uv run agent-ingest delete --path /data/documents/old.pdf

# Удаление по ID
uv run agent-ingest delete --document-id abc123
```

**Вывод:**
```
OK  удалён: Лекция по алгоритмам (abc123)
```

**Примечание:** Удаляет как запись в ChromaDB, так и файл с диска (если существует).

---

#### `clear-generated` — Очистка сгенерированных материалов

```bash
uv run agent-ingest clear-generated [ОПЦИИ]
```

**Опции:**
- `-d, --discipline-id ID` — очистить материалы только для указанной дисциплины

**Примеры:**
```bash
# Очистить все сгенерированные материалы
uv run agent-ingest clear-generated

# Очистить материалы по дисциплине
uv run agent-ingest clear-generated -d "машинное-обучение"
```

**Что делает:**
- Удаляет документы из `generated_materials/` в ChromaDB
- Удаляет соответствующие файлы с диска
- Очищает пустые директории в `generated_materials/`

**Вывод:**
```
Удалено документов: 3
```

---

## agent-generate — Генерация учебных материалов

Генерирует PDF/DOCX материалы для дисциплин и автоматически добавляет их в RAG-индекс.

### Базовые переменные окружения

```bash
# Модель Ollama для генерации (дефолт: qwen2.5:0.5b)
DOCGEN_MODEL=qwen2.5:0.5b

# URL Ollama API (дефолт: http://127.0.0.1:11434/api/generate)
DOCGEN_OLLAMA_URL=http://localhost:11434/api/generate

# Параметры генерации
DOCGEN_NUM_PREDICT=4500        # Количество токенов (дефолт: 4500)
DOCGEN_TEMPERATURE=1.0         # Температура (дефолт: 1.0)
DOCGEN_TIMEOUT=3600           # Таймаут в секундах (дефолт: 3600)
DOCGEN_MIN_RESPONSE_CHARS=120 # Минимальная длина ответа (дефолт: 120)
DOCGEN_MAX_ATTEMPTS=2         # Максимальное количество попыток (дефолт: 2)

# Директория вывода (дефолт: ./generated_materials)
DOCGEN_OUTPUT_DIR=./generated_materials

# Seed для Faker (для воспроизводимости)
DOCGEN_FAKE_SEED=42
```

### Типы генерируемых материалов

| Тип | Формат | Объём (слов) | Назначение |
|---|---|---|---|
| Лекция | PDF | 650-1000 | Теоретический материал |
| Методичка | DOCX | 500-850 | Практическое руководство |
| Лабораторная работа | DOCX | 450-750 | Лабораторный практикум |

### Команды

#### `generate` — Генерация для одной дисциплины

```bash
uv run agent-generate generate -d ID_ДИСЦИПЛИНЫ [ОПЦИИ]
```

**Опции:**
- `-d, --discipline-id ID` (обязательно) — ID дисциплины
- `--force` — пересоздать существующие материалы
- `-m, --model МОДЕЛЬ` — модель Ollama для генерации

**Примеры:**
```bash
# Генерация материалов для дисциплины
uv run agent-generate generate -d "алгоритмы-и-структуры-данных"

# Принудительная перегенерация
uv run agent-generate generate -d "базы-данных" --force

# С другой моделью
uv run agent-generate generate -d "веб-технологии" -m "llama3.2:3b"
```

**Вывод:**
```
  Лекция: лекция_алгоритмы_и_структуры_данных.pdf  /path/to/generated_materials/.../лекция_алгоритмы.pdf
  Методичка: методичка_алгоритмы_и_структуры_данных.docx  /path/to/.../методичка_алгоритмы.docx
  Лабораторная работа: лабораторная_работа_алгоритмы_и_структуры_данных.docx  /path/to/.../лабораторная_работа.docx

Всего: 3
```

**Примечания:**
- Если материалы уже существуют и `--force` не указан, возвращает существующие
- Автоматически добавляет сгенерированные файлы в RAG-индекс
- Создаёт директорию дисциплины в `DOCGEN_OUTPUT_DIR`

---

#### `generate-all` — Генерация для всех дисциплин

```bash
uv run agent-generate generate-all [ОПЦИИ]
```

**Опции:**
- `--force` — пересоздать все материалы
- `-m, --model МОДЕЛЬ` — модель Ollama для генерации

**Примеры:**
```bash
# Генерация для всех дисциплин
uv run agent-generate generate-all

# Полная перегенерация всех материалов
uv run agent-generate generate-all --force -m "mistral:7b"
```

**Вывод:**
```
[1/10] Алгоритмы и структуры данных
  Лекция: лекция_алгоритмы.pdf
  Методичка: методичка_алгоритмы.docx
  Лабораторная работа: лабораторная_работа_алгоритмы.docx
[2/10] Базы данных
  Лекция: лекция_базы_данных.pdf
  ...

Готово. Дисциплин: 10, файлов в базе: 30
```

---

## Переменные окружения — полный список

### Для agent-ingest

| Переменная | Дефолт | Описание |
|---|---|---|
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | URL RAG-сервиса |
| `RAG_LOCAL_FILES_ONLY` | `1` | Только локальные файлы (отключает загрузку из сети) |
| `HF_HUB_DISABLE_TELEMETRY` | `1` | Отключить телеметрию HuggingFace |
| `TOKENIZERS_PARALLELISM` | `false` | Отключить параллелизм токенайзеров |

### Для agent-generate

| Переменная | Дефолт | Описание |
|---|---|---|
| `DOCGEN_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `DOCGEN_OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | URL Ollama API |
| `DOCGEN_NUM_PREDICT` | `4500` | Максимальное количество токенов в ответе |
| `DOCGEN_TEMPERATURE` | `1.0` | Температура генерации |
| `DOCGEN_TIMEOUT` | `3600` | Таймаут запроса в секундах |
| `DOCGEN_MIN_RESPONSE_CHARS` | `120` | Минимальная длина ответа в символах |
| `DOCGEN_MAX_ATTEMPTS` | `2` | Максимальное количество попыток генерации |
| `DOCGEN_OUTPUT_DIR` | `./generated_materials` | Директория для сгенерированных файлов |
| `DOCGEN_FAKE_SEED` | `None` | Seed для Faker (для воспроизводимости тестовых данных) |

---

## Типовые сценарии

### Полный цикл: генерация + импорт

```bash
# 1. Сгенерировать материалы для дисциплины
uv run agent-generate generate -d "машинное-обучение" --force

# 2. Проверить, что файлы появились
ls generated_materials/машинное_обучение/

# 3. Документы автоматически добавлены в RAG-индекс
uv run agent-ingest list -d "машинное-обучение"

# 4. Протестировать поиск
uv run agent-ingest search "нейронные сети" -d "машинное-обучение"
```

### Массовая генерация для всех дисциплин

```bash
# Сгенерировать всё (займёт 30-60 минут в зависимости от модели и железа)
uv run agent-generate generate-all

# Проверить результат
uv run agent-ingest list
```

### Очистка и перегенерация

```bash
# Удалить все сгенерированные материалы
uv run agent-ingest clear-generated

# Пересоздать для одной дисциплины
uv run agent-generate generate -d "искусственный-интеллект" --force
```

---

## Архитектурные особенности

### One-shot принцип

Обе утилиты (`agent-ingest` и `agent-generate`) проектированы как **one-shot команды**:
- Запускаются, выполняют задачу, завершаются
- Не требую long-running процессов
- Не хранят состояние между запусками

### Зависимости от сервисов

```
agent-ingest  →  HTTP  →  rag-сервис (порт 8082)
                     ↘️  ChromaDB
                     ↘️  SQLite (university.db)

agent-generate  →  Ollama API (порт 11434)
       ↘️  DOCGEN_OUTPUT_DIR
       ↘️  agent-ingest (для импорта в RAG)
```

### Изоляция от MCP-сервера

В соответствии с **Этапом 0.5**:
- `mcp_server/` НЕ содержит зависимостей на генерацию документов
- `mcp_server/` НЕ содержит Ollama-клиент
- Все зависимости на генерацию вынесены в `fixtures/`

---

## Проверка работоспособности (Этап 0.5)

Для подтверждения выполнения этапа 0.5 ROADMAP:

```bash
# 1. agent-ingest работает через HTTP к rag
uv run agent-ingest list

# 2. agent-generate создаёт артефакты и импортирует их в rag
uv run agent-generate generate -d "алгоритмы-и-структуры-данных"
uv run agent-ingest list -d "алгоритмы-и-структуры-данных"

# 3. Все сервисы поднимаются независимо
python -m rag.service        # RAG-сервис
python -m mcp_server.server  # MCP-сервер
python -m demo.api.server     # API-сервис
python -m demo.web.server     # Web-сервис
```

---

## Устранение неполадок

### "RAG-сервис недоступен"

```bash
# Проверить, что RAG-сервис запущен
curl http://127.0.0.1:8082/health

# Запустить RAG-сервис
uv run python -m rag.service
```

### "Ollama недоступен"

```bash
# Проверить, что Ollama запущен
curl http://127.0.0.1:11434/api/tags

# Запустить Ollama
ollama serve

# Убедиться, что модель установлена
ollama pull qwen2.5:0.5b
```

### "Дисциплина не найдена"

```bash
# Проверить список дисциплин в БД
sqlite3 university.db "SELECT id, name FROM disciplines;"

# Сгенерировать фикстуры, если БД пустая
python -m fixtures.generate
```

### Генерация прерывается с ошибкой

```bash
# Увеличить таймаут и количество попыток
DOCGEN_TIMEOUT=7200 DOCGEN_MAX_ATTEMPTS=3 uv run agent-generate generate -d ID

# Использовать более надёжную модель
DOCGEN_MODEL=llama3.2:3b uv run agent-generate generate -d ID
```

---

## Структура директории fixtures/

```
fixtures/
├── __init__.py           # Пустой, для импорта
├── README.md             # Этот файл
├── ingest.py             # CLI: agent-ingest (import/list/search/delete/clear-generated)
├── agent_generate.py     # CLI: agent-generate (generate/generate-all)
├── document_generator.py # Логика генерации документов (Ollama + DOCX/PDF)
├── generate.py           # Генерация фикстурных данных (для fixtures.json)
└── catalog.py            # Каталоги и справочники (дисциплины, специальности, темы)
```

---

## Связь с ROADMAP

Эта документация покрывает **Этап 0.5**: "Выделить CLI-утилиты `agent-ingest` и `agent-generate`".

- ✅ `agent-ingest import/list/search/delete` работает через HTTP к `rag`
- ✅ `agent-generate` вынесен в отдельный entrypoint
- ✅ Генерация материалов отделена от поиска и чата
- ✅ В `mcp` не осталось зависимостей на генерацию документов и Ollama-клиент
- ✅ Все сервисы (`mcp`, `api`, `web`, `rag`) поднимаются независимо
