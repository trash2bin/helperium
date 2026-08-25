# План исправления блокирующих дефектов Helperium — 2026-08-18

> **Статус:** 🗃️ исполненный historical remediation plan. Он фиксирует исходные причины и intended change sets на `ed421c9`, а не текущий release verdict.
>
> **Реализация после плана:** основной core-readiness набор вошёл в `14d3758`; затем `bd5adb5`, `c1a20a7`, `ccce086`, `f9df354` и `ff2d08b` закрыли SQLite onboarding, Docker isolation, backend/SSE resilience и API/MCP perimeter hardening. Последняя локальная deterministic проверка: `make ci` passed и clean Docker E2E 137 passed.
>
> **Что остаётся внешним/следующим:** GitHub branch protection, fresh budgeted live-model benchmark, browser acceptance на deployed domain, edge/alerting/rollback game day и расширенный RAG/prompt-injection security assessment.

## Historical progress snapshot

| Контур | Статус | Проверка |
|---|---|---|
| Single-tenant MCP names | **Готово** | Новый registry policy сохраняет tenant routing, но префиксует имена только в composite scope; focused E2E прошёл. |
| Safe default и Admin runtime indicator | **Готово** | `config.example.json` read-only; Admin различает saved metadata и runtime manifest; browser подтвердил 0/5 и read-only/read-only. |
| Demo и native DX | **Готово** | `strategy` формирует tabs; native restart прошёл без ручного RAG `PYTHONPATH`; embed bootstrap использовал lockfile. |
| E2E limiter, auth/CORS и CI profile | **Готово** | `e2e-up` поднимает secure test stack с 1000/1000, test-only bearer token и явным Origin allowlist; CI override изолирован от production compose; full no-LLM suite: 131 passed, без skipped. |
| Benchmark infra invariant | **Готово** | `error_source=infra` учитывается без legacy class; 107 deterministic benchmark tests зелёные. |
| Admin aggregate health | **Готово** | Backend отдаёт `ok`/`degraded`/`unavailable`; финальный browser показывает зелёный indicator. |
| Product-flow E2E | **Готово** | Добавлены default→demo manifest/strategy preview, single-tenant Streamable MCP и authenticated Admin health regressions; focused test: 3 passed. |
| Branch protection и fresh paid live benchmark | **Внешнее действие** | Нужны настройки GitHub repository и отдельный controlled model run; они не должны подменяться локальным кодовым изменением. |

## Historical correction to the previous audit

Прежний аудит ошибочно интерпретировал поле `tools` вместо `mcp_tools` в live `/mcp/manifest`. Повторная проверка показала, что runtime `default` публикует **5 инструментов**: `db_map`, `db_describe`, `db_search`, `db_get`, `db_related`. Следовательно, **отсутствие runtime tools не является дефектом**.

Реальный дефект другой: Config page считает сохранённое поле `cfg.mcp_tools`, тогда как Tools page и MCP gateway используют runtime `mcp_tools`, сгенерированные по `endpoints`. Поэтому один экран показывает `0`, другой — `5`, но gateway получает реальные инструменты. Это надо исправить как проблему терминологии и source-of-truth в Admin UI, а не как outage генератора manifest.[1] [2] [3]

| № | Дефект или риск | Подтверждённый статус | Приоритет |
|---:|---|---|---|
| 1 | Single-tenant MCP даёт префиксованные имена инструментов | Воспроизводится локально и в CI | P0 |
| 2 | Default config запускается в `read_only: false` | Подтверждено config и Admin UI | P0 |
| 3 | Красный E2E job не блокирует попадание изменений в `main` | `main` не protected | P0 |
| 4 | Config UI смешивает persisted и runtime tool counts | Подтверждено кодом и UI | P1 |
| 5 | Demo не строит tabs для endpoint `strategy` | Подтверждено browser и frontend-кодом | P1 |
| 6 | Native startup не bootstrap-ит RAG/embed полностью | Воспроизводится в clean checkout | P1 |
| 7 | E2E локально пересекается с production rate limit | Воспроизводится при 10 RPS / burst 20 | P1 |
| 8 | Live benchmark report не является release-quality evidence | Historical commit и нулевая telemetry | P1 |
| 9 | Admin overview не даёт агрегированного health | Наблюдался `Статус —` при доступных API | P2 |

