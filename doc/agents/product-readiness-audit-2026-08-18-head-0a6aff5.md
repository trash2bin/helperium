# Аудит готовности Helperium к пилоту — HEAD `0a6aff5`

Этот документ читать при планировании пилота, исправлении core-пути агента или принятии решения о release. Он фиксирует независимую проверку текущего `main` после аудита `14d3758`; исторические отчёты остаются доказательствами своих дат и не заменяют этот verdict.

> **Release verdict: NO-GO.** Архитектура core-пути в целом соответствует назначению продукта, а unit и contract coverage сильны. Однако на фактическом native-стеке не проходит базовый onboarding read-only SQLite tenant, обязательный E2E-набор каскадно красный, а штатная demo-конфигурация указывает на отсутствующий `autoparts`. Следовательно, центральное обещание «подключить существующую SQL БД без кода и показать grounded-ответ на сайте» на текущем окружении не доказано.

## Контекст и методика

Проверка проведена на чистом рабочем дереве `main`, HEAD `0a6aff5054e569ef0b81014511287edbf876dcde` от 2026-08-18. Изучены технический паспорт, service README, агентный pipeline, config generation, benchmark/E2E артефакты, текущие live HTTP-контракты. На подключённой macOS-машине запущен штатный `dev.sh` stack. Не выполнялся платный вызов внешней LLM: это не требуется для доказательства воспроизводимой native E2E-поломки, а fresh paid benchmark следует выполнять только после восстановления P0 и с отдельным бюджетом.

| Проверка | Результат | Доказательство |
|---|---:|---|
| Core benchmark evaluator | **Green** | `107 passed in 0.17s` |
| Go suites data-service + MCP gateway | **Green** | `make ci-test-go` прошёл все пакеты |
| Admin build + contract/unit tests | **Green** | `73 passed`; build успешен |
| E2E collection | **131 collected** | Native pytest collection |
| Native no-LLM E2E | **Red** | `20 failed, 12 passed, 99 errors` за 5.23 s |
| Live health endpoints | **Green, недостаточно** | :8080–:8085 отдают 200 |
| Live demo configured tenant | **Red** | `autoparts` отсутствует в data-service |
| Codebase-memory graph | **Unavailable** | MCP binary отсутствует в окружении; выводы подтверждены source, тестами и live endpoints |

## Соответствие продуктовой цели

Helperium реализует нужную продуктовую конструкцию. Агентный pipeline делает input guard, tool discovery через MCP, итерации LLM/tool-call, output guard, fallback и сохранение истории. Data-service строит конфиг и MCP surface из SQL-схемы, ограничивает data path read-only обёрткой, а MCP gateway держит tenant-scoped Streamable HTTP sessions. Embed widget и административный контур также реально существуют.[1] [2] [3]

Но пилотная ценность измеряется не существованием компонентов, а **сквозным outcome**: новый клиент подключает свою БД, генерируется конфиг, agent получает разрешённые инструменты, виджет показывает данные и возвращает grounded ответ. Этот outcome сейчас блокирован уже на первом шаге native onboarding. Поэтому текущая оценка — **design fit: высокий; demonstrated product fit: низкий**.

## P0 — native onboarding read-only SQLite не работает

На штатном running stack `POST /admin/tenants` для существующего SQLite fixture с `read_only: true` возвращает HTTP 500. Воспроизводимый ответ содержит `unable to open database file: out of memory (14)` и показывает DSN, в который добавлены write PRAGMA: `journal_mode(WAL)`, `synchronous(NORMAL)`, `busy_timeout(5000)`, `foreign_keys(1)`.

Первопричина находится на границе контракта. `buildTenantInstance` всегда сначала подключает основной `data_source.dsn`; `read_only: true` лишь запрещает write endpoints и оборачивает query connection. Без явного `readonly_dsn` флаг не преобразует SQLite DSN в `file:...?...mode=ro`, поэтому `SqliteAdapter.ensurePragmaParams` считает DSN read-write и добавляет WAL. Адаптер умеет убрать write PRAGMA для **уже заданного** `mode=ro` или `immutable=1`, но этот путь не достигается стандартным onboarding payload.[4] [5]

| Impact | Acceptance criterion | Приоритет |
|---|---|---:|
| Клиент не может подключить fixture/существующую SQLite БД; E2E каскадно рушится | Регистрация `read_only: true` без ручного `readonly_dsn` проходит на macOS/Linux; manifest и query работают | P0 |
| Read-only policy не соответствует connection-mode | Контракт явно определяет, какой DSN используется для introspection и query; WAL не добавляется на DB-level RO path | P0 |
| Unit test создаёт `file:?mode=ro` вручную и не ловит onboarding integration gap | Новый regression test вызывает реальный `POST /admin/tenants` с обычным SQLite DSN + `read_only: true` | P0 |

