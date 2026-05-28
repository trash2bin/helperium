# Генерация документов

Генерирует учебные материалы через локальную Ollama-модель и сразу привязывает их к дисциплинам.

Для каждой дисциплины создаются файлы:

- `Лекция` в PDF
- `Методичка` в DOCX
- `Лабораторная работа` в DOCX

Файлы сохраняются в `generated_materials/`, метаданные попадают в SQLite `documents`, текстовые чанки в `document_chunks`, а векторы в `chroma_db/`.

## Требования

Проверка доступности Ollama:

```bash
curl $OLLAMA_HOST/api/tags
```

## Генерация всех дисциплин

```bash
agent-ingest generate-all
```

Если нужно указать другую модель:

```bash
agent-ingest generate-all --model "<model-name>"
```

## Перегенерация всех файлов

`--force` пересоздаёт материалы с теми же путями. Старые записи с такими путями заменяются, дубли не создаются.

```bash
agent-ingest generate-all --force
```

## Генерация одной дисциплины

```bash
agent-ingest generate -d "<discipline-id>"
```

Пересоздать одну дисциплину:

```bash
agent-ingest generate -d "<discipline-id>" --force
```

## Полная очистка сгенерированных материалов

Удалить сгенерированные документы из SQLite, ChromaDB и с диска:

```bash
agent-ingest clear-generated
```

Только для одной дисциплины:

```bash
agent-ingest clear-generated -d "<discipline-id>"
```

## Ручная загрузка документа

Ручной импорт остался:

```bash
agent-ingest import ./file.docx -d "<discipline-id>" -t "Название документа"
```

## Полезные настройки

Увеличить объём ответа модели:

```bash
DOCGEN_NUM_PREDICT=7000 agent-ingest generate-all
```

Переопределить endpoint Ollama:

```bash
DOCGEN_OLLAMA_URL=http://IP:11434/api/generate agent-ingest generate-all
```