## Принципы внедрения

> **Не лечить тесты вместо контракта.** Single-tenant обязан публиковать `db_map`, `db_search` и другие неперефиксные имена. Менять E2E ожидания на `{tenant}__db_map` запрещено: это сломает публичный single-tenant contract и не решит текущую причину.[4] [5]

Каждый пункт должен быть отдельным reviewable change set с собственным regression test. P0-изменения нельзя объединять с RAG, refactor UI либо новыми продуктовым возможностями: иначе снова станет невозможно понять, что именно сломало agent→DB path.

| Правило | Практическое следствие |
|---|---|
| Runtime является источником истины для agent tool surface | `data-service /mcp/manifest` и MCP `tools/list` — проверяемая пара контрактов. |
| Stored config остаётся источником истины для редактирования | Admin UI обязан явно маркировать, что это сохранённая декларация, а не runtime count. |
| Safe by default | Пример/demo config стартует read-only; write требует явного, отдельного change flow. |
| CI является release gate | Любой required job обязан быть protected на `main`; зелёный локальный smoke не заменяет merge protection. |
| Benchmark измеряет конкретную сборку | Raw report без commit/model/dataset/telemetry не участвует в продуктовых KPI. |

## P0-1. Исправить naming policy для single-tenant MCP

### Причина

В `createServerForTenant()` в registry передаётся реальный `tenantID`, что необходимо для tenant-scoped data call. Но `Registry.RegisterAll()` использует тот же признак для решения о префиксе имени: при любом непустом `tenantID` имя становится `{tenant}__{tool}`. В результате single-tenant получает composite-формат имени, хотя комментарии и E2E contract требуют обычные `db_map`, `db_search` и т. п.[4] [5]

Это смешение двух независимых сущностей: **контекст выполнения** (`tenantID` нужен handler и audit labels) и **политика представления имени** (`prefixToolNames` нужен только для composite scope).

### Предлагаемая реализация

В `services/mcp-gateway/internal/tools/tools.go` добавить явный флаг, например `prefixToolNames bool`, в `Registry` или в отдельную `RegistryOptions`. `tenantID` сохранить без изменений для `registerOne()` и `makeHandler()`.

| Фабрика | `tenantID` | `prefixToolNames` | Ожидаемое имя |
|---|---|---:|---|
| `NewRegistry(cfg)` | `""` | `false` | `db_map` |
| `NewTenantRegistry(cfg, "tenant-a")` | `tenant-a` | `false` | `db_map` |
| `NewPrefixedRegistry(cfg, "tenant-a")` | `tenant-a` | `true` | `tenant-a__db_map` |

`RegisterAll()` должен принимать решение только по `prefixToolNames`. Обработчик продолжает получать реальный `tenantID`; нельзя передавать пустой ID ради имени, иначе single-tenant call потеряет tenant routing. Нужно обновить устаревшие комментарии `makeHandler()`, где single mode описан как получение ID из context, хотя current creation path передаёт closure tenant ID.

### Обязательные тесты

| Уровень | Новый или изменённый тест | Критерий |
|---|---|---|
| Go unit | `NewTenantRegistry` + `RegisterAll` | Список содержит `db_map`, не содержит `tenant-a__db_map`; handler сохраняет `tenant-a`. |
| Go unit | `NewPrefixedRegistry` + `RegisterAll` | Список содержит `tenant-a__db_map`, не содержит голый `db_map`. |
| Streamable HTTP E2E | existing `test_streamable_http_lists_tools_and_calls_read_only_tool` | `tools/list` содержит `db_map`; вызов `db_map` успешен. |
| Composite E2E | existing composite suite | Два tenant scope дают только `a__db_map` и `b__db_map`; cross-tenant call не проходит. |
| Agent E2E | ScriptedLLM v5 chain и isolation tests | После фикса больше нет каскада TaskGroup/`NoneType` failures. |

### Смежное решение, которое нужно зафиксировать до merge

