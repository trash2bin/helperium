# E2E тесты (tests/e2e/) — CI-ready

Модульные e2e тесты для всех сервисов helperium. Используют Python seedgen
для генерации тестовых SQLite БД — **не требуют Go компилятора, внешних БД
или реального LLM**. Готовы к запуску в CI.

## Запуск

```bash
# Все e2e (без LLM) — 131 тест, ~2-3 мин
uv run pytest tests/e2e/ -v

# Без traceback
uv run pytest tests/e2e/ --no-traceback
```

Требуют все 6 сервисов (data-service :8084, mcp-gateway :8083, api-service :8081,
demo-web :8080, rag :8082, admin-dashboard :8085).

**Как поднять сервисы локально:**
```bash
# Secure E2E profile изолирован от .env: повышает limiter, включает gateway
# bearer-auth и Origin allowlist. Обычный `start` сохраняет production-like
# 10 RPS / burst 20 и не подменяет локальную security-конфигурацию.
./infra/scripts/dev.sh e2e-up

# Полный suite или один product-flow файл.
./infra/scripts/dev.sh e2e
./infra/scripts/dev.sh e2e services/agent-db/tests/e2e/test_product_readiness_paths.py -q
```

## Структура

```
tests/
├── e2e/                          # ← этот каталог: CI-ready, без LLM
│   ├── conftest.py               # .env load, health-check, CLI args
│   ├── helpers.py                # seed_database, register_tenant, mcp_call, admin_headers
│   ├── README.md
│   ├── test_admin_lifecycle.py   # 11 тестов: CRUD, stats, duplicate 409, delete, persistence
│   ├── test_agents.py            # 10 тестов: agents CRUD, providers, widget
│   ├── test_config_persistence.py# 4 теста: .data/tenants/{id}.json
│   ├── test_data_isolation.py    # 9 тестов: tenant A ≠ B, ghost → 404, db_get denied
│   ├── test_mcp_composite.py     # composite mode, prefixed tools
│   ├── test_mcp_dynamic.py       # generated tools + cross-tenant isolation
│   ├── test_mcp_streamable_http.py # v2 Streamable HTTP: scope, sessions, bearer auth и Origin policy
│   ├── test_mcp_validation.py    # required args, limits, tool composition
│   ├── test_named_agent_composite_pipeline.py # named-agent composite chain
│   ├── test_product_readiness_paths.py # 3 product flows: default/demo/MCP/Admin
│   ├── test_scripted_llm.py      # pipeline through ScriptedLLMProvider (LLM mock)
│   ├── test_search_strategies.py # grep/filter/schema/manifest
│   ├── test_tenant_fixture.py    # TestTenant lifecycle helpers
│   └── test_v5_tool_surface.py   # canonical v5 tool surface
│
├── e2e-llm/                      # требует реальный LLM API ключ (не в CI)
│   ├── conftest.py
│   ├── test_implicit_intent.py   # LLM сам вызывает db_*/filter_* по интенту
│   ├── test_llm_chat.py          # SSE chat, agent chat, tools + response
│   ├── test_search_e2e.py        # discovery → search → filter → multiturn
│   └── test_search_strategy.py   # grep/filter/schema через MCP
│
└── external/                     # требует внешние БД (документация)
    └── README.md
```

## Как это работает

Все тесты используют `setup_module` / `teardown_module` (не yield-fixtures —
pytest баг с class-scoped yield fixtures в pytest 9.x).

### seed_database()

Генерирует SQLite БД из seed-сценария. Использует **Python seedgen**
(`services/agent-db/agent_db/seedgen/`), не Go код:

```python
from tests.e2e.helpers import seed_database

# Из сценария (рекомендуется)
seed_database(db_path, scenario="sqlite-testseed")
```

Параметр `seed_path`/`seed.json` удалён — все e2e используют сценарии из
`services/data-service/testdata/scenarios/` (legacy `seed_database(seed_path=...)`
выпилен в 2026-08, gitignored `specs/fixtures/seed.json` больше не нужен).

### mcp_call()

Полный SSE+JSON-RPC протокол для вызова MCP инструментов (v5 surface):

```python
from tests.e2e.helpers import mcp_call

result = mcp_call("db_search", {"entity": "auto_parts", "pattern": "масло"}, tenant_ids="e2e-uni")
assert result.success
```

Поддерживает multi-tenant (composite) через `tenant_ids="a,b"` → префикс `{tenantID}__`.

### Тулсёрфейс v5 (для справки)

| Strategy | MCP tool | Параметры |
|---|---|---|
| map | `db_map` | — |
| describe | `db_describe` | entity |
| search | `db_search` | entity, pattern (required), limit, fields |
| get | `db_get` | entity, id |
| related | `db_related` | entity, id, relation |
| filter | `filter_{entity}` | поля по IsFilterableField + `__gt/__lt/__gte/__lte/__like/__in/__neq` |

`db_filter` НЕ существует; `grep_*/schema_*` per-entity НЕ эмитятся (консолидированы).

## Написание нового теста

1. Создать файл `tests/e2e/test_my_feature.py`
2. Определить `setup_module`/`teardown_module` для seed + cleanup
3. Использовать `seed_database()` + `register_tenant()` + `delete_tenant()` из helpers
4. Проверить в dedicated profile: `./infra/scripts/dev.sh e2e tests/e2e/test_my_feature.py -v`

## CI

В `.github/workflows/ci.yml` — job `test-e2e`: собирает образы сервисов
(docker compose build), поднимает стек через `docker compose --profile test`
и гоняет `tests/e2e/` в контейнере e2e. LLM-тесты в отдельном каталоге
`tests/e2e-llm/` и в CI не участвуют.

## Запуск в Docker (профиль test)

```bash
# Собрать образы сервисов
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  ./infra/scripts/compose.sh --profile test build data-service mcp-gateway api admin-dashboard web

# Поднять стек и прогнать e2e в контейнере
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  ./infra/scripts/compose.sh --profile test up e2e --abort-on-container-exit --exit-code-from e2e

# Остановить и удалить volumes
ADMIN_TOKEN=ci-secret-token ./infra/scripts/compose.sh --profile test down -v
```

Как это устроено:
- Сервис `e2e` (profile `test`) использует образ `helperium-api` и монтирует
  проект в `/workspace` (rw) — чтобы тесты видели и создавали БД по тем же
  путям, что и data-service.
- В контейнер e2e доливается `pytest` + `agent-db` (seedgen) в `.venv`.
- Сервисы доступны по внутренним именам compose-сети
  (`data-service:8084`, `mcp-gateway:8083`, `api:8081`, `web:8080`).
- `ADMIN_TOKEN`/`VIEWER_TOKEN` обязательны и пробрасываются во все сервисы
  (data-service, api, admin-dashboard).
- CI override использует только test-only non-secret MCP token и явный Origin
  allowlist. Поэтому отсутствие bearer token и неразрешённый browser Origin
  проверяются в каждом стандартном E2E run, а не условно пропускаются.
