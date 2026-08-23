# PM и технический аудит готовности Helperium — 2026-08-18

> **Статус:** архивный независимый аудит текущего `main`.
>
> **Проверенный коммит:** `ed421c9615381219671ede176c98135852a15ade`.
>
> **Вердикт:** **не готов к controlled pilot и тем более к заявлению production-ready.** Техническое ядро и административная поверхность существуют, но текущий `main` имеет блокирующий регресс ключевого single-tenant MCP-пути, а локальная demo-конфигурация противоречит заявленному безопасному сценарию.
>
> **Поправка 2026-08-18:** прежняя проверка неверно читала поле `tools` вместо `mcp_tools`. Повторная проверка live `/mcp/manifest` для `default` подтвердила 5 runtime-инструментов: `db_map`, `db_describe`, `db_search`, `db_get`, `db_related`. Вывод об отсутствии runtime tool surface отозван; остаются реальные дефекты single-tenant naming, read-write default и неясное отображение saved config против runtime manifest.[14]

## Резюме для решения

Helperium решает осмысленную задачу: переводит запрос пользователя в управляемый доступ к живой SQL-базе через сгенерированные инструменты, а не подменяет это статическим RAG. Целевая цепочка **виджет → api-service → MCP gateway → data-service → клиентская БД** соответствует pre-final цели: без правки кода подключить БД клиента, ограничить поверхность доступа и дать готовый виджет на сайт.[1] [2]

Однако на текущем `main` эта цепочка не проходит в её базовом варианте — **один tenant, один агент, один набор неперефиксных инструментов**. В коде шлюза объявлено, что single-tenant server должен публиковать обычные имена `db_map`, `db_search` и т. п.; фактически registry добавляет `{tenant}__` к любому непустому tenant ID. Это ломает discovery, вызовы инструментов, ScriptedLLM pipeline и E2E suite. Локальное воспроизведение и обязательный GitHub Actions job на `HEAD` подтверждают, что это не дефект конкретной машины аудита.[3] [4] [5]

| Контур | Оценка на `ed421c9` | Решение PM |
|---|---|---|
| SQL → config → data-service | **Жёлтый** | Контракты и генерация существуют; не выпускать без проверки runtime tool surface для каждого tenant. |
| MCP Streamable HTTP в single-tenant | **Красный** | Немедленный стоп-релиз: имена инструментов не соответствуют контракту и блокируют agent→DB path. |
| Benchmark реальной модели | **Жёлтый/красный** | Regression harness живой, но canonical live KPI устарел и его telemetry непригодна для cost/latency решения. |
| Deterministic E2E | **Красный** | 31 failed / 95 passed / 2 skipped локально; CI на `HEAD` тоже красный. |
| Demo Web и embed-путь | **Красный** | Интерфейс открывается, но вкладки данных зависают; чат в ScriptedLLM-mode даёт generic non-answer, а основная agent→DB цепочка заблокирована MCP naming regression. |
| Admin Dashboard | **Жёлтый** | Экран управления функционален и навигация понятна, но отображает конфликтующие источники правды о tool surface. |
| Безопасность default demo | **Красный** | UI показывает `Read-write` для default tenant, хотя продуктовое обещание — read-only по умолчанию. |
| DX локального запуска | **Жёлтый/красный** | Documented `uv sync` + `dev.sh start` не поднимает RAG без workaround; embed build также требует неописанной установки Node dependencies. |

## Объём и методика

Аудит выполнялся на чистом clone `trash2bin/helperium`, без правки продуктового кода и без реальных платных LLM-вызовов. Были установлены заявленные Python и Go зависимости, поднят полный локальный стек в одном sandbox, а demo и admin были открыты через браузерную автоматизацию в той же среде. Для безопасного детерминированного запуска использовались разные временные admin/viewer токены и `ScriptedLLM`; они не являются пользовательскими или production credentials.

| Проверка | Фактический результат | Что это доказывает |
|---|---:|---|
| `test_bench_core.py` | **106 passed**, 0.57 s | Evaluator, SSE parsing, verdict taxonomy и агрегация benchmark отчёта регрессируют детерминированно. |
| Полный локальный `tests/e2e/` | **31 failed, 95 passed, 2 skipped**, 92.42 s | Core E2E больше не зелёный; массовый каскад от single-tenant MCP regression воспроизведён. |
| Независимые admin/data E2E модули | **63 passed, 2 failed**, 37.82 s | CRUD/config/search largely работают; два `db_get` isolation сценария также упираются в тот же `db_search` MCP path. |
| GitHub Actions CI на `HEAD` | E2E job **failure** | Проблема уже присутствует в обязательном CI, а не обнаружена только локально. |
| Local full stack | data, RAG, MCP, API, web, admin health endpoints готовы | Сервисы можно собрать и запустить, но только после двух DX-workaround. |
| Browser audit | demo и admin загрузились | UI-доступность проверена реальным браузером, а не только HTTP/DOM unit tests. |

