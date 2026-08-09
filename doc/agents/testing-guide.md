# Тестирование — кратко и по делу

## Порядок запуска
1. **Unit/Integration** — без сервисов, через `make`
2. **E2E без LLM** — 124 теста, нужны сервисы (`./scripts/dev.sh start`)
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

## E2E (services/agent-db/tests/e2e/) — 124 теста

```bash
./scripts/dev.sh start
./scripts/dev.sh status
uv run pytest services/agent-db/tests/e2e/ -v
./scripts.dev.sh stop
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
|---|---|---|
| test_admin_lifecycle.py | 11 | CRUD тенантов |
| test_agents.py | 10 | Agents CRUD, providers |
| test_config_persistence.py | 4 | Атомарная запись `.data/tenants/{id}.json` |
| test_data_isolation.py | 9 | Tenant A ≠ B |
| test_mcp_composite.py | 5 | Composite mode (`X-Tenant-ID: a,b`) |
| test_mcp_dynamic.py | 5 | v5 тулы через MCP |
| test_mcp_validation.py | 28 | Required args, limits |
| test_scripted_llm.py | 11 | **Полный пайплайн без LLM** ⭐ |
| test_search_strategies.py | 31 | grep/filter/schema (auto-shop, clinic) |
| test_sse_session.py | 4 | SSE, JSON-RPC |
| test_v5_tool_surface.py | 6 | v5 surface (db_*, filter_*) |

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
./scripts/dev.sh logs api           # логи сервиса
uv run pytest ...::test_name -v --tb=long   # один тест
uv run pytest ... -q --tb=line      # только упавшие
```

---

## Troubleshooting

| Симптом | Решение |
|---|---|
| Connection refused :8084 | `./scripts/dev.sh start && ./scripts/dev.sh status` |
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

**Verified:** 2026-08-07
