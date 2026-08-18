# Helperium — проектный гид

Этот файл — **навигация и операционный контракт**, а не дневник разработки. Датированные аудиты, benchmark runs и исполненные планы сохраняются как evidence в `doc/`; их выводы не заменяют проверку текущего кода и тестов.

## Назначение и границы

Helperium — self-hosted платформа, которая подключает клиентскую SQL-базу в **read-only** режиме, интроспектирует схему и предоставляет LLM-агенту tenant-scoped MCP-инструменты. Встраиваемый чат отвечает по живым данным; администратор управляет tenant-конфигурацией, агентами и policy.

`demo/autoparts-store` — **внешний consumer**, а не часть runtime Helperium. Не меняй, не останавливай, не seed'ируй и не очищай его контейнеры или PostgreSQL без отдельного прямого разрешения. Он должен подключаться как обычный demo tenant с отдельными read-only credentials.

| Контур | Порт | Роль |
|---|---:|---|
| `api-service` | 8081 | FastAPI orchestration, LLM, chat SSE, MCP client |
| `rag-service` | 8082 | Опциональный RAG/ChromaDB |
| `mcp-gateway` | 8083 | Authenticated Streamable HTTP MCP `/mcp`, tenant/composite scope |
| `data-service` | 8084 | Tenant lifecycle, manifest, read-only data/query surface |
| `admin-dashboard` | 8085 | Admin RBAC и management UI |
| `demo/web` | 8080 | Dev-only proxy/demo pages; не production storefront |
| `agent-db` | — | Seedgen, fixtures, benchmark и E2E |

## Критический data flow

```text
Embed widget → api-service → LLM/orchestrator → MCPClient
  → mcp-gateway /mcp → data-service → tenant DB
  → SSE (tool_call/tool_result/token/final/error/done) → widget
```

Admin flow: `admin-dashboard → api-service/data-service`; tenant onboarding идёт через `POST /admin/tenants`, затем manifest/config generation. Агент не должен получать сырой DSN, секреты или cross-tenant scope из browser-controlled headers.

## Неподвижные контракты

| Тема | Контракт |
|---|---|
| Tenant data | По умолчанию read-only. Не добавляй write SQL/tools как shortcut. |
| MCP transport | Единственный современный transport — Streamable HTTP `/mcp`; legacy SSE MCP routes не возвращать. |
| MCP scope | Только `X-Tenant-ID`; query parameter не выбирает tenant. Composite scope ограничен уникальными ID. Tenant ID допускает `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. |
| MCP auth/origin | Production требует distinct `MCP_API_KEY`/`MCP_CLIENT_API_KEY` и explicit `MCP_ALLOWED_ORIGINS`. `/health` остаётся public. |
| Direct chat | Browser `X-Tenant-ID` не определяет scope direct chat; используется server-configured default. |
| API CORS | При отсутствии override Compose разрешает только `http://localhost:8080`. Public embed domains указываются явно в `CORS_ALLOW_ORIGINS`; не возвращай wildcard fallback. |
| Demo isolation | Локальные E2E используют CI volumes и loopback ports; не пишут в пользовательские `.data` или external storefront data. |
| Public errors | Ошибки dependency/transport должны быть retryable и sanitised; не раскрывать DSN, credentials, filesystem paths, stack traces или internal hosts. |

## Как работать с репозиторием

Сначала прочитай релевантный маршрут ниже, затем используй graph/codebase memory, если он доступен, и подтверди связи кодом или tests. Если graph service недоступен, используй targeted `git grep`, service README и test suite — не делай предположений по старому audit text.

Меняй контракт API, schema, tenant model или public tool surface только после явного подтверждения пользователя. Обычные bug fixes, безопасная documentation hygiene, broken links и isolated test regressions можно исправлять самостоятельно. Каждый подтверждённый defect получает regression test; каждый code change проходит ближайший targeted suite и, если затронуты сервисные границы, Docker E2E.

| Задача | Начать с |
|---|---|
| MCP session, auth, tools или tenant scope | `doc/agents/mcp-session-lifecycle.md` → `services/mcp-gateway/README.md` → `doc/agents/security-isolation.md` |
| Data-service/query/config generation | `services/data-service/README.md` → `doc/agents/search-strategies.md` → `doc/agents/adapter-pattern.md` |
| Tenant onboarding и Admin | `doc/agents/tenant-lifecycle.md` → `services/admin-dashboard/README.md` → `doc/RUNBOOK.md` |
| API, SSE, LLM или abuse | `services/api-service/README.md` → `doc/agents/anti-abuse.md` → `doc/agents/tool-call-safety-layers.md` |
| Security assessment | `doc/PENTEST-CHEK.md` → `doc/agents/security-isolation.md` → `doc/agents/tool-call-safety-layers.md` |
| Cross-service HTTP/CORS | `doc/api-flow.md` → `doc/agents/http-clients.md` → `doc/agents/api-contracts.md` |
| CI, local failures и E2E | `doc/agents/testing-guide.md` → `doc/agents/ci-cd.md` → `Makefile` |
| Benchmark / answer quality | `doc/benchmark/README.md` → `doc/benchmark/core-benchmark.md` → `doc/benchmark/runs/README.md` |
| Operations / monitoring | `doc/agents/operations.md` → `doc/monitoring.md` → `infra/scripts/dev.sh` |
| Product/demo readiness | Current code + recent CI/E2E evidence, затем dated audits and `doc/agents/remediation-plan-2026-08-18.md` as historical context |

