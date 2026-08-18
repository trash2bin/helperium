# Testing guide

Этот документ нужен при изменении сервисов, tenant lifecycle, MCP или agent pipeline. Сначала запускай ближайший targeted test, затем полный `make ci`; изменения межсервисного контракта и Docker wiring требуют clean Docker E2E.

## Быстрые проверки

```bash
make ci-test-py      # API, RAG, demo/web и SDK
make ci-test-go      # data-service, mcp-gateway, helperium-go
make ci-test-embed   # widget tests + production bundle
make ci-admin        # admin-dashboard build and tests
make ci-docs         # links и AGENTS catalog coverage
make ci              # полный локальный CI
```

Для одного test file используй repository-root paths:

```bash
uv run pytest services/api-service/src/api_service/tests/unit/agent/ -v
uv run pytest services/rag/tests/unit/ -v
uv run pytest demo/web/tests/unit/ -v
uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v
```

Не фиксируй в документации постоянный count тестов: suite развивается. Получить актуальный состав можно так:

```bash
uv run pytest services/agent-db/tests/e2e/ --collect-only -q
```

## E2E без live LLM

### Clean Docker CI profile

Это предпочтительный путь для service boundary, tenant DB, CORS, MCP auth/origin и SSE regressions. Контур создаёт named test volumes и не должен писать в обычные локальные `.data`.

```bash
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose --project-directory infra \
  -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test up --abort-on-container-exit --exit-code-from e2e

ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose --project-directory infra \
  -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test down -v
```

The CI override enables distinct test-only admin/viewer credentials, MCP bearer authentication and an explicit MCP Origin allowlist. Never point host pytest at Docker services when a test relies on SQLite paths visible only inside the Compose volumes.

### Native isolated profile

Use the native profile when diagnosing host-specific behavior:

```bash
./infra/scripts/dev.sh e2e-up
./infra/scripts/dev.sh e2e -v
./infra/scripts/dev.sh stop
```

The profile uses separate loopback ports and temporary databases. It must not share the normal `dev.sh start` runtime state.

## E2E map

| Area | Primary files |
|---|---|
| Tenant lifecycle and persistence | `test_admin_lifecycle.py`, `test_config_persistence.py`, `test_tenant_fixture.py` |
| Tenant data isolation and query surface | `test_data_isolation.py`, `test_search_strategies.py`, `test_v5_tool_surface.py` |
| MCP transport and scope | `test_mcp_streamable_http.py`, `test_mcp_composite.py`, `test_mcp_dynamic.py`, `test_mcp_validation.py` |
| Agent/SSE behavior | `test_scripted_llm.py`, `test_named_agent_composite_pipeline.py` |
| Backend failure handling | `test_backend_resilience.py`, `test_web_proxy_resilience.py` |
| Product paths | `test_product_readiness_paths.py`, `test_agents.py` |

Add a regression at the lowest level that reproduces the fault. Use Docker E2E whenever the defect depends on Compose env, service boundaries, container filesystem visibility, CORS, auth middleware or stream transport. E2E tests must use fixtures and CI volumes; do not create persistent user tenants or mutate external demo data.

## Scripted LLM

`ScriptedLLMProvider` gives deterministic tool/SSE coverage without a paid model:

```bash
USE_SCRIPTED_LLM=1 SCRIPTED_LLM_PATH=script.jsonl \
  uv run pytest services/agent-db/tests/e2e/test_scripted_llm.py -v
```

A JSONL fixture emits one model response per line. Use it for tool invocation, error handling, SSE terminal events and adversarial deterministic cases. Live model runs are a separate, budgeted acceptance step and must preserve a report artifact with commit, model, dataset and cost metadata.

## Debugging

```bash
./infra/scripts/dev.sh logs api
./infra/scripts/dev.sh logs mcp
uv run pytest path/to/test.py::test_name -v --tb=long -s
```

| Symptom | First check |
|---|---|
| `401`/`403` from admin or MCP | Verify distinct admin/viewer tokens, MCP bearer token and allowed Origin. |
| Missing tenant or tools | Confirm tenant registration, manifest generation and `X-Tenant-ID`; query parameter must not select scope. |
| SSE ends unexpectedly | Check API and gateway logs, scripted fixture and terminal `error`/`done` events. |
| SQLite state leaks across tests | Stop the test profile and run Compose `down -v`; inspect only CI volumes. |
| RAG import/dependency failure | Use `make ci-test-py` or read `services/rag/README.md`; do not patch `PYTHONPATH` blindly. |

## Mutation and external tests

```bash
./infra/scripts/run_mutmut.sh --build && ./infra/scripts/run_mutmut.sh --docker
./infra/scripts/run_mutmut.sh --go
```

External/live LLM and browser checks are intentionally outside deterministic CI. Keep their credentials, budgets and target domains explicit; do not target `demo/autoparts-store` without separate approval.

**Last verified:** 2026-08-19 (working tree after `ff2d08b`). Full local `make ci` passed; clean Docker E2E passed 137 tests after API CORS and MCP tenant-scope hardening.
