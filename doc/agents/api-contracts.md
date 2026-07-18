# API контракты и specs/

См. [specs/README.md](../../specs/README.md) — полное описание.

```
specs/
├── config.example.json       # Пример конфига (SQLite)
├── config.postgres.json      # Пример конфига (PostgreSQL)
├── api.openapi.yaml          # OpenAPI api-service (автогенерация из FastAPI)
└── rag.openapi.yaml          # OpenAPI rag (автогенерация из FastAPI)
```

**Валидация конфига** — в `helperium-go/config/types.go` (метод `Config.Validate()`), не во внешнем schema.
**OpenAPI specs** — слепки автогенерации. Первичен код. Тесты ловят рассинхрон:
```bash
uv run pytest api-service/src/api_service/tests/unit/test_openapi_api.py
uv run pytest rag/tests/unit/test_openapi_spec.py
```
