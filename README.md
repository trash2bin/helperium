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
| `get_materials(discipline_id, type?)` | Список файлов по дисциплине; при первом вызове генерирует их локально |
| `generate_materials(discipline_id, force?)` | Явно сгенерировать или пересоздать материалы дисциплины |
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
- python-docx — генерация DOCX
- встроенный PDF-генератор — генерация PDF-лекций без внешних утилит
- Sentence Transformers — локальная embedding-модель
- ChromaDB — локальная векторная база для RAG-поиска
- Ollama — локальная генерация текста материалов

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
│   ├── document_generator.py # Генерация PDF/DOCX-материалов
│   └── rag.py          # Импорт документов, чанкинг, embeddings, retrieval
├── fixtures/
│   └── generate.py     # Генератор тестовых данных
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

При первом импорте документа модель скачивается из Hugging Face. Если модель уже скачана или указан локальный путь, можно запретить сетевые обращения:

```bash
RAG_LOCAL_FILES_ONLY=1
RAG_DEVICE=cuda
```

Пример:

```bash
$ agent-ingest import ~/Documents/test.pdf
```

```text
Найди задание под номером 11 в Методичка по базам данных
```

## Быстрый старт

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
```

Усли необходима тестовая база данных (`fixtures.json`):
```bash
python fixtures/generate.py
```

```
uv sync
uv tool install .
```

Пересобрать пакет после изменения кода:

```bash
uv tool install . --reinstall
```

Загрузить документ в RAG:

```bash
# Загрузил лекции
agent-ingest import ./lectures/lec01.pdf -d "cs-101" -t "Лекция 1: Введение"
agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки"
agent-ingest import ./textbook.docx -t "Учебник Алохи"

# Проверил что загрузилось
agent-ingest list

# Протестировал поиск
agent-ingest search "как работает быстрая сортировка" -n 3

# Если результат не устраивает
agent-ingest import ./lectures/lec02.pdf -d "cs-101" -t "Лекция 2: Сортировки (исправленная)"

# Удаление
agent-ingest delete --document-id e077bb64-31e6-4d37-98c6-d2f350d7a947
```

Запустить через MCP Inspector для проверки инструментов:

```bash
mcp dev server.py
```

В браузере выбрать транспорт **STDIO**, команда `python`, аргумент `server.py`, нажать Connect.


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
