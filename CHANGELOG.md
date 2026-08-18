# CHANGELOG.md

## 2026-08-18
- **fix(docker-e2e/resilience):** Docker CI получил отдельные named volumes и root-only init gate, поэтому чистый E2E больше не пишет в локальные `.data` и не crash-loop'ит из-за прав на SQLite state. Web SSE-proxy убирает upstream framing headers (`Content-Length`/`Transfer-Encoding`), сохраняя upstream 404/429; API 429 теперь содержит точный `Retry-After` из active SlowAPI window. Добавлены Docker E2E на proxy status/framing, per-visitor forwarded-IP bucket и очистку SQLite WAL/SHM sidecars. **Проверка:** чистый Docker E2E **135 passed**, `make ci`.

- **fix(resilience):** web SSE-прокси теперь передаёт upstream 429 и `Retry-After` без ложного 500; api-service и anti-abuse используют исходный IP из `X-Forwarded-For`, а demo-web сохраняет его через private proxy chain. Добавлены регрессии для 429, forwarded IP и герметичного CORS default-теста; практический resilience-аудит задокументировал измеренный API restart (~5 с), отсутствие alerting/edge DDoS контура и pre-prod gates. **Проверка:** `make ci`.

- **fix(mcp/security):** gateway получил production fail-fast `MCP_REQUIRE_AUTH=true`, shared `MCP_API_KEY`/`MCP_CLIENT_API_KEY` Compose wiring, strict `MCP_ALLOWED_ORIGINS` policy, header-only metadata access и bounds для composite scopes (8 unique tenant IDs by default). Registry больше не удерживает mutex во время manifest fetch.
- **fix(tenant-authority):** public direct text/voice chat использует только server-configured `DEFAULT_TENANT_ID` и игнорирует browser `X-Tenant-ID`; composite scope может происходить только из persisted named-agent record.
- **test(mcp):** native SDK v2 E2E добавил session replay isolation, composite-scope abuse rejection, configured missing-auth и invalid-Origin checks; полный secure-stack deterministic E2E: **127 passed**.
- **docs(mcp):** `.env.example`, Compose, dev launcher, MCP service READMEs, lifecycle guide и production RUNBOOK документируют обязательные production credentials, Origin policy, stateful topology и executable verification commands.
- **refactor(api/tenant):** server-controlled direct-chat scope вынесен из HTTP handlers в `tenant_authority.py`; route code теперь выражает только вызов policy resolver, а не читает environment и не содержит authorization policy inline.
- **test(mcp):** добавлен настоящий ScriptedLLM E2E pipeline persisted named-agent composite scope → api-service → MCPClient → два prefixed gateway tools → две tenant БД data-service → SSE. Request с hostile `X-Tenant-ID` не влияет на result; rebuilt secure stack: **128 passed**.
- **fix(ci/mcp):** полный `make ci` восстановлен: Ruff отформатировал четыре Python-файла, устаревшая demo-проверка удалённого `mcp_service_url` удалена, а gateway снова публикует `mcp_tool_calls_total` и lifecycle-based `mcp_sessions_active` для Streamable HTTP. Grafana и monitoring docs больше не называют этот transport legacy SSE.

## 2026-08-17

- **test(mcp):** native Streamable HTTP v2 E2E теперь покрывает read-only tool call, composite tenant scope с prefixed tools и fail-closed rejection tenant query parameter без `X-Tenant-ID`; gateway unit suite дополнена регрессиями отсутствия legacy routes, header-only scope resolver и `503` при saturation bounded tenant-scope cache. Полный deterministic E2E: **123 passed**.
- **docs(mcp):** AGENTS.md, README api-service, mcp-gateway и agent-db синхронизированы с единственным `/mcp` transport contract, service auth, error/status semantics, diagnostics и executable focused test commands. Package map агента больше не маркирует MCP client как SSE.

