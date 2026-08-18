# Тестирование — кратко и по делу

## Порядок запуска
1. **Unit/Integration** — без сервисов, через `make`
2. **E2E без LLM** — 131 тест, запускать через изолированный native-профиль `dev.sh e2e-up`
3. **E2E с LLM** — только по требованию, денег много, в CI не гоняем

---

## Unit / Integration (нативно)

```bash
make ci-test-py      # Python: API, RAG, web, SDK
make ci-test-go      # Go: data-service, mcp-gateway, helperium-go
make ci-test-embed   # TS: embed widget (vitest + build)
make ci-admin        # Admin dashboard
make ci              # всё + линты + аудит + доки
```

**Python по отдельности:**
```bash
uv run pytest rag/tests/                              # 108
uv run pytest api-service/src/api_service/tests/      # ~260
uv run pytest demo/web/tests/                         # 73
uv run pytest demo/tests/                             # 18
uv run pytest helperium-sdk/tests/                    # 83
```

**Agent pipeline unit (198 тестов, моки Protocol'ов):**
```bash
cd api-service
uv run pytest src/api_service/tests/unit/agent/ -v
```

**Go:**
```bash
go test ./services/data-service/... ./services/helperium-go/...   # 690
go test ./services/mcp-gateway/...                               # ~80
```

---

## E2E (services/agent-db/tests/e2e/) — 131 тест
```bash
./infra/scripts/dev.sh e2e-up
./infra/scripts/dev.sh e2e -v
./infra/scripts/dev.sh stop
```

**Compose CI-режим**: запускай тесты внутри compose, если сервисы работают в Docker. Host pytest нельзя направлять на Docker services: пути SQLite хоста и контейнера различаются. Используй **оба** compose-файла: `docker-compose.ci.yml` включает test-only MCP/auth конфигурацию, а admin и viewer токены должны различаться.
```bash
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test up --abort-on-container-exit --exit-code-from e2e

docker-compose -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test down -v
```

**Чек-лист:**
- `ADMIN_TOKEN`, `VIEWER_TOKEN` в `.env`
- `uv sync --group dev && uv pip install -e services/agent-db`

### Структура
```
services/agent-db/tests/e2e/
├── helpers.py      # TestTenant, make_tenant, mcp_call, parse_sse_stream
├── conftest.py     # factory-fixtures: tenant(), tenants()
└── test_*.py       # тонкие тесты
```

**Пример:**
```python
def test_x(tenant):
    t = tenant("sqlite-testseed")
    result = mcp_call("db_search", {"entity": "auto_parts", "pattern": "масло"}, tenant_ids=t.id)
    assert result.success
```

### Файлы
| Файл | Тестов | Что проверяет |
|---|---:|---|
| `test_admin_lifecycle.py` | 11 | CRUD tenant и базовый onboarding |
| `test_agents.py` | 10 | Agents CRUD и providers |
| `test_config_persistence.py` | 4 | Атомарная запись `.data/tenants/{id}.json` |
| `test_data_isolation.py` | 9 | Tenant A ≠ B, включая `db_get` и filter |
| `test_mcp_composite.py` | 5 | Composite mode (`X-Tenant-ID: a,b`) |
| `test_mcp_dynamic.py` | 5 | Dynamic/generated MCP tools |
| `test_mcp_streamable_http.py` | 7 | Streamable HTTP, tenant scope, auth и Origin policy |
| `test_mcp_validation.py` | 28 | Required args, limits и tool schemas |
| `test_named_agent_composite_pipeline.py` | 1 | Persisted named-agent composite scope до prefixed MCP tool |
| `test_product_readiness_paths.py` | 3 | Read-only default, canonical single-tenant names, dashboard health |
| `test_scripted_llm.py` | 11 | **Полный pipeline без LLM**: SSE, tools, recovery и guards |
| `test_search_strategies.py` | 31 | grep/filter/schema для auto-shop и clinic |
| `test_tenant_fixture.py` | 3 | Tenant factory, rewrite и isolation fixture |
| `test_v5_tool_surface.py` | 3 | `db_*` и entity filter surface |
### ScriptedLLMProvider — 11 тестов без LLM
```bash
# Воспроизведение
USE_SCRIPTED_LLM=1 SCRIPTED_LLM_PATH=script.jsonl \
  uv run pytest services/agent-db/tests/e2e/test_scripted_llm.py -v

# Запись (реальный LLM → JSONL)
USE_SCRIPTED_LLM=1 SCRIPTED_LLM_RECORD=1 SCRIPTED_LLM_PATH=my.jsonl \
  uv run pytest services/agent-db/tests/e2e/test_scripted_llm.py::test_basic_chat -v
```

`script.jsonl` — по строке на ответ:
```jsonl
{"type": "tool_call", "name": "db_search", "arguments": {"entity": "auto_parts", "pattern": "масло"}}
{"type": "token", "content": "Нашёл 5 записей"}
{"type": "final", "content": "Готово"}
```

---

## Docker (`--profile test`) — только CI

```bash
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  ./infra/scripts/compose.sh --profile test build data-service mcp-gateway api admin-dashboard web

ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token \
  ./infra/scripts/compose.sh --profile test up e2e --abort-on-container-exit --exit-code-from e2e

ADMIN_TOKEN=ci-secret-token ./infra/scripts/compose.sh --profile test down -v
```

**Логи:** `./infra/scripts/compose.sh --profile test logs -f e2e`
**Дебаг:** `./infra/scripts/compose.sh --profile test run --rm e2e bash`

CI-контейнер устанавливает `agent-db[dev]`, поэтому async Streamable HTTP сценарии получают `pytest-asyncio`. Внутри compose используются service DNS (`admin-dashboard:8085`), а CI-профиль прокидывает один test-only MCP token и в прямые MCP проверки, и в дочерний API процесса ScriptedLLM. Не запускайте эти проверки произвольным `docker run` без этих переменных: получится ложный `401` или generic SSE error.


---

## E2E с LLM (opt-in) — `services/agent-db/tests/e2e-llm/`

Не в CI. Нужен API ключ.
```bash
uv run pytest services/agent-db/tests/e2e-llm/ -v
```

| Файл | Что проверяет |
|---|---|
| test_implicit_intent.py | LLM сам вызывает db_search/filter_* |
| test_llm_chat.py | SSE chat, tool call + response |
| test_search_e2e.py | discovery → search → filter → multiturn |
| test_search_strategy.py | grep/filter/schema через MCP |

**Логи (`-s`):** SSE-лог + backlog в `backlog/agent_e2e-llm-test_*.jsonl`

**Грабли:**
- Tenant не создан → LLM отвечает текстом, tool_calls=[]
- v4 тулы в конфиге → LLM вызывает grep_* (нужен v5: db_* + filter_*)
- LiteLLM не тот provider → зависание
- Схема >10K chars → LLM теряет контекст
- Нет User-Agent → 403 Blocked
- Повторяющийся session_id → накопление истории

---

## Дебаг

```bash
./infra/scripts/dev.sh logs api           # логи сервиса
uv run pytest ...::test_name -v --tb=long   # один тест
uv run pytest ... -q --tb=line      # только упавшие
```

---

## Troubleshooting

| Симптом | Решение |
|---|---|
| Connection refused :8084 | `./infra/scripts/dev.sh start && ./infra/scripts/dev.sh status` |
| 403 на admin | `ADMIN_TOKEN=secret` в `.env` |
| Висит на SSE | `./scripts.dev.sh logs mcp` |
| ModuleNotFoundError: agent_db | `uv pip install -e services/agent-db` |
| PG тесты падают | `docker compose up -d db` + `DATABASE_URL=...` |

---

## Mutation Testing

```bash
./infra/scripts/run_mutmut.sh --build && ./infra/scripts/run_mutmut.sh --docker  # Python ~65%
./infra/scripts/run_mutmut.sh --go                                                # Go ~5 мин
```

---

**Last verified:** 2026-08-18 (HEAD `0a6aff5` + uncommitted native E2E and SQLite read-only fixes) — `make ci-test-go` прошёл; полный native E2E-профиль прошёл **131/131** за 91.28 s на macOS; точечный Linux Docker CI onboarding-набор прошёл **11/11**. Native-профиль поднимает сервисы на изолированных loopback-портах `18080–18085`, использует `/tmp` для временных SQLite tenant-баз и не пересекается с SSH-туннелями/обычным `dev.sh start` на `8080–8085`. Контейнерный E2E запускай с обоими compose-файлами и разными admin/viewer токенами, как в GitHub Actions.
