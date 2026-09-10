AGENTS.md — **навигация и операционный контракт**, а не дневник разработки. Датированные аудиты, benchmark runs и исполненные планы сохраняются как evidence в `doc/`; их выводы не заменяют проверку текущего кода и тестов.

## Назначение и границы

Helperium — self-hosted платформа, которая подключает клиентскую SQL-базу в **read-only** режиме, интроспектирует схему и предоставляет LLM-агенту tenant-scoped MCP-инструменты. Встраиваемый чат отвечает по живым данным; администратор управляет tenant-конфигурацией, агентами и policy.

`demo/autoparts-store` — автономный поддерживаемый demo consumer, а не часть production runtime Helperium (но используется в публичном демо). Его код и deployment-конфигурацию можно менять для security, integration design и воспроизводимости; не останавливай, не seed'ируй и не очищай его контейнеры или PostgreSQL без отдельного прямого разрешения. Он должен подключаться как обычный demo tenant.

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
  → mcp-gateway /mcp → data-service ( or RAG) → tenant DB
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
| No prompt steering | Поведение модели контролируется структурой (schemas, allow-list, validation, лимиты, регенерация), не текстовыми уговорами в транскрипте. Новые model-facing steering-нотисы не добавлять; существующие repair-нотисы после структурного отказа — не прецедент. |
| API CORS | При отсутствии override Compose разрешает только `http://localhost:8080`. Public embed domains указываются явно в `CORS_ALLOW_ORIGINS`; не возвращай wildcard fallback. |
| Demo isolation | Локальные E2E используют CI volumes и loopback ports; не пишут в пользовательские `.data` или external storefront data. |
| Public errors | Ошибки dependency/transport должны быть retryable и sanitised; не раскрывать DSN, credentials, filesystem paths, stack traces или internal hosts. |

## Как работать с репозиторием

Сначала прочитай релевантный маршрут ниже (или похожий), затем используй graph/codebase memory/grep/find, и подтверди связи кодом или tests. Используй services READMEs и test suite — не делай предположений по старому audit text.

Меняй контракт API, schema, tenant model или public tool surface только после явного подтверждения пользователя. Обычные bug fixes, безопасная documentation hygiene, broken links и isolated test regressions можно исправлять самостоятельно. Каждый подтверждённый defect получает regression test; каждый code change проходит ближайший targeted suite и, если затронуты сервисные границы, Docker E2E

При нахождении бага и особенно уязвимости безопасности **сначала test** потом рабочий код (тест обязан падать!).

| Задача | Начать с |
|---|---|
| MCP session, auth, tools или tenant scope | `doc/agents/mcp-session-lifecycle.md` → `services/mcp-gateway/README.md` → `doc/agents/security-isolation.md` |
| Data-service/query/config generation | `services/data-service/README.md` → `doc/agents/search-strategies.md` → `doc/agents/adapter-pattern.md` |
| Tenant onboarding и Admin | `doc/agents/tenant-lifecycle.md` → `services/admin-dashboard/README.md` → `doc/RUNBOOK.md` |
| API, SSE, LLM или abuse | `services/api-service/README.md` → `doc/agents/anti-abuse.md` → `doc/agents/tool-call-safety-layers.md` |
| Разбор жалоб посетителей (reports) | `doc/agents/reports-triage.md` → `doc/agents/operations.md` → `doc/monitoring.md` |
| Security assessment | `doc/PENTEST-CHEK.md` → `doc/agents/security-isolation.md` → `doc/agents/tool-call-safety-layers.md` |
| Cross-service HTTP/CORS | `doc/api-flow.md` → `doc/agents/http-clients.md` → `doc/agents/api-contracts.md` |
| CI, local failures и E2E | `doc/agents/testing-guide.md` → `doc/agents/ci-cd.md` → `Makefile` |
| Зависимости / uv lock / аудит CVE | `doc/dependency-index-traps.md` → `pyproject.toml` → `Makefile` (ci-audit, ci-e2e) |
| Benchmark / answer quality | `doc/benchmark/README.md` → `doc/benchmark/core-benchmark.md` → `doc/benchmark/runs/README.md` |
| Остатки демо-аудита / follow-up | `doc/archive/demo-readiness-followup-2026-08-31-head-f094429.md` → локальный untracked todo-файл в корне репозитория (рабочий список для агента-исполнителя, в git не входит) |
| Operations / monitoring | `doc/agents/operations.md` → `doc/monitoring.md` → `infra/scripts/dev.sh` |
| Product/demo readiness | Current code + recent CI/E2E evidence, затем dated audits (`doc/archive/product-demo-readiness-audit-2026-08-28-head-53a3172.md`, `doc/archive/demo-readiness-followup-2026-08-31-head-f094429.md`, `doc/archive/widget-demo-readiness-2026-09-01-head-f094429.md`) and `doc/archive/remediation-plan-2026-08-18.md` as historical context |
| Остальное | по примеру по названию .md файла искать и только потом искать и править код/ошибку |

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

