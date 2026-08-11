# API контракты и specs/

См. [specs/README.md](../../specs/README.md) — полное описание.

```
specs/
├── config.example.json       # Пример конфига (SQLite)
├── config.postgres.json      # Пример конфига (PostgreSQL)
├── api.openapi.yaml          # OpenAPI api-service (автогенерация из FastAPI)
└── rag.openapi.yaml          # OpenAPI rag (автогенерация из FastAPI)
```

**Валидация конфига** — в `services/helperium-go/config/types.go` (метод `Config.Validate()`), не во внешнем schema.
**OpenAPI specs** — слепки автогенерации. Первичен код. Тесты ловят рассинхрон:
```bash
uv run pytest services/api-service/src/api_service/tests/unit/test_openapi_api.py
uv run pytest services/rag/tests/unit/test_openapi_spec.py
```
---
**Last verified:** 2026-08-09 (HEAD `be9a991`) — спецификации сверены с кодом генерации
