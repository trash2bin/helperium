# OpenAPI-спецификации сервисов + JSON-конфиги

В `specs/` лежат **декларативные контракты** между сервисами. Каждый файл —
источник правды для своего аспекта системы.

## File layout

```
specs/
├── config.schema.json         # JSON Schema конфига data-service (runtime-валидация)
├── config.schema.md           # Человеко-читаемое руководство к схеме
├── config.example.json        # Пример конфига SQLite (для тестов, dev-запуска)
├── config.postgres.json       # Пример конфига PostgreSQL (production-шаблон)
├── api.openapi.yaml           # OpenAPI api-service сервера (порт 8081)
├── rag.openapi.yaml           # OpenAPI rag-сервиса (порт 8082)
├── fixtures/                  # seed.json для data-service --seed (.gitignore)
└── README.md
```

---

## config.schema.json — конфиг data-service

**Жизненно важный рантайм-артефакт.** Без него data-service не стартанёт:

- `helperium-go/config/loader.go` ищет схему по цепочке путей при загрузке
- `helperium-go/config/validate.go` валидирует каждый конфиг JSON Schema
- Тесты ищут `specs/config.schema.json` относительно CWD или бинарника

**Принцип:** схема — Source of Truth. Примеры (`config.example.json`,
`config.postgres.json`) должны быть валидны против схемы. Если схема
меняется — примеры обновляются синхронно.

### Как проверить примеры против схемы

```bash
# jsonschema — pip install jsonschema
uv run jsonschema -i specs/config.example.json specs/config.schema.json
uv run jsonschema -i specs/config.postgres.json specs/config.schema.json
```

---

## api.openapi.yaml / rag.openapi.yaml — OpenAPI контракты Python-сервисов

Оба спека **автоматически генерируются FastAPI** из Pydantic-моделей
и декораторов `@app.get/post`. Рабочий процесс:

```
FastAPI-код → app.openapi() → YAML spec → git commit
```

### Изменять spec вручную — НЕЛЬЗЯ

**Первичен код.** Spec — snapshot на момент последнего commit'а.
Добавил новый endpoint или поле в модели → прогнал тест → спека
обновилась сама:

```bash
# Тесты проверяют что код и spec совпадают (без запуска сервисов):
uv run pytest api-service/src/api_service/tests/unit/test_openapi_api.py  -v  # OpenAPI контракт api-service (см. api-service/README.md)
uv run pytest rag/tests/unit/test_openapi_spec.py      -v
```

Тест падает → обновляем spec:

```bash
# 1. Запустить сервис
# 2. Экспортировать схему
curl -s http://127.0.0.1:8081/openapi.json | yu -x . > specs/api.openapi.yaml
curl -s http://127.0.0.1:8082/openapi.json | yu -x . > specs/rag.openapi.yaml
```

> `yu` — утилита конвертации JSON → YAML. Альтернатива: `python3 -c "import yaml,json; yaml.dump(json.load(sys.stdin), sys.stdout)"`.

---

## Data-service OpenAPI — runtime-генерация

В отличие от Python-сервисов, data-service НЕ хранит OpenAPI spec-файл.
Схема генерируется runtime через `data-service/internal/openapigen/openapigen.go`
на основе загруженного конфига. Живая спека:

```bash
curl http://data-service:8084/openapi.json
```

**Это нормально.** Конфиг data-service описывает сущности клиента —
хранить статический spec бессмысленно, он разный для каждого tenant'а.

---

## Генерация клиента (на любом языке)

```bash
openapi-generator generate -i specs/rag.openapi.yaml -g python -o /tmp/rag-client
openapi-generator generate -i specs/api.openapi.yaml -g typescript -o /tmp/api-client
```
