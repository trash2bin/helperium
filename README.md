# agent-tutor

MCP-сервер для университетского ассистента на базе LLM. Даёт языковой модели доступ к данным об учебном процессе через набор инструментов — студенты, расписание, дисциплины, учебные материалы.

## Что умеет

Модель может вызывать пять инструментов:

| Инструмент | Что делает |
|---|---|
| `get_student(student_id)` | Карточка студента |
| `get_schedule(group_id, day?)` | Расписание группы, опционально по дню |
| `get_disciplines(student_id)` | Дисциплины студента через его группу |
| `get_materials(discipline_id, type?)` | Учебные материалы по дисциплине |
| `search_materials(query, discipline_id?)` | Поиск по содержимому материалов |

## Стек

- Python 3.10+
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — транспортный слой MCP
- SQLite — локальное хранилище
- Pydantic — схемы ответов

## Структура

```
agent-tutor/
├── server.py           # MCP-сервер, точка входа
├── main.py             # REST-заглушка на FastAPI (отдельно от MCP)
├── db/
│   ├── database.py     # SQLite, создание таблиц, загрузка фикстур
│   └── models.py       # Pydantic-модели
├── tools/
│   ├── student.py      # StudentTools
│   └── disciplines.py  # DisciplineTools
├── fixtures/
│   └── generate.py     # Генератор тестовых данных
└── fixtures.json       # Тестовые данные
```

## Быстрый старт

```bash
git clone https://github.com/ivan/agent-tutor
cd agent-tutor

uv sync
uv tool install .
```

Сгенерировать тестовые данные если `fixtures.json` ещё нет:

```bash
python fixtures/generate.py
```

Запустить через MCP Inspector для проверки инструментов:

```bash
mcp dev server.py
```

В браузере выбрать транспорт **STDIO**, команда `python`, аргумент `server.py`, нажать Connect.


Пример запроса к модели:

```
Какие материалы доступны студенту с id "456c4e68-290a-4f8b-b0d0-545534adaf3e" по его дисциплинам?
```

## Текущее состояние

Проект находится на стадии рабочего прототипа.

Работает:
- MCP-сервер стартует и публикует все пять инструментов
- SQLite-база инициализируется и загружает фикстуры при старте
- Инструменты возвращают типизированные Pydantic-ответы
- Проверено в MCP Inspector и Goose

Не реализовано ещё:
- `generate_course_plan` — есть схема `CoursePlan`, инструмента нет
- Нормальный поиск — сейчас `LIKE '%query%'` без ранжирования
- `get_exam_topics`, `get_teacher_info`, `get_university_news`
- Структурированные ошибки (`StudentNotFound` и т.д.)
- Конфигурация через env, сейчас `DB_PATH` зашит в `server.py`

## Известные ограничения

Параметр `day` в `get_schedule` фильтрует по названию дня недели (`"Понедельник"`), а не по номеру недели семестра — несмотря на историческое название `week` в коде.

Дисциплины студента вычисляются через его группу и расписание, а не через прямую связь. Если дисциплина есть в учебном плане, но временно не стоит в расписании — она не вернётся.
