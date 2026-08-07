# CHANGELOG.md

> Единый журнал значимых изменений проекта. Хронология, от новых к старым. Подробности по каждому пункту — в соответствующих README/doc/agents/*.

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
