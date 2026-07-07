# RAG Service

Сервис поиска по документам (ChromaDB), чанкинг, эмбеддинги.

## Роль в системе

`rag` — векторный поиск по документам:
- Импорт документов (PDF, TXT, MD, DOCX)
- Чанкинг (рекурсивный / sentence-based)
- Эмбеддинги (sentence-transformers)
- Векторное хранение (ChromaDB)
- Семантический поиск + контекст для LLM

## Эндпоинты

| Путь | Метод | Описание |
|---|---|---|
| `/health` | GET | Статус сервиса (SQLite + ChromaDB + embedding model) |
| `/search` | POST | Семантический поиск |
| `/context` | POST | Готовый контекст для LLM |
| `/documents/list` | POST | Список документов с фильтром |
| `/documents/import` | POST | Импорт документа/директории по пути |
| `/documents/upload` | POST | Загрузка файла (multipart/form-data) + импорт |
| `/documents/delete` | POST | Идемпотентное удаление документа по ID или пути |

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `RAG_HOST` | `127.0.0.1` | Хост для бинда HTTP сервера |
| `RAG_PORT` | `8082` | Порт |
| `RAG_HTTP_TIMEOUT` | `60` | Таймаут HTTP-запросов к RAG (секунды) |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Директория ChromaDB |
| `CHROMA_COLLECTION` | `documents` | Имя коллекции ChromaDB |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Модель эмбеддингов |
| `RAG_EMBEDDING_BATCH_SIZE` | `64` | Batch size для эмбеддингов |
| `RAG_DEVICE` | `cpu` | `cpu` / `cuda` / `mps` |
| `RAG_LOCAL_FILES_ONLY` | `0` | Только локальные файлы модели (не скачивать) |
| `RAG_CHUNKER_TYPE` | `semantic` | Стратегия чанкинга: semantic, recursive, sentence |
| `RAG_CHUNK_SIZE` | `512` | Размер чанка |
| `RAG_CHUNK_OVERLAP` | `80` | Перекрытие чанков |
| `RAG_PAGE_OVERLAP_TOKENS` | `50` | Перекрытие между страницами (токены) |
| `RAG_CONTEXT_MAX_TOKENS` | `8000` | Макс. токенов в собранном контексте |
| `RAG_DB_PATH` | — | Путь к SQLite БД метаданных (по умолчанию рядом с chroma_db) |

## Запуск

```bash
# Из корня проекта
cd /project/root
uv run python -m rag.service

# Или через Docker
docker compose up -d rag
```

## Тестирование

```bash
uv run pytest rag/tests/ -v   # 51 тест
```

---

## 🔧 Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `ChromaDB: collection not found` | Не импортированы документы | `curl -X POST http://127.0.0.1:8082/documents/import -d '{"path":"./docs"}'` |
| `Embedding model not found` / OOM | Недостаточно RAM / не та модель | Меньшая модель: `EMBEDDING_MODEL=intfloat/multilingual-e5-small` |
| `CUDA out of memory` | GPU память переполнена | `EMBEDDING_DEVICE=cpu` или уменьшите `CHUNK_SIZE` |
| `sqlite3.OperationalError: database is locked` | ChromaDB.lock от прошлого запуска | `pkill -f rag.service && rm -f chroma_db/*.lock` |
| Поиск возвращает пусто / нерелевантно | Нет документов / плохой chunking | Проверить `/documents/list`, настроить `CHUNK_SIZE/OVERLAP` |
| 500 на `/search` | ChromaDB не запущен / путь неверен | `ls -la chroma_db/`, проверить `CHROMA_PERSIST_DIR` |

### Быстрый smoke-тест
```bash
# 1. Health
curl -s http://127.0.0.1:8082/health

# 2. Импорт тестового документа
curl -s -X POST http://127.0.0.1:8082/documents/import \
  -H "Content-Type: application/json" \
  -d '{"path": "./specs/fixtures"}'

# 3. Поиск
curl -s -X POST http://127.0.0.1:8082/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "limit": 3}' | jq .

# 4. Контекст для LLM
curl -s -X POST http://127.0.0.1:8082/context \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "limit": 5}' | jq .
```

### Логи
- Ручное запуск: stdout/stderr терминала
- Через `dev.sh`: `.data/logs/rag.log`