## P0 — demo runtime drift

В `.env` активны `DEFAULT_TENANT_ID=autoparts` и `DEMO_TENANTS=autoparts`, а `/api/tenants` ожидаемо предлагает `autoparts`. В текущем data-service health зарегистрированы только `default`, исторические `e2e-*` и `test-grep-debug`; `autoparts` отсутствует. Попытка загрузить manifest для `autoparts` завершилась 404. При этом `default` tenant честно отдаёт read-only manifest и данные через demo proxy (`/api/data/categories?pattern=Книги` → 200), то есть proxy/UI path сам по себе жизнеспособен.[6] [7]

Это не просто локальная косметика. В Admin API также видны сохранённые агенты, привязанные к `autoparts`, но среди runtime tenant его нет. Экран dashboard остаётся HTTP 200 и сообщает девять healthy tenant, что способно создать ложное впечатление готовности демонстрации.

| Impact | Acceptance criterion | Приоритет |
|---|---|---:|
| Посетитель demo получает `Failed to load entities` вместо каталога и чата | Startup/readiness проверяет configured tenant, manifest и один read endpoint | P0 |
| Admin state и demo/agent state расходятся | Один bootstrap source регистрирует tenant и синхронно создаёт demo/agent config | P0 |
| Случайные `e2e-*` загрязняют admin UX | Test tenant lifecycle очищает state либо использует изолированный data dir | P1 |

## Benchmark: сильный evaluator, не текущий KPI

Harness и его регрессии находятся в хорошем состоянии: текущий deterministic набор прошёл `107/107`. Он проверяет verdict `CORRECT/PARTIAL/WRONG/ERROR`, error taxonomy, retrieval/answer defects, SKUs, false uncertainty, lost total, budget/loop/dedupe, SSE parsing и infra aggregation.[8]

Однако последний canonical live artifact относится к коммиту `c0f3d62`, содержит 49 cases с 45 CORRECT, 2 PARTIAL, 1 WRONG и 1 ERROR. Его заявленный `verdict_pass_rate` — 95.9% — **не может быть release KPI для `0a6aff5`**: между коммитами менялись transport, tenant policy, filter contract и benchmark aggregation. В raw historical report timeout также записан при `infra_error_rate=0.0`; текущие тесты уже фиксируют правильную классификацию, но старый report этим не становится.[9] [10]

Следующая корректная последовательность: сначала P0 onboarding + demo gate, затем 5–10 case budgeted live smoke с commit, dataset checksum, provider/model, policy, token/cost/duration и `infra_error_rate`; только после green smoke — full 49-case run.

## E2E coverage: содержательно широкое, но не release evidence

Набор из 131 no-LLM теста покрывает CRUD и persistence tenant, isolation, generated tool surface, search strategies, Streamable HTTP security, composite routing и ScriptedLLM-путь `SSE → tool_call → tool_result → final` без внешней модели.[11] Этот дизайн корректно отделяет детерминированную интеграцию сервисов от нестабильности провайдера.

Проблема — не число тестов, а невыполненный gate. На реальном native stack полный suite дал `20 failed, 12 passed, 99 errors`, и targeted test подтвердил раннюю падение tenant registration. Следовательно, нельзя трактовать Go/unit green как доказательство работающего production path. Кроме того, в текущем E2E нет browser-driven acceptance для `tenant selector → manifest → tabs/table → widget input → SSE tool call → final`; HTTP/proxy проверки это не заменяют.

| Сценарий | Текущее доказательство | Verdict |
|---|---|---:|
| Новый SQLite tenant → rewrite → manifest → query | Есть intent и тесты, native runtime падает на registration | **Red** |
| Agent → MCP → data-service → SSE final без real LLM | ScriptedLLM есть, но full native gate блокирован onboarding | **Red** |
| Composite tenant isolation / auth / Origin | Сильные deterministic tests; итоговый suite red | **Yellow** |
| Demo visitor получает data + grounded answer | Existing `default` proxy proof, configured `autoparts` отсутствует | **Red** |
| Admin сохраняет config и показывает runtime state | API/contract tests green; browser acceptance отсутствует | **Yellow** |
| Tool choice и качество real LLM | Historical live run только на старом commit | **Yellow/Red** |