## 2026-08-15
- **fix(bench/evaluator):** добавлены fixture-scoped `value_aliases` с проверкой явного отрицания; `payment=online` теперь корректно принимает display labels «онлайн»/«онлайн-оплата» без generic fuzzy matching. Deterministic re-evaluation сохранённого третьего NIM run изменила только payment case: **47 CORRECT / 1 PARTIAL / 1 WRONG / 0 ERROR** (98,0%), без model-вызовов.
- **feat(bench/policy):** versioned `autoparts-benchmark-v1` policy и CLI `sync-agent-policy` воспроизводимо синхронизируют только `system_prompt` через Agent API, не меняя provider/tenant config. Policy требует MCP-grounding для tenant facts и остановки после достаточного tool result.
- **feat(data-service):** generic FilterStrategy MCP description теперь явно фиксирует: вызов требует field filter, `limit` — только preview, `total` — авторитетное count значение.
- **test/docs(bench):** добавлены positive/negative/negation alias tests, policy synchronizer и FilterStrategy contract regressions; зарегистрированы третий raw NIM run, tool traces, case-level analysis и payment-alias re-evaluation. `make ci-test-py`, `make ci-test-go`, `make ci-docs` зелёные.
- **fix(bench):** скорректированы deterministic evaluator и отчёт: однозначная классификация agent-side tool parse errors, нормализация узких пробелов в числах, word-boundary для маркеров неуверенности, verdict-oriented metrics вместо legacy success/tool-error rate; обновлены fixture expectations и регрессии.
- **fix(bench/cases):** unfiltered count cases допускают `db_describe`; две неоднозначные скидочные fixtures сохранены как `deprecated` history и заменены явными cases для ценовой скидки (`old_price > price` → 72) и маркетинговых labels (`sale`/`promo` → 49). Active scoring загружает 49 cases, history содержит 51.
- **feat(data-service):** filter parameter descriptions теперь включают versioned PostgreSQL field comments; deterministic autoparts fixture задаёт domain meaning для `old_price` и `label`, проходящий через introspection и configgen без ручной правки runtime tenant config.
- **docs(benchmark):** reconciled provenance трёх NVIDIA NIM artifacts; добавлен raw full-run report, обновлены README, core benchmark contract и documented active/historical discount lifecycle.
- **test:** добавлены regression tests для count/discount fixture, deprecation loading и configgen field descriptions; полный локальный `make ci` зелёный.

