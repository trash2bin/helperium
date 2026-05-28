# Генерация документов

Генерирует учебные материалы через локальную Ollama-модель и сразу привязывает их к дисциплинам.
Генератор находится в `fixtures/document_generator.py` и запускается через CLI `agent-ingest`; MCP-сервер материалы не генерирует.

В разработке предпочтительно запускать CLI через проектное окружение:

```bash
uv run agent-ingest <command>
```

Если пакет установлен как tool, после изменений кода его нужно переустановить:

```bash
uv tool install . --reinstall
```

Для каждой дисциплины создаются файлы:

- `Лекция` в PDF
- `Методичка` в DOCX
- `Лабораторная работа` в DOCX

Файлы сохраняются в `generated_materials/`, метаданные попадают в SQLite `documents`, текстовые чанки в `document_chunks`, а векторы в `chroma_db/`.

## Требования

Проверка доступности Ollama:

```bash
curl ${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags
```

## Генерация всех дисциплин

```bash
uv run agent-ingest generate-all
```

Если нужно указать другую модель:

```bash
uv run agent-ingest generate-all --model "<model-name>"
```

## Перегенерация всех файлов

`--force` пересоздаёт материалы с теми же путями. Старые записи с такими путями заменяются, дубли не создаются.

```bash
uv run agent-ingest generate-all --force
```

## Генерация одной дисциплины

```bash
uv run agent-ingest generate -d "<discipline-id>"
```

Пересоздать одну дисциплину:

```bash
uv run agent-ingest generate -d "<discipline-id>" --force
```

## Полная очистка сгенерированных материалов

Удалить сгенерированные документы из SQLite, ChromaDB и с диска:

```bash
uv run agent-ingest clear-generated
```

Только для одной дисциплины:

```bash
uv run agent-ingest clear-generated -d "<discipline-id>"
```

## Ручная загрузка документа

Ручной импорт остался:

```bash
uv run agent-ingest import ./file.docx -d "<discipline-id>" -t "Название документа"
```

## Полезные настройки

Использовать другую Ollama-модель:

```bash
DOCGEN_MODEL=qwen2.5:3b uv run agent-ingest generate-all
```

Увеличить объём ответа модели:

```bash
DOCGEN_NUM_PREDICT=7000 uv run agent-ingest generate-all
```

Переопределить endpoint Ollama:

```bash
DOCGEN_OLLAMA_URL=http://IP:11434/api/generate uv run agent-ingest generate-all
```

Сохранить материалы в другую папку:

```bash
DOCGEN_OUTPUT_DIR=/tmp/agent-tutor-materials uv run agent-ingest generate-all
```

## Команды `agent-ingest`, связанные с генерацией

| Команда | Что делает |
|---|---|
| `uv run agent-ingest generate -d "<discipline-id>"` | Создаёт недостающие материалы одной дисциплины |
| `uv run agent-ingest generate -d "<discipline-id>" --force` | Полностью пересоздаёт материалы одной дисциплины |
| `uv run agent-ingest generate-all` | Создаёт недостающие материалы для всех дисциплин |
| `uv run agent-ingest generate-all --force` | Пересоздаёт материалы всех дисциплин |
| `uv run agent-ingest clear-generated` | Удаляет все сгенерированные материалы из SQLite, ChromaDB и с диска |
| `uv run agent-ingest clear-generated -d "<discipline-id>"` | Удаляет сгенерированные материалы одной дисциплины |

`--model` у `generate` и `generate-all` делает то же самое, что временный `DOCGEN_MODEL` для одного запуска.

## Переменные генератора

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `DOCGEN_MODEL` | `qwen2.5:0.5b` | Ollama-модель для генерации текста |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Базовый адрес Ollama |
| `DOCGEN_OLLAMA_URL` | `$OLLAMA_HOST/api/generate` | Полный endpoint Ollama generate API |
| `DOCGEN_NUM_PREDICT` | `4500` | Лимит генерируемых токенов |
| `DOCGEN_OUTPUT_DIR` | `./generated_materials` | Папка для созданных PDF/DOCX |
