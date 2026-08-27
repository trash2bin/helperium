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
  → SSE (tool_call/tool_result/final/error/done) → widget
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
| Product/demo readiness | Current code + recent CI/E2E evidence, затем dated audits and `doc/archive/remediation-plan-2026-08-18.md` as historical context |

## Проверка

```bash
make ci                         # полный local CI
make ci-docs                    # links и AGENTS catalog coverage
make ci-test-py                 # Python unit/integration
make ci-test-go                 # Go suites
make ci-test-embed              # widget tests + build
make ci-admin                   # admin-dashboard tests
```

Полный isolated Docker E2E. `compose.sh --profile test` принудительно заменяет local `.env` на test-only secure MCP/API credentials, explicit MCP Origin policy и повышенные только для E2E rate limits; не передавай production-named secrets вручную. При необходимости используй только `MCP_TEST_*` и `API_TEST_BEARER_TOKEN`.

```bash
# ci-state-init is a one-shot permission bootstrap; do not attach it to
# --abort-on-container-exit. Start long-lived dependencies, then run e2e alone.
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test up -d data-service mcp-gateway api admin-dashboard web
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test run --rm e2e
```

После Docker run очисти только Helperium test resources:

```bash
ADMIN_TOKEN=ci-admin-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test down -v
```

Не трогай `autoparts-store-*`. Shared `infra_helperium-net` может остаться, если им пользуется заранее поднятый `infra-rag-1` или внешний storefront.

## Документация и артефакты

Живые service guides находятся рядом с кодом. Датированные audits, incidents, plans и benchmark analyses — архивные evidence snapshots: не удаляй их молча и не называй их current verdict без проверки текущего HEAD. Новый архивный документ добавляй только когда он содержит самостоятельное измерение/решение; иначе обновляй существующий README или guide.

`CHANGELOG.md` пополняется **одной краткой записью на commit**. Не используй его как рабочие заметки или полный отчёт тестового прогона. Verification markers в документах описывают commit, на котором текст сверялся; при содержательной правке обновляй marker и ссылку на актуальный test evidence.

Безопасно удалять generated caches, `__pycache__`, `.pytest_cache`, `.ruff_cache`, coverage, `.DS_Store`, build binaries и SQLite `-wal`/`-shm` sidecars после остановки использующих их процессов. Не удаляй без отдельной проверки `.data`, session/provider stores, benchmark reports, Hugging Face cache, RAG state или external demo data: они могут быть намеренно сохранёнными runtime/evidence artifacts.

### Полный каталог для discoverability

| Группа | Документы |
|---|---|
| API и agent | `services/api-service/README.md`, `services/api-service/embed/README.md`, `doc/agents/anti-abuse.md`, `doc/agents/tool-call-safety-layers.md`, `doc/agents/backlog-product-decision.md`, `doc/agents/deferred-decisions.md`, `doc/agents/openspec-adoption-decision.md`, `doc/agents/documentation-restructure-plan.md` |
| Data и config | `services/data-service/README.md`, `services/data-service/internal/configgen/README.md`, `doc/agents/adapter-pattern.md`, `doc/agents/config-migration.md`, `doc/agents/search-strategies.md`, `specs/config.schema.md`, `specs/fixtures/README.md`, `specs/README.md` |
| MCP и security | `services/mcp-gateway/README.md`, `doc/agents/mcp-session-lifecycle.md`, `doc/agents/security-isolation.md`, `doc/agents/http-clients.md`, `doc/agents/api-contracts.md` |
| Admin, web и operations | `services/admin-dashboard/README.md`, `doc/agents/tenant-lifecycle.md`, `doc/agents/web-service.md`, `doc/agents/operations.md`, `doc/agents/ci-cd.md`, `doc/agents/testing-guide.md` |
| RAG, demos и E2E | `services/rag/README.md`, `services/agent-db/README.md`, `services/agent-db/agent_db/bench/README.md`, `services/agent-db/tests/e2e/README.md`, `services/agent-db/tests/e2e-llm/README.md`, `services/agent-db/tests/external/README.md`, `demo/README.md`, `demo/web/README.md`, `demo/autoparts-store/README.md` |
| Benchmark live docs | `doc/benchmark/README.md`, `doc/benchmark/core-benchmark.md`, `doc/benchmark/runs/README.md` |
| Benchmark archives | `doc/archive/2026-08-05-data-service-audit.md` |
| Product/audit archives | `doc/archive/2026-08-01-data-service-refactor-audit.md`, `doc/archive/product-readiness-audit-2026-08-18-head-14d3758.md`, `doc/archive/production-resilience-audit-2026-08-18-head-bd5adb5.md`, `doc/archive/remediation-plan-2026-08-18.md` |

## Current verification baseline

**Last verified:** 2026-08-20 (working tree following `e839d6c`). Full local `make ci` passed; the API suite passed **375 tests** with the same 38 pre-existing pytest marker warnings, and Pyright passed with no errors. The current security-critical session quota has one explicit public contract: `max_user_turns_per_session` / `ABUSE_MAX_USER_TURNS`. It counts durable accepted ingress user turns, not transcript messages, and it is the same accepted-at marker used by `min_interval_ms`; provider/tool failure never refunds it. The retired `max_messages_per_session` / `ABUSE_MAX_MESSAGES` names have no compatibility alias. Admin global/per-agent JSON decoding, direct agent DTO validation and persisted admin config loading reject stale unknown fields; a stale global policy fails startup/reload instead of silently falling back to weaker defaults. The typed SDK `AbuseConfigOverride` is `extra=forbid`, and API/admin OpenAPI plus dashboard bindings expose only the new field. `SessionStore` remains a domain facade over `SessionRepository`/`SQLiteSessionRepository`; legacy transcript history backfills before the first accepted turn. Native runtime restarted all six Helperium services healthy. Live admin `GET /api/abuse-settings` returned `max_user_turns_per_session=50` without the old key; legacy PUT returned `400`; acknowledged `POST /api/admin/abuse-config/reload` returned `status=applied`. The seeded `autoparts` read-only tenant completed a fresh MiniMax `db_search → tool_result → final` turn under the renamed runtime. The trusted-data invariant and `AppendOnlyLoop` context telemetry remain as verified in `9e85526`; their system-level declaration is defence-in-depth, not a hard prompt-injection boundary. This proves local core, MCP transport, live fallback, explicit user-turn quota contract, no-alias migration, admin apply and core regression coverage; it does **not** replace broader LLM quality/benchmark coverage, browser acceptance on a deployed domain, edge/WAF validation, alerting/rollback game day, multi-instance shared abuse state, a full RAG/prompt-injection assessment, behavioural anomaly detection after untrusted results, or a reserve/commit spending design bound to a named agent/account rather than tenant ID. The CI E2E workflow now starts long-lived dependencies detached and runs `e2e` as the only terminal container: `ci-state-init` may exit successfully without aborting the stack. Explicit `CORS_ALLOW_ORIGINS=http://localhost:8080` prevents a runner `.env` wildcard from masking the fail-closed CORS regression. A clean Docker profile completed all 137 E2E tests under this lifecycle; only CI volumes/containers were removed, while the external storefront was untouched.