Не трогай `autoparts-store-*` это отдельный демо с виджетом и своей бд на pg. Shared `infra_helperium-net` может остаться, если им пользуется заранее поднятый `infra-rag-1` или внешний storefront.

## Документация и артефакты

Живые service guides находятся рядом с кодом. Датированные audits, incidents, plans и benchmark analyses — архивные evidence snapshots: не удаляй их молча и не называй их current verdict без проверки текущего HEAD. Новый архивный документ добавляй только когда он содержит самостоятельное измерение/решение; иначе обновляй существующий README или guide.

`CHANGELOG.md` пополняется **одной краткой записью на commit**. Не используй его как рабочие заметки или полный отчёт тестового прогона. Verification markers в документах описывают commit, на котором текст сверялся; при содержательной правке обновляй marker и ссылку на актуальный test evidence.


### Полный каталог для discoverability

| Группа | Документы |
|---|---|
| API и agent | `services/api-service/README.md`, `services/api-service/embed/README.md`, `doc/agents/anti-abuse.md`, `doc/agents/tool-call-safety-layers.md`, `doc/agents/backlog-product-decision.md`, `doc/agents/deferred-decisions.md`, `doc/agents/spending-reserve-commit-decision.md`, `doc/agents/openspec-adoption-decision.md`, `doc/agents/documentation-restructure-plan.md`, `doc/agents/queue-plan-2026-08-30-head-bfa16d3.md` |
| Data и config | `services/data-service/README.md`, `services/data-service/internal/configgen/README.md`, `doc/agents/adapter-pattern.md`, `doc/agents/config-migration.md`, `doc/agents/search-strategies.md`, `specs/config.schema.md`, `specs/fixtures/README.md`, `specs/README.md` |
| MCP и security | `services/mcp-gateway/README.md`, `doc/agents/mcp-session-lifecycle.md`, `doc/agents/security-isolation.md`, `doc/agents/http-clients.md`, `doc/agents/api-contracts.md` |
| Admin, web и operations | `services/admin-dashboard/README.md`, `doc/agents/tenant-lifecycle.md`, `doc/agents/web-service.md`, `doc/agents/operations.md`, `doc/agents/reports-triage.md`, `doc/agents/ci-cd.md`, `doc/agents/testing-guide.md` |
| RAG, demos и E2E | `services/rag/README.md`, `services/agent-db/README.md`, `services/agent-db/agent_db/bench/README.md`, `services/agent-db/tests/e2e/README.md`, `services/agent-db/tests/e2e-llm/README.md`, `services/agent-db/tests/external/README.md`, `demo/README.md`, `demo/web/README.md`, `demo/autoparts-store/README.md` |
| Benchmark live docs | `doc/benchmark/README.md`, `doc/benchmark/core-benchmark.md`, `doc/benchmark/runs/README.md` |
| Benchmark archives | `doc/archive/2026-08-05-data-service-audit.md` |
| Product/audit archives | `doc/archive/2026-08-01-data-service-refactor-audit.md`, `doc/archive/product-readiness-audit-2026-08-18-head-14d3758.md`, `doc/archive/production-resilience-audit-2026-08-18-head-bd5adb5.md`, `doc/archive/product-demo-readiness-audit-2026-08-28-head-53a3172.md`, `doc/archive/demo-readiness-followup-2026-08-31-head-f094429.md`, `doc/archive/widget-demo-readiness-2026-09-01-head-f094429.md`, `doc/archive/remediation-plan-2026-08-18.md` |

Коммит происходит только по скиллу, а также перед коммитом работает pre-commit, который способен править файлы тем самым раняя git commit. Файлы убираются из индекса и их стоит заново git add.