RAG tools сейчас регистрируются отдельно от prefix logic. При composite server `RegisterAll()` вызывается по одному разу на tenant, поэтому возможна повторная регистрация одинаковых `search_documents`, `list_documents`, `get_rag_context`. До изменения naming policy нужно принять и закодировать один из вариантов: **(а)** RAG tools регистрируются один раз на composite server и имеют явную scope policy; либо **(б)** RAG tools тоже tenant-prefix-ятся. Нельзя оставлять это неявным побочным эффектом цикла registration.[5]

### Definition of Done

Полный E2E suite зелёный без изменения expected names; latest GitHub CI зелёный; один browser/API acceptance доказывает `widget → SSE → db_* tool call → grounded final` для отдельного read-only tenant.

## P0-2. Сделать default demo безопасным и явным

### Причина

`data-service` без `--config`/`DS_CONFIG` грузит `specs/config.example.json`, где `data_source.read_only` задано как `false`. Это именно конфигурация, используемая native and compose default path, а не нормальный tenant-create flow: создание tenant через admin уже устанавливает `read_only=true`.[6] [7]

### Предлагаемая реализация

Изменить demo/example config на `read_only: true`. Если проекту нужен write-demo, вынести его в **явный opt-in** config, например `specs/config.example.write-demo.json`, с предупреждением в README. Нельзя оставлять write mode в файле, который является неявным default для команды запуска.

После этого привести Admin Config page к двум чётким разделам:

| Поле UI | Источник | Отображение |
|---|---|---|
| Saved configuration | `GET /api/tenants/{id}/config` | «Сохранённая декларация»; `mcp_tools` может быть 0/legacy и не должен называться runtime count. |
| Runtime tool surface | `GET /api/tenants/{id}/manifest` | «Активные MCP-инструменты»; count, timestamp refresh, список/хеш имён. |
| Effective access mode | runtime manifest `read_only` + config | Если расходятся — `degraded/config mismatch`, а не зелёный статус. |

Минимальный UI fix не требует менять генератор manifest: Config page должен загружать manifest параллельно с config и показывать `runtimeMcpTools` отдельно. Tools page уже использует runtime manifest; её не надо переводить на stored config.[1] [2]

### Обязательные тесты

| Уровень | Проверка |
|---|---|
| Config loader/Go | Default `config.example.json` загружается с `read_only=true`. |
| Data-service integration | Default manifest содержит `read_only=true`, методы записи не попадают в exposed API/MCP tools. |
| Admin backend | Созданный tenant и default config не дают write action viewer/agent scope. |
| Admin browser/unit | Config page явно различает `saved_mcp_tools` и `runtime_mcp_tools`; не выводит «0 MCP-инструментов», когда manifest содержит 5. |
| End-to-end security | Попытка write через agent/MCP получает controlled rejection; обычный read path остаётся успешным. |

### Definition of Done

Новый чистый checkout запускается с read-only default; dashboard показывает понятный effective mode; admin Tools page и MCP `tools/list` совпадают по runtime names; documented write-demo доступен только по отдельному явному параметру.

## P0-3. Восстановить настоящий release gate

### Причина

E2E job текущего HEAD уже failure, но branch protection API вернул `Branch not protected`. Значит GitHub Actions технически обнаруживает проблему, но `main` не защищён required checks.[8] [9]

### Предлагаемая реализация

Настроить branch ruleset/branch protection для `main` через GitHub settings или organization policy:

1. Require pull request before merge.
2. Require up-to-date branch before merge.
3. Require как минимум `E2E tests (no LLM)`, `Go tests`, `Python tests`, `Docs — мёртвые пути`, `JS lint`.
4. Enforce for administrators; запретить direct push, кроме документированного break-glass процесса.
5. Требовать завершение всех checks именно на merge commit/merge queue, а не на старом head SHA.

Это конфигурация репозитория, не кодовый commit. До включения ruleset нужно устранить P0-1, иначе новый gate закономерно будет держать main красным.

### Definition of Done

Невозможно влить PR с failed E2E. Тестовый PR/commit с намеренно падающим E2E получает blocked merge, а successful rerun на актуальном merge commit разрешает merge.

## P1-1. Исправить demo manifest-to-tabs контракт

### Причина

`demo/web/static/app.js` допускает только `list`, `find`, `custom_query` как источники collection tab. Runtime config использует `op: "strategy"` для collection GET endpoints. При zero matching tabs функция просто делает `return`, оставляя `Loading entities…` навсегда.[10]