## Проверка

```bash
make ci                         # полный local CI
make ci-docs                    # links и AGENTS catalog coverage
make ci-test-py                 # Python unit/integration
make ci-test-go                 # Go suites
make ci-test-embed              # widget tests + build
make ci-admin                   # admin-dashboard tests
```

Полный isolated Docker E2E:

```bash
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose --project-directory infra \
  -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test up --abort-on-container-exit --exit-code-from e2e
```

После Docker run очисти только Helperium test resources:

```bash
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token \
  docker-compose --project-directory infra \
  -f infra/docker-compose.yml -f infra/docker-compose.ci.yml \
  --profile test down -v
```

Не трогай `autoparts-store-*`. Shared `infra_helperium-net` может остаться, если им пользуется заранее поднятый `infra-rag-1` или внешний storefront.

## Документация и артефакты

Живые service guides находятся рядом с кодом. Датированные audits, incidents, plans и benchmark analyses — архивные evidence snapshots: не удаляй их молча и не называй их current verdict без проверки текущего HEAD. Новый архивный документ добавляй только когда он содержит самостоятельное измерение/решение; иначе обновляй существующий README или guide.

`CHANGELOG.md` пополняется **одной краткой записью на commit**. Не используй его как рабочие заметки или полный отчёт тестового прогона. Verification markers в документах описывают commit, на котором текст сверялся; при содержательной правке обновляй marker и ссылку на актуальный test evidence.

Безопасно удалять generated caches, `__pycache__`, `.pytest_cache`, `.ruff_cache`, coverage, `.DS_Store`, build binaries и SQLite `-wal`/`-shm` sidecars после остановки использующих их процессов. Не удаляй без отдельной проверки `.data`, session/provider stores, benchmark reports, Hugging Face cache, RAG state или external demo data: они могут быть намеренно сохранёнными runtime/evidence artifacts.

### Полный каталог для discoverability

| Группа | Документы |
|---|---|
| API и agent | `services/api-service/README.md`, `services/api-service/embed/README.md`, `doc/agents/anti-abuse.md`, `doc/agents/tool-call-safety-layers.md` |
| Data и config | `services/data-service/README.md`, `services/data-service/internal/configgen/README.md`, `doc/agents/adapter-pattern.md`, `doc/agents/config-migration.md`, `doc/agents/search-strategies.md`, `specs/config.schema.md`, `specs/fixtures/README.md`, `specs/README.md` |
| MCP и security | `services/mcp-gateway/README.md`, `doc/agents/mcp-session-lifecycle.md`, `doc/agents/security-isolation.md`, `doc/agents/http-clients.md`, `doc/agents/api-contracts.md` |
| Admin, web и operations | `services/admin-dashboard/README.md`, `doc/agents/tenant-lifecycle.md`, `doc/agents/web-service.md`, `doc/agents/operations.md`, `doc/agents/ci-cd.md`, `doc/agents/testing-guide.md` |
| RAG, demos и E2E | `services/rag/README.md`, `services/agent-db/README.md`, `services/agent-db/agent_db/bench/README.md`, `services/agent-db/tests/e2e/README.md`, `services/agent-db/tests/e2e-llm/README.md`, `services/agent-db/tests/external/README.md`, `demo/README.md`, `demo/web/README.md`, `demo/autoparts-store/README.md` |
| Benchmark live docs | `doc/benchmark/README.md`, `doc/benchmark/core-benchmark.md`, `doc/benchmark/runs/README.md` |
| Benchmark archives | `doc/benchmark/data-service-audit.md`, `doc/benchmark/demo-integration-audit.md`, `doc/benchmark/incident-camry.md`, `doc/benchmark/plan-for-review.md`, `doc/benchmark/runs/2026-08-16-nvidia-nim-rebuilt-final-full-run-analysis.md` |
| Product/audit archives | `doc/agents/data-service-refactor-audit.md`, `doc/agents/product-readiness-audit-2026-08-16.md`, `doc/agents/product-readiness-audit-2026-08-18.md`, `doc/agents/product-readiness-audit-2026-08-18-head-0a6aff5.md`, `doc/agents/product-readiness-audit-2026-08-18-head-14d3758.md`, `doc/agents/production-resilience-audit-2026-08-18-head-bd5adb5.md`, `doc/agents/remediation-plan-2026-08-18.md` |

## Current verification baseline

**Last verified:** 2026-08-19 (working tree after `ff2d08b`). Full local `make ci` passed. Clean Docker E2E passed **137 tests** after API CORS and MCP tenant-scope hardening. Current `main` also contains native SQLite onboarding and backend dependency/SSE resilience fixes. This proves the deterministic local core path; it does **not** replace a live LLM quality run, browser acceptance on the deployed domain, edge/WAF validation, alerting/rollback game day or a full RAG/prompt-injection assessment.
