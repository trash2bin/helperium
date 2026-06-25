# OpenAPI-спецификации сервисов

Этот каталог содержит OpenAPI-спецификации всех HTTP-сервисов проекта agent-tutor.

## File layout

```
specs/
├── rag.openapi.{json,yaml}   # RAG-сервис (порт 8082)
├── api.openapi.{json,yaml}   # API-сервер с агентом (порт 8081)
├── schemas/                   # JSON Schema для Go data-service (генерируется из моделей)
├── fixtures/                  # seed.json для data-service --seed (регенерируется, .gitignore)
└── README.md
```

## Принцип

Spec — **первичен**. Изменение API начинается с правки spec, затем реализация в коде.
CI проверяет соответствие:

```bash
diff <(curl -s http://rag:8082/openapi.json) <(yq -o json specs/rag.openapi.yaml)
diff <(curl -s http://api:8081/openapi.json) <(yq -o json specs/api.openapi.yaml)
```

Если не совпало — CI падает.

## Как обновить spec

```bash
# Экспорт из работающего сервиса
curl -s http://127.0.0.1:8082/openapi.json | python3 -m yaml > specs/rag.openapi.yaml
curl -s http://127.0.0.1:8081/openapi.json | python3 -m yaml > specs/api.openapi.yaml
```

Или из Python:

```python
import json, yaml
from rag.service import app
with open("specs/rag.openapi.yaml", "w") as f:
    yaml.dump(app.openapi(), f, allow_unicode=True, sort_keys=False)
```

## Генерация клиента (на любом языке)

```bash
# Установи openapi-generator (Java) или oapi-codegen (Go)
openapi-generator generate -i specs/rag.openapi.yaml -g python -o /tmp/rag-client
openapi-generator generate -i specs/api.openapi.yaml -g typescript -o /tmp/api-client
```