### Предлагаемая реализация

Расширить список допустимых collection operations как минимум до `strategy`; дополнительно проверять, что endpoint — `GET` и route не содержит path parameters. Не использовать «все GET endpoints» без фильтра: `health`/`stats` не являются entity table tabs. Если manifest корректен, но tabs пусты, UI должен показать осмысленный empty state с диагностическим ID и refresh, а не вечный loader.

### Обязательные тесты

| Уровень | Проверка |
|---|---|
| Frontend unit (jsdom/Vitest) | Manifest со `strategy` `/products` создаёт tab и запускает `loadData()`. |
| Frontend unit | Manifest без collection endpoints заменяет loader на deliberate empty state. |
| Browser acceptance | Demo default открывается, `Loading entities…` исчезает, видна первая entity table. |
| Proxy test | Existing `/api/manifest` proxy sample с `strategy` остаётся проходить. |

## P1-2. Сделать native startup воспроизводимым

### Причина и минимальный выбор

Native launcher вызывает `uv run --package rag python -m rag.service`, но RAG package layout/configuration не предоставляет импортируемый top-level `rag` после обычного `uv sync`. Также launcher запускает `npm run build` для embed, не выполняя `npm install`/`npm ci` в этом package. Это делает «одна documented команда» зависимой от побочных локальных состояний.[11] [12]

Нужно выбрать **один** поддерживаемый packaging layout для RAG. Предпочтительный вариант — conventional package directory `services/rag/rag/` либо `src/rag/` с соответствующей Hatch configuration. Временный `PYTHONPATH` не должен быть production/native workaround. Для embed launcher должен детерминированно применять lockfile: `npm ci` при отсутствующем `node_modules` или явная отдельная documented `make bootstrap`/`./infra/scripts/bootstrap.sh`, которую `dev.sh` проверяет и даёт точную ошибку.

### Обязательные тесты

1. В чистой временной директории/CI job: `uv sync --frozen` → `uv run --package rag python -c 'import rag'` успешно.
2. Clean native smoke: remove `.venv`, package `node_modules`, `.data/pids`; bootstrap → `dev.sh start`; все 6 health endpoints зелёные.
3. Повторный `dev.sh start` должен быть идемпотентен и не скачивать неожиданный package `tsc`.

## P1-3. Отделить E2E traffic profile от production rate limit

Production limiter с default 10 RPS / burst 20 применяется ко всем Streamable HTTP методам на IP. E2E быстро открывает/закрывает много sessions с `127.0.0.1`, поэтому часть локального failure была rate-limit noise. Его нельзя «лечить» отключением limiter в production.[13]

Нужно добавить явный test profile в compose CI и native `dev.sh e2e`: например, `MCP_RATE_LIMIT_RPS=1000`, `MCP_RATE_LIMIT_BURST=1000` **только** для test process. Production values остаются безопасными. Альтернатива — deterministic pacing/backoff в E2E helper, но она делает suite медленнее и менее надёжным; profile является предпочтительным первым шагом.

Обязательны две независимые проверки: middleware unit-test продолжает возвращать 429 на production values, а full E2E test profile не имеет rate-limit log entries и не меняет production compose defaults.

## P1-4. Вернуть benchmark в статус доказательства, а не исторической цифры

Canonical raw report относится к `c0f3d62`, содержит 49 cases с нулевыми duration/token/cost telemetry и case с `error_source=infra`, который не участвует в `infra_error_rate`, так как aggregation смотрит только на `ErrorClass.INFRA_ERROR`.[14] [15]

### Предлагаемая реализация

Сначала добавить invariant в aggregator: если `error_source == "infra"`, report обязан содержать `ErrorClass.INFRA_ERROR` либо count его отдельным способом; нельзя публиковать `infra_error_rate=0` при infra case. Затем в raw-report schema добавить `telemetry_complete` и `provenance_complete`. KPI promotion запрещён, если любой из них false.

После P0-1 запустить новый live smoke не менее 10 high-signal cases на зафиксированных `commit`, provider/model identifier, dataset checksum, policy version, seed, wall-clock duration, tokens и cost. Полный 49-case live run выполнять только после smoke green; результат не сравнивать напрямую с historical 95.9 % без одинаковой версии policy и транспорта.

