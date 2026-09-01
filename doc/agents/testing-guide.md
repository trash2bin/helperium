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

## Изоляция живых runtime-артефактов

API unit suite обязан работать на изолированных путях. Session-scoped autouse fixture `_isolate_runtime_artifacts` (services/api-service/src/api_service/tests/unit/conftest.py) перенаправляет `AGENT_DB_PATH` и `SPENDING_PERSISTENCE_PATH` в pytest temp: без этого app-level тесты с `TestClient` + lifespan лениво создают `get_agent_store()` по живому пути `<repo>/agents.sqlite`, и при заданном `ENCRYPTION_KEY` конструктор репозитория мигрирует plaintext `llm_config` в ciphertext тестовым ключом — dev-БД молча повреждается. Эти две переменные читаются лениво; import-time константы (`settings.session_db_path`, `provider_store.DEFAULT_PROVIDERS_PATH`) из fixture перенаправить нельзя — если тесту нужны реальные пути, он обязан задать их через `monkeypatch` явно.

Команда с test-only ключом (ключ одноразовый, в репозиторий не сохраняется):

```bash
ENCRYPTION_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())") \
ENCRYPTION_KEY="$ENCRYPTION_KEY" CORS_ALLOW_ORIGINS=http://localhost:8080 PYTHONPATH=$PWD \
  uv run -- python -m pytest services/api-service/src/api_service/tests/ -q
```

Без `ENCRYPTION_KEY` часть suite ожидаемо падает (`LLMEncryptionKeyRequiredError`) — это fail-fast контракт, а не дефект.

## E2E без live LLM

### Clean Docker CI profile

Это предпочтительный путь для service boundary, tenant DB, CORS, MCP auth/origin и SSE regressions. Контур создаёт named test volumes и не должен писать в обычные локальные `.data`.

```bash
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test up -d data-service mcp-gateway api admin-dashboard web

# `up`/`run` в test-профиле автоматически пересобирают образы сервисов
# (ps/logs/down — нет), поэтому свежий workspace-код не встречает старый image.

ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test run --rm e2e

ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test down -v
```

`compose.sh --profile test` overrides local `.env` with self-contained test-only security values: `MCP_DEV=false`, required MCP bearer authentication, matching gateway/client credentials, an explicit MCP Origin allowlist, high test-only MCP rate limits, and an API control-plane bearer propagated to both services and the E2E caller. Override those values only through `MCP_TEST_*` or `API_TEST_BEARER_TOKEN`, never through production-named variables. `ci-state-init` is a successful one-shot volume permission bootstrap, so do not use `--abort-on-container-exit` for this profile; only `e2e` is terminal. Explicit `CORS_ALLOW_ORIGINS` prevents a local/runner wildcard `.env` from invalidating the fail-closed CORS test. Never point host pytest at Docker services when a test relies on SQLite paths visible only inside the Compose volumes.

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

**Last verified:** 2026-08-31 (working tree after `f094429`, uncommitted audit-tail fixes on top). Clean Docker E2E last passed 138 tests with ci-state-init completing normally outside the terminal E2E lifecycle and explicit fail-closed CORS default; the test-profile compose wrapper now rebuilds service images on `up`/`run` (see above), and the e2e README AST counter stands at 148.