## UI и операционная оценка

Все шесть сервисов running stack были healthy по базовым endpoint. Admin HTML и `/api/dashboard`, `/api/agents`, `/api/tenants` возвращают 200; Admin build и 73 tests зелёные. Demo root отдаёт HTML 200, а proxy подходит для существующего `default` tenant. Это доказывает, что сервисы поднимаются и часть контуров доступна, но не доказывает happy path configured demo.

Полноценный visual browser walkthrough не проведён: sandbox browser не имеет доступа к loopback подключённой macOS-машины, а доступный Playwright MCP не имеет установленного Firefox и не предоставляет рекомендованный install tool. Поэтому выводы о UI основаны на live HTTP/proxy трассах, исходном frontend и contract tests; это ограничение следует закрыть browser acceptance в CI, а не скрывать его health checks.

## Технический долг и мёртвые/неполные контуры

| Контур | Статус | Риск и решение |
|---|---|---|
| `datasource.SQLDataSource` | Частично живой | Роутер использует только `Schema()`. `Search`, `Filter`, `GetByID`, `Count`, `Distinct` намеренно возвращают errors, так как production использует strategies/handlers. Сократить интерфейс до Schema-provider либо завершить методы; текущая широкая абстракция вводит в заблуждение. P2. |
| `ReadonlyDSN` | Недоиспользованный механизм | Отдельный RO connection существует и тестируется, но standard `read_only` onboarding не генерирует RO DSN. Либо сделать его автоматически обязательным для SQLite, либо удалить иллюзию DB-level isolation из default flow. P0. |
| `demo/web` | Dev-only, но product evidence | README и AGENTS честно маркируют его dev-only, но проект использует его как доказательство UI. Изолировать demo bootstrap от дев-артефактов и проверять readiness. P1. |
| Legacy compatibility | Контролируемый, но требует бюджета | MCP legacy transport удалён и защищён regression tests; в api-service остались legacy parsing/backlog/env config paths. Нужна таблица владельцев и sunset criteria, но немедленное удаление не оправдано. P2. |

## План восстановления

| Волна | Результат | Обязательные доказательства |
|---:|---|---|
| 0 | Зафиксировать текущий verdict NO-GO; не публиковать 95.9% как KPI HEAD | Этот отчёт, native E2E log, сохранённый reproduction 500 |
| 1 | Исправить SQLite read-only onboarding и добавить integration regression | Native `test_one_tenant_via_fixture` green на macOS/Linux; full no-LLM E2E green |
| 1 | Восстановить единую demo truth для `autoparts` или намеренно использовать существующий tenant | configured manifest + collection 200; readiness failure при drift |
| 2 | Добавить browser acceptance | Screenshots/trace: tenant selector, table, widget, tool call и final; Admin saved/runtime state |
| 3 | Запустить ограниченный live smoke на текущем commit | Полная provenance и economics; budget approved |
| 4 | Рационализировать SQLDataSource/legacy scope | ADR с владельцем, сроком и regression suite |

## References

[1]: [Технический паспорт и data flow](../../AGENTS.md)
[2]: [Основной README и продуктовое позиционирование](../../README.md)
[3]: [Agent orchestrator и pipeline](../../services/api-service/src/api_service/agent/orchestrator.py)
[4]: [Создание runtime tenant instance](../../services/data-service/internal/server/tenant_lifecycle.go)
[5]: [SQLite adapter и DSN PRAGMA policy](../../services/data-service/internal/datasource/sqlite_adapter.go)
[6]: [Demo web proxy](../../demo/web/server.py)
[7]: [Demo frontend tenant/manifest flow](../../demo/web/static/app.js)
[8]: [Core benchmark tests](../../services/agent-db/tests/test_bench_core.py)
[9]: [Canonical live benchmark raw report](../benchmark/runs/2026-08-16-nvidia-nim-rebuilt-final-full-run-raw-report.json)
[10]: [Benchmark run registry](../benchmark/runs/README.md)
[11]: [Testing guide](testing-guide.md)

---
**Last verified:** 2026-08-18 (HEAD `0a6aff5`) — изучены core pipeline/config/MCP/data-service paths; `107/107` benchmark tests, Go suites и Admin 73 tests green; 131 E2E collected; native stack E2E red (`20 failed, 12 passed, 99 errors`) с воспроизведённым SQLite onboarding 500; demo/admin HTTP contracts проверены. Paid live LLM benchmark и visual browser walkthrough не выполнялись.
