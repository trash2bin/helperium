# E2E тесты

Модульные e2e тесты для всех сервисов helperium. Используют Python seedgen
для генерации тестовых БД — **не требуют Go компилятора или data-service seed-cli**.

## Запуск

```bash
# Все e2e без LLM — 48 тестов, ~5 сек
uv run pytest tests/e2e/ -v

# С LLM (требует MISTRAL_API_KEY из .env)
uv run pytest tests/e2e/ -v --llm-key

# Без traceback
uv run pytest tests/e2e/ --no-traceback
```

Требуют все 6 сервисов (data-service :8084, mcp-gateway :8083, api-service :8081,
demo-web :8080, rag :8082, admin-dashboard :8085).

## Структура

```
tests/e2e/
├── conftest.py                # .env load, health-check, CLI args
├── helpers.py                 # seed_database, register_tenant, mcp_call, admin_headers
│
├── test_data_isolation.py     # 6 тестов: tenant A ≠ B, ghost → 404
├── test_admin_lifecycle.py    # 11 тестов: CRUD, stats, duplicate 409, delete, persistence
├── test_config_persistence.py # 4 теста: .data/tenants/{id}.json
├── test_mcp_dynamic.py        # 5 тестов: tools + cross-tenant isolation
├── test_mcp_composite.py      # 5 тестов: composite mode, prefixed tools
├── test_sse_session.py        # 4 теста: SSE open, JSON-RPC, tools/list
├── test_agents.py             # 10 тестов: agents CRUD, providers, widget
│
└── llm/
    └── test_llm_chat.py       # 4 теста: SSE chat, agent chat, tools + response
```

## Как это работает

Все тесты используют `setup_module` / `teardown_module` (не yield-fixtures —
pytest баг с class-scoped yield fixtures в pytest 9.x).

### seed_database()

Генерирует SQLite БД из seed-сценария. Использует **Python seedgen**
(`agent-db/agent_db/seedgen/`), не Go код:

```python
from tests.e2e.helpers import seed_database

# Из сценария
seed_database(db_path, scenario="sqlite-testseed")

# Из seed.json
seed_database(db_path, seed_path=Path("specs/fixtures/seed.json"))
```

Защита от PostgreSQL env vars: `seed_database()` всегда создаёт SQLite БД,
даже если в .env установлены `DB_DRIVER=postgres` и `DATABASE_URL`.

### mcp_call()

Полный SSE+JSON-RPC протокол для вызова MCP инструментов:

```python
from tests.e2e.helpers import mcp_call

result = mcp_call("list_student", tenant_ids="e2e-uni")
assert result.success
```

Открывает SSE сессию, получает endpoint URL, POST-запрос JSON-RPC.
Поддерживает multi-tenant (composite) через `tenant_ids="a,b"`.

### Проверка persistence

```python
from tests.e2e.helpers import save_and_check_persistence

config = save_and_check_persistence("my-tenant")
assert config["version"] == 1
```

## LLM тесты (tests/e2e/llm/)

Требуют API ключ Mistral из `.env` (`MISTRAL_API_KEY`).

Используют socket timeout в SSE парсере (12 сек тишины = конец стрима),
чтобы не висеть вечно при недоступности LLM API.

User-Agent везде: `Mozilla/5.0 (compatible; HelperiumE2E/1.0)`
— anti-abuse guard блокирует `python-requests` по умолчанию.

## Написание нового теста

1. Создать файл `tests/e2e/test_my_feature.py`
2. Определить `setup_module`/`teardown_module` для seed + cleanup
3. Использовать `seed_database()` + `register_tenant()` + `delete_tenant()` из helpers
4. Проверить: `uv run pytest tests/e2e/test_my_feature.py -v`