## P2. Сделать Admin health полезным

Overview не должен показывать неопределённый `Статус —`, если downstream calls уже проверены. Backend `/api/dashboard` должен возвращать явные состояния каждого dependency (`healthy`, `degraded`, `unavailable`, `unknown`) и timestamp/ошибку. Frontend показывает aggregate policy: all healthy → `healthy`; required service degraded → `degraded`; no usable data → `unknown`. Это не блокирует P0, но снижает MTTR и риск ложного «всё работает» во время demo.

## Порядок работ и зависимости

| Волна | Изменения | Можно параллельно | Блокирует |
|---:|---|---|---|
| 0 | Freeze feature merges; открыть tracking issues; включить временный manual approval на main | Да | Всё release-facing |
| 1 | P0-1 MCP prefix policy и её unit/E2E contract | Нет, это первый кодовый фикс | Full E2E, agent path, benchmark smoke |
| 1 | P0-2 safe default config | Да, независим от naming | Demo/public exposure |
| 1 | P0-3 GitHub branch protection | Да, но включить after P0-1 branch is green | Повторное попадание regressions в main |
| 2 | P1-1 demo strategy tabs; P1-2 native bootstrap | Да | Browser demo и DX readiness |
| 3 | P1-3 test profile; P1-4 telemetry invariant/fresh smoke | Да после P0-1 | Reliable QA evidence |
| 4 | P2 admin health, browser suite expansion | Да | Operational maturity |
| 5 | Controlled pilot gate | Нет | External customer use |

## Общий acceptance gate перед pilot

Ни один single metric не является достаточным. Pilot допустим только при одновременном выполнении всех условий:

| Gate | Проверяемый результат |
|---|---|
| MCP contract | Single tenant видит/вызывает `db_map`; composite видит только prefixed data tools; tenant routing сохраняется. |
| Data safety | Default и new tenant read-only; write attempt rejection проверен end-to-end. |
| E2E | Full no-LLM suite зелёная на merge commit, без rate-limit noise. |
| UI | Demo table строится из `strategy`; widget выдаёт grounded answer в scripted deterministic flow; empty/error state понятен. |
| Admin | UI подписывает saved vs runtime state; runtime tools совпадают с MCP list; aggregate health actionable. |
| DX | Clean bootstrap поднимает все шесть сервисов без ручного `PYTHONPATH`/`npm install`. |
| Benchmark | New report имеет complete provenance/telemetry; infra rate invariant проходит; live smoke green. |
| Governance | Main branch protected required checks и PR-only merge. |

## References

[1]: ../../services/data-service/internal/runtime/handlers/mcp_manifest.go "Runtime manifest and generated `mcp_tools`"
[2]: ../../services/admin-dashboard/src/domains/config.ts "Saved config summary implementation"
[3]: ../../services/admin-dashboard/src/domains/tools.ts "Tools page runtime manifest implementation"
[4]: ../../services/mcp-gateway/cmd/main.go "Single-tenant versus composite server construction"
[5]: ../../services/mcp-gateway/internal/tools/tools.go "Registry naming and tenant routing"
[6]: ../../services/data-service/cmd/server/main.go "Default config selection"
[7]: ../../specs/config.example.json "Unsafe default `read_only` value"
[8]: ../../.github/workflows/ci.yml "E2E is defined as a CI job"
[9]: https://github.com/trash2bin/helperium/actions/runs/32079886408 "Current HEAD E2E job failure"
[10]: ../../demo/web/static/app.js "Manifest-to-tabs implementation"
[11]: ../../services/rag/pyproject.toml "RAG project script and Hatch packaging"
[12]: ../../infra/scripts/dev.sh "Native launcher build order"
[13]: ../../services/mcp-gateway/cmd/ratelimit.go "Rate limiter defaults and environment override"
[14]: ../../services/agent-db/agent_db/bench/report.py "Benchmark aggregation"
[15]: ../../services/agent-db/agent_db/bench/evaluator.py "Infra error classification"

**Last verified:** 2026-08-18 on `ed421c9615381219671ede176c98135852a15ade`; runtime `default.mcp_tools` rechecked after correction by Manus AI.