## P0 — блокирующие дефекты

### P0-1. Single-tenant MCP публикует неправильные имена инструментов

> **Ожидаемый контракт:** в single-tenant режиме имена инструментов не префиксуются; `{tenantID}__` используется только для composite scope.[6]

`createServerForTenant()` создаёт `NewTenantRegistry(cfg, tenantID)`. Но обе фабрики `NewTenantRegistry` и `NewPrefixedRegistry` передают непустой ID в один `newRegistry`, а `RegisterAll()` безусловно добавляет префикс при `r.tenantID != ""`. Следовательно, single-tenant фактически получает `tenant__db_map`, в то время как agent и тесты вызывают `db_map`.[5] [6]

| Проявление | Подтверждение | Воздействие |
|---|---|---|
| `tools/list` не содержит `db_map` | Minimal v2 Streamable HTTP test падает на exact assertion | Агент не может обнаружить ожидаемые data tools. |
| `db_search`, `db_get`, `filter_*` дают transport TaskGroup errors | 31 E2E failure после исключения rate limit | Основной agent→DB workflow неработоспособен. |
| `ScriptedLLM` E2E падает | Не проходят v5 tool chain, recovery и empty-call validation | CI не подтверждает даже детерминированный orchestration path. |
| GitHub CI уже красный | E2E job на run `32079886408` завершён failure | Main не является release candidate. |

**Минимальный фикс.** Отделить audit/metric tenant identity от name-prefix policy. `NewTenantRegistry` должен передавать в регистрацию признак `prefix=false`; `NewPrefixedRegistry` — `prefix=true`. Обязательно добавить unit-тесты на обе поверхности и сохранить Streamable HTTP E2E как release gate. Нельзя «чинить» это обновлением E2E ожиданий: комментарии gateway, документация и продуктовый контракт однозначно требуют неперефиксные single-tenant имена.

### P0-2. Default tenant небезопасен; Admin UI смешивает saved config и runtime manifest

В авторизованной Admin Dashboard конфигурация `default` показала **`Read-write`**, 6 entities, 17 endpoints, **0 сохранённых `mcp_tools`** и 6 custom queries. Текст UI прямо сообщает, что read-write активирует все инструменты, включая write operations. Это противоречит публичному описанию, где чтение является default и write operations требуют явного включения/подтверждения. Источник режима — default `specs/config.example.json`, который явно содержит `read_only: false`.[1] [14]

Интерпретацию tool count необходимо уточнить: Config page считает **сохранённый** массив `cfg.mcp_tools`, а Tools page и live `/mcp/manifest` используют **runtime-генерацию** из endpoints. Повторная проверка показала 5 runtime-инструментов, а не 0. Это не агентный runtime outage, но это опасный UX/data-governance дефект: один экран говорит «0 MCP tools», другой и gateway используют 5, не объясняя различие и не указывая authoritative runtime state.[14] [15] [16]

**Критерий закрытия.** Новый чистый tenant и default demo должны стартовать с `read_only=true`; config summary обязан явно маркировать saved config и отдельно показывать runtime manifest count/last refresh; Tools page и MCP `tools/list` должны совпадать по именам; отдельный E2E обязан проверять, что write methods не зарегистрированы и не вызываются через MCP.

## Benchmark: зрелый harness, но недостоверный release KPI

Детерминированная часть benchmark хорошо структурирована: 49 active autoparts cases, явные verdicts `CORRECT/PARTIAL/WRONG/ERROR`, error taxonomy и checks retrieval/answer/hallucination/refusal/tool budget.[8] Локальный 106-test core suite проходит, поэтому harness можно использовать для регрессии evaluator-а.

Canonical raw live report утверждает 45 `CORRECT`, 2 `PARTIAL`, 1 `WRONG`, 1 `ERROR` и `verdict_pass_rate` 95.9 % (47/49). Но он записан на commit `c0f3d62`, тогда как аудит проверяет `ed421c9`; между ними полностью поменялся транспорт MCP и затронуты api-service, gateway, E2E и tenant authority.[9] [10] Этот показатель нельзя использовать как текущий KPI качества.

| Проблема evidence | Наблюдение | Решение |
|---|---|---|
| Неподходящий commit | raw report: `git_commit=c0f3d62`; текущий `HEAD=ed421c9` | Fresh run на фиксированном `HEAD`, dataset, policy, model и seed. |
| Нулевая telemetry | duration/tool calls/cost — нули в summary и 49 case metrics | Помечать такой отчёт `incomplete_telemetry=true` и запрещать KPI promotion. |
| Inconsistent infra classification | Case `ERROR` имеет `error_source=infra`, но top-level `infra_error_rate=0.0`, histogram без `INFRA_ERROR` | Исправить aggregation и добавить fixture на case-level infra error. |
| Один domain, один turn | 49 кейсов только autoparts и no multi-turn | Добавить минимум два контрастных tenant fixture, multi-turn и permissions scenario. |
| Live LLM opt-in | CI проверяет ScriptedLLM, не tool selection реальной модели | Ночной бюджетный live smoke 5–10 questions после восстановления E2E. |