> Единый журнал значимых изменений проекта. Хронология, от новых к старым. Подробности по каждому пункту — в соответствующих README/doc/agents/*.

## 2026-08-11

- **feat(bench):** переработка evaluator — явный `verdict` (CORRECT/PARTIAL/WRONG/ERROR) и таксономия ошибок `ErrorClass` (17 кодов: HALLUCINATED_SKU, LOST_TOTAL, FALSE_UNCERTAINTY, TOOL_OVERUSE, TOOL_LOOP, SCHEMA_ENTITY_ERROR, INFRA_ERROR и др.). Новые детерминированные проверки: SKU-галлюцинация (через `check_skus`/`any_of_skus`), LOST_TOTAL (знал total:N, сказал «много» — Camry-класс), FALSE_UNCERTAINTY («скорее всего» при точных данных), budget (`budget.max_tool_calls/db_get/llm/tokens/cost`), tool-loop (проброс `loop_warnings` из backlog). Фикс бага: error payload `{"error": "timeout"}` больше не считается данными → INFRA_ERROR, `error_source` (agent/tool/infra). Dedupe в `min_count` по уникальным сущностям. Сужены bool-маркеры и `_derive_from_tool_numbers`.
- **feat(bench/report):** отчёт расширен — verdict-доли, histogram ошибок по классам, p50/p95 по tokens/duration/cost/tool_calls/llm_calls, `avg_repeated/unique_tool_calls`/`avg_db_get`, run_metadata (git_commit/model/dataset/timestamp). Новое: `diff_reports()` — case-level diff двух прогонов (регрессии). CLI: exit code по verdict (WRONG/ERROR → 1).
- **feat(bench/cases):** кейсы autoparts.json обогащены — `budget.max_tool_calls` на count/aggregation, `answer_rules.expect_total_mentioned` на count, `any_of_skus`+`check_skus` на lookup (46 полей).
- **fix(bench/smoke):** smoke_scripted — исправлены устаревшие пути (`api-service/src` → `services/api-service/src`, `agent-db` → `services/agent-db`), скрипт-мок синхронизирован с актуальными кейсами (EXT-01392 → 2751). Прогнан end-to-end на живом стеке (lookup/absence CORRECT, count WRONG — ожидаемо).
- **feat(bench/baseline):** первый реальный прогон 49 кейсов на живом агенте (polza/deepseek-v4-flash): 80% CORRECT / 16% PARTIAL / 4% WRONG / 0% ERROR, success 95.9%. Артефакт: `reports/baseline-c1d7f81/` (report.json + backlog + summary.md). Triage выявил 8 false positives evaluator (исправлены: bool-матчинг по ключу, «возможно» убран из маркеров, LOST_TOTAL по expected.count, табличные № строк 1..50, отказ «в базе нет», breakdown-числа ≤ total, db_map не запрещён) и 2 реальных дефекта агента (галлюцинация цен в таблицах, LOST_TOTAL).
- **tests(bench):** 34 → 73 тестов (`test_bench_core.py`): verdict, error classes, SKU (вкл. кириллические АП-100005), LOST_TOTAL, FALSE_UNCERTAINTY, budget, dedupe, error payload, bool, percentiles, diff_reports, derived numbers. Все зелёные, без LLM/сети.
- **fix(bench/review):** ужесточены производные числа — «всего/товаров/позиций» больше НЕ прощают неподтверждённые числа (только явная арифметика с = или маркеры итого/плюс/ещё); произвольные пары больших чисел (700=20×35) больше не прощаются — только line-item цена×кол-во (677×3=2031); SKU-regex расширен до `\d{3,6}` (ловит АП-100005) + декодирование unicode-escape в tool_results (кириллические SKU в JSON).
- **docs(bench):** README бенча + core-benchmark.md — секции «Verdict и таксономия ошибок», обновлены метрики/формат кейсов/нюансы; метки верификации обновлены.

## 2026-08-07

- **fix(demo-web):** `_proxy_to_data_service` терял query-params — добавлен `params=dict(request.query_params)` (кириллический `pattern` через `/api/data/{entity}` доходил пустым).
- **feat(api-service):** seed агента `default` в lifespan (виджет `data-agent="default"` на свежем окружении давал 404) + env override `PROVIDER_STORE_PATH` для провайдер-стора (`.data/` в контейнере не writable).
- **chore(demo):** реальный LLM для демо — `USE_SCRIPTED_LLM=0`, `OPENAI_API_KEY/BASE/MODEL` (Polza/deepseek-v4-flash) в compose, `ENABLE_THINK=false`; `.env` — кавычки вокруг `OPENAI_MODEL` (содержит `&`).
- **docs:** артефакт `doc/benchmark/demo-integration-audit.md` — 6 корней демо-проблем + 2 открытых фронтовых бага (app.js вкладки, admin-dashboard Alpine); карта §5b + метка верификации обновлены.
- **docs:** полная верификация документации роем субагентов + реструктуризация карты доков в AGENTS.md: (1) фактчек всех core-доков против кода (исправлены битые пути `docker/`→`infra/docker/`, `scripts/dev.sh`→`infra/scripts/dev.sh`, задвоенный `services/agent-db/services/agent-db/`, несуществующие файлы/метрики, устаревшие версии/счётчики; mcp-session-lifecycle и search-strategies подтверждены); (2) §5 — маршрутная карта (5a маршруты по задачам + 5b единый каталог файл→сервис→повод→глубина, слит из бывших 5b/5c, helperium-go отдельным блоком, номенклатура сервисов согласована с §4, артефакты отдельным блоком); (3) §7 — секции «🛑 Когда остановиться и спросить» и «📝 Как задокументировать новую фичу», контракт метки `Last verified` (коммит проверки, не текущий), контракт CHANGELOG (только при коммите); (4) CI-чек `infra/scripts/check_docs_paths.py` (мёртвые пути + сироты: каждый док должен упоминаться в AGENTS.md) + джоба `docs-links` + `make ci-docs`.
- **chore:** убраны из трекинга артефакты мутационных тестов (`report.json`, 44 MB) — коммит `317d5ca`.
- **fix(ci):** починены сломанные DSN-пути + отсутствующий `curl` в mcp-gateway image (`4f1c0db`).
- **chore(admin-dashboard):** untrack компилируемый бинарник `bin/admin-dashboard` (`6e8fe1e`).

## 2026-08-06

- **refactor:** реструктуризация репозитория — сервисы в `services/`, инфраструктура в `infra/` (`e05acee`, `ade9e5c`, `7e42eb8`, `a32a4fa`).
- **test(e2e):** тесты перенесены в `services/agent-db/tests/`, e2e-контур в agent-db.
- **fix(bench):** применены фиксы data-service аудита, бенч 81.6% → **93.9%** (`c627b84`).
- **feat(bench):** детерминированный core benchmark без LLM-судьи (`c2c6619`).
- **chore:** удалён дублирующийся `MONITORING`-док.

## 2026-08-05

- **refactor(tests):** расширяемая e2e-архитектура — `TestTenant` + factory fixtures, автогенерация сценариев БД (`40860c5`).

## 2026-08-03

- **refactor(data-service):** LLM-first tool surface — N пер-энтити `filter_*` + 5 консолидированных `db_*` (`2cad540`).
- **refactor(data-service):** удалён мёртвый код (BuildFind/BuildList, ReadOnlyDB, expression-конструкторы, EntityResolver map-методы, coerceValue, pagination-хелперы, SetAuditRecorder, SwaggerHandlerWithTenant) — минус 1602 строки (`59a364b`).

## 2026-08-02

- **refactor(data-service):** удалён write-tool approval flow (ApprovedTools/approve/pending) (`b17b910`).
- **refactor(data-service):** post-refactor аудит-фиксы (C1-C6/M1-M7/R1-R9), config v4, удалён legacy find/list (`2e58d42`).

## 2026-07-30

- **audit(api-service):** 12 фиксов — exception-safe history, token budget pre-check, type-based classify_error, ErrorContext, LiteLLM cost, safety net, spending persistence, composite tenant spending, homoglyph guard, MCPClient circuit breaker + TTL GC, ProviderPool TOCTOU (`78ec03d`).

## 2026-07-29

- **docs:** AGENTS.md/APPEND_SYSTEM.md ужаты на 55%, вычищены устаревшие README, добавлены verification-маркеры (`e9e3066`).
