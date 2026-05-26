# agent-tutor

MCP-сервер для университетского ассистента на базе LLM. Даёт языковой модели доступ к данным об учебном процессе через набор инструментов — студенты, расписание, дисциплины, учебные материалы.

## Что умеет

Модель может вызывать семь инструментов:

| Инструмент | Что делает |
|---|---|
| `get_student(student_id)` | Карточка студента |
| `find_student_by_name(name)` | Поиск студента по ФИО |
| `get_schedule(group_id, day?)` | Расписание группы, опционально по дню |
| `get_disciplines(student_id)` | Дисциплины студента через его группу |
| `get_materials(discipline_id, type?)` | Учебные материалы по дисциплине |
| `search_materials(query, discipline_id?)` | Поиск по содержимому материалов |
| `get_student_grades(student_id, discipline_id?)` | Оценки студента, опционально по одной дисциплине |

## Стек

- Python 3.12+
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — транспортный слой MCP
- SQLite — локальное хранилище
- Pydantic — схемы ответов
- Faker — генерация тестовых данных

## Структура

```
agent-tutor/
├── server.py           # MCP-сервер, точка входа
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
- MCP-сервер стартует и публикует все семь инструментов
- SQLite-база инициализируется и загружает фикстуры при старте
- Инструменты возвращают типизированные Pydantic-ответы
- Проверено в MCP Inspector и Goose, Cluade-code, Pi
