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

**HTTP-роуты docs у api-service отключены по умолчанию.** С bd508c1 (control-plane hardening) FastAPI создаётся с `docs_url=None, redoc_url=None, openapi_url=None`, поэтому `/docs`, `/redoc` и `/openapi.json` отвечают 404. Это решение, а не настройка, потерявшаяся по умолчанию: схема API не должна быть публично доступна без аутентификации. Спецификацию смотреть в `specs/api.openapi.yaml`; она генерируется программно (`app.openapi()`) и сверяется drift-тестом выше.

Для локальной отладки можно включить роуты переменной `API_ENABLE_DOCS=1|true|yes|on` (см. таблицу env в `services/api-service/README.md`): тогда `/docs`, `/redoc` и `/openapi.json` отдаются **без аутентификации**. На публичных окружениях флаг включать нельзя. Регрессионные тесты флага: `services/api-service/src/api_service/tests/unit/test_api_docs_flag.py`.

---
**Last verified:** 2026-09-06 (working tree, HEAD `93fa78d`) — docs-роуты отключены по умолчанию, флаг `API_ENABLE_DOCS` покрыт тестами