## E2E: сильная матрица сценариев, но она сейчас сигнализирует релизный блокер

Набор насчитывает **128 тестов**, хотя README всё ещё заявляет 124; это небольшое, но показательное расхождение документации с текущей реальностью.[3] Содержательно покрываются tenant lifecycle, config persistence, data isolation, composite MCP, dynamic/generated tools, required params, ScriptedLLM, search strategies и Streamable HTTP.

Проблема не в отсутствии E2E как таковых. Проблема в том, что regression уже есть в suite, CI его ловит, а `main` всё равно содержит merge с красным mandatory job. Это означает отсутствующий release gate: технически CI умеет сообщить о блокере, организационно он не останавливает интеграцию.

Первый локальный full run дополнительно падал сильнее из-за MCP rate limiter с defaults 10 RPS / burst 20: быстро создаваемые Streamable HTTP sessions с одного localhost превышали квоту. После временного audit-only увеличения лимитов число failure сократилось с 35 до 31, что отделяет транспортную чувствительность тестового окружения от основной naming regression. Требуется явный E2E profile с безопасным высоким внутренним лимитом либо deterministic backoff в helper; production rate limit не следует отключать.

## Demo и Admin UI: что реально увидит пользователь

Demo Web загрузился в браузере и отображает selectors Database/Agent, таблицу данных и встроенный Shadow DOM widget. Но data section остаётся в состоянии **`Loading entities…`**, хотя `/api/manifest` вернул HTTP 200 с entities. Причина в frontend contract: `app.js` строит tabs только для endpoint ops `list`, `find`, `custom_query`, а default manifest отдаёт collection endpoints как `strategy`; функция выходит без смены placeholder.[11]

Проверочный вопрос в виджет вернул пользователю «модель завершила работу без ответа». Поскольку в этом аудите используется ScriptedLLM, это не доказывает слабость production model и не означает отсутствия runtime-инструментов: live manifest `default` содержит пять `db_*` tools. Но demo всё равно не демонстрирует заявленный «вопрос → live DB answer» path: в данном режиме нет подходящего scripted response, а основной production-like путь одновременно блокируется single-tenant MCP naming regression. В шапке показывается `API: Ollama unavailable`, что дополнительно делает local deterministic mode непригодным для продажной демонстрации.[14]

Admin Dashboard, напротив, загружается, принимает токен, показывает tenancies, config, tools, agents, RAG, Anti-Abuse, fallback, voice и audit navigation. Это реальная сильная сторона проекта: административная поверхность уже существует и функционально не выглядит как пустой mock. Но summary health отображает `Статус —` при 200 ответах backend endpoints, а Config page не объясняет различие между сохранённым config и runtime-generated manifest. До устранения P0 это остаётся хорошим интерфейсом, которому нужны более точные runtime-индикаторы, чтобы быть надёжным client config layer.

## DX и запуск

Documented local path `uv sync` → `./infra/scripts/dev.sh start` не проходит с первого раза в чистом checkout. RAG launcher вызывает `python -m rag.service`, однако workspace packaging добавляет `services/rag` в `sys.path` вместо родительского `services`, из-за чего `import rag` завершается `ModuleNotFoundError`. В аудите RAG удалось поднять только с временным `PYTHONPATH=<repo>/services`.

Второй блокер — `dev.sh` вызывает сборку embed widget, а `build.sh` запускает `npx tsc`/`npx esbuild`, но launcher не устанавливает embed `devDependencies`. В чистой среде npx предлагает пакет `tsc@2.0.4`, который не является TypeScript compiler, и сборка останавливается. После ручного `npm install` в `services/api-service/embed` сборка успешна.[12] [13]

Эти проблемы не делают Docker deployment автоматически сломанным, но противоречат обещанному «local development без Docker overhead» и превращают первый запуск в ручную диагностику. Для pre-final это существенный риск внедрения второго клиента.

## Приоритизированный план восстановления

