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

## Виртуальное окружение

- `uv sync` создаёт/обновляет `.venv`.
- `uv venv --python 3.12` явно создаёт окружение на Python 3.12.
- `source .venv/bin/activate` активирует окружение в shell.
- `deactivate` выходит из активированного окружения.
- `rm -rf .venv && uv sync` пересоздаёт окружение с нуля.

## Документы и RAG

- `agent-ingest import <path>` импортирует документы в SQLite + ChromaDB.
- `agent-ingest list` показывает документы.
- `agent-ingest search <query>` проверяет поиск без MCP-сервера.
- `agent-ingest delete --document-id <id>` или `agent-ingest delete --path <path>` удаляет документ.
- `agent-ingest` принудительно выставляет `RAG_LOCAL_FILES_ONLY=1`, поэтому embedding-модель должна быть в локальном кэше или задана локальным путём через `RAG_EMBEDDING_MODEL`.

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

Более полный справочник команд и переменных находится в `README.md` и `GENERATION.md`.

## Осторожность

- Не удаляй `university.db`, `chroma_db/` или `generated_materials/`, если задача явно этого не требует.
- В рабочем дереве могут быть пользовательские изменения. Не откатывай их без прямой просьбы.
- Не коммить изменения без прямой просьбы пользователя.
