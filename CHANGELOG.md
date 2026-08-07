# CHANGELOG.md

> Единый журнал значимых изменений проекта. Хронология, от новых к старым. Подробности по каждому пункту — в соответствующих README/doc/agents/*.

## 2026-08-07

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