| Приоритет | Действие | Владелец | Критерий закрытия |
|---|---|---|---|
| P0 | Разделить tenant identity и tool-name prefix policy в gateway | Backend | Single tenant: `db_map`; composite: `{tenant}__db_map`; Go + Streamable HTTP E2E зелёные. |
| P0 | Исправить default demo safety и runtime-индикаторы | Platform/Product | `read_only=true`; Config page различает saved config и runtime manifest; Tools page = MCP `tools/list`; live widget отвечает на 3 deterministic DB prompts. |
| P0 | Восстановить release gate | Engineering | Нельзя merge в main при failed `test-e2e`; latest main CI полностью зелёный. |
| P1 | Исправить demo `strategy` → tabs contract | Web | Manifest с `strategy` отображает entities/data table, browser acceptance добавлен в CI. |
| P1 | Исправить RAG package layout и embed dependency bootstrap | DX | Документированная команда quickstart успешно поднимает все шесть сервисов в clean checkout. |
| P1 | Ввести E2E test profile для MCP limiter | Backend/QA | E2E не flake-ит из-за production limiter, production defaults остаются защищёнными. |
| P1 | Перезапустить live benchmark на новом HEAD | AI/QA | Report provenance/telemetry заполнены; `ERROR` и infra metrics консистентны. |
| P2 | Синхронизировать docs и count | Docs | E2E README показывает 128/current count и реальный supported run mode. |
| P2 | Сделать aggregate admin health actionable | Admin | Карточка показывает `healthy/degraded/unavailable` и ссылку на конкретный upstream. |

## Рекомендуемые acceptance gates перед pilot

Сначала нужно устранить P0 и только затем снова оценивать AI-качество. Решение о pilot не должно опираться на старые 95.9 %: пока agent не видит expected single-tenant tools, score описывает другую версию продукта.

| Gate | Обязательная проверка |
|---|---|
| Runtime tool contract | Создать чистый read-only tenant → rewrite → manifest = admin tools = MCP `tools/list`; tool names unprefixed. |
| End-to-end agent | Widget question → SSE tool_call → MCP tool → data-service → grounded final; пройти минимум 3 fixture questions и absence case. |
| Isolation | Two tenant scopes не читают чужие marker records; composite scope показывает только prefixed tools. |
| Security | Write method отсутствует в manifest/tool list; viewer GET=200, mutation=403; equal tokens fail fast. |
| CI | Python, Go, admin/widget, docs и full E2E все green на merge commit. |
| Benchmark | Fresh live smoke/report c complete telemetry и explicit provider/model/commit/seed. |
| Browser acceptance | Playwright flow покрывает admin login/tenant review и widget injection/chat/error state. |

## Итог

Проект не является «пустым прототипом»: в нём есть многосервисное ядро, schema-driven конфигурация, audit/admin UI, embed widget, SSE и продуманная benchmark taxonomy. Это весомый актив. Но в текущем состоянии коммерческая формулировка «подключить живую БД клиента и безопасно отвечать пользователям» опережает доказанную runtime реальность.

**Правильное PM-решение: заморозить расширение функциональности, не тратить следующий спринт на RAG/новые каналы и закрыть три P0 — single-tenant MCP contract, safe/default demo configuration и blocking CI gate.** После этого провести fresh benchmark и добавить browser acceptance. Только тогда controlled pilot будет обоснован инженерно, а не презентационно.

## References

[1]: ../../README.md "Helperium README: goal, live SQL and read-only default"
[2]: ../FINAL_TASK.md "Pre-final definition and readiness criterion"
[3]: ../../services/agent-db/tests/e2e/README.md "E2E suite design and stated count"
[4]: ../../.github/workflows/ci.yml "Required CI jobs and E2E workflow"
[5]: https://github.com/trash2bin/helperium/actions/runs/32079886408 "GitHub Actions run for `ed421c9`: E2E failure"
[6]: ../../services/mcp-gateway/cmd/main.go "Single-tenant and composite MCP server contract"
[7]: ../../services/mcp-gateway/internal/tools/tools.go "Registry prefix implementation"
[8]: ../benchmark/core-benchmark.md "Core benchmark design and metrics"
[9]: ../benchmark/runs/README.md "Canonical benchmark run registry"
[10]: ../benchmark/runs/README.md "Benchmark run registry (canonical run metadata, no raw artifacts)"
[11]: ../../demo/web/static/app.js "Manifest-to-tabs frontend contract"
[12]: ../../services/rag/pyproject.toml "RAG package configuration"
[13]: ../../services/api-service/embed/build.sh "Embed widget build script"
[14]: ../../services/data-service/internal/runtime/handlers/mcp_manifest.go "Runtime manifest source of truth and `mcp_tools` generation"
[15]: ../../services/admin-dashboard/src/domains/config.ts "Saved config summary source"
[16]: ../../services/admin-dashboard/src/domains/tools.ts "Runtime manifest source for Tools page"

**Last verified:** 2026-08-18 (`ed421c9615381219671ede176c98135852a15ade`) — code, current CI result, deterministic benchmark tests, local E2E, full native stack and browser UI inspected by Manus AI. Поправка `mcp_tools` внесена и проверена в тот же день.
