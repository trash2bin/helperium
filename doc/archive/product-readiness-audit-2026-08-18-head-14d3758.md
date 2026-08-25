# PM и технический аудит готовности Helperium — HEAD `14d3758`

Читайте этот документ перед пилотом, демонстрацией либо изменением release gate. Это независимый снимок текущего `main` от 2026-08-18; прежний аудит product-readiness-audit-2026-08-18.md (архив, удалён) сохраняется как историческое evidence до последнего набора исправлений.

## Резюме для решения

**Продуктовая гипотеза правильная:** Helperium строит управляемый read-only путь от вопроса в виджете к живой клиентской SQL-базе через конфиг, MCP-инструменты и агент, а не подменяет этот путь RAG. Архитектура и значительная детерминированная тестова**Продуктовая гипотеза правильная:** Helperium строит �й `main` нельзя считать кандидатом на pilot или продажную демонстрацию**. Воспроизведены два независимых P0-сбоя product-flow: native runtime не регистрирует новый SQLite tenant, а запущенное demo выбирает tenant `autoparts`, отсутствующий в data-service. Поэтому цепочка «подключить БД → получить manifest → показать таблицу → спросить агента» сейчас не доказана. Красный обязательный E2E job в GitHub Actions подтверждает, что это не только локальная эстетическая проблема.[3]

| Контур | Оценка на `14d3758` | Решение PM |
|---|---|---|
| Продуктовая архитектура и безопасность read-only | **Жёлтый** | Направление верное; требуется доказать runtime-onboarding на чистом окружении. |
| Benchmark harness | **Зелёный** | Детерминированный evaluator регрессирует; результат реальной модели не актуален для HEAD. |
| E2E / release gate | **Красный** | CI E2E: 13 failed / 118 passed; native E2E: 20 failed / 12 passed / 99 errors. |
| Demo и embed-flow | **Красный** | Страница отвечает 200, но выбранный tenant не существует в data-service; manifest возвращает 404. |
| Admin Dashboard | **Жёлтый** | HTTP и контрактные тесты работают; dashboard показывает загрязнённые E2E tenant и не подтверждает демонстрационный tenant. |
| Документация и DX | **Жёлтый** | Ссылки валидны, но E2E guide отставал от набора (124 вместо 131); `make ci-test-py` зависит от локального `.env`. |

> **Release verdict:** **NO-GO для пилота и публичной demo.** Разрешён только внутренний engineering preview после устранения P0-1 и P0-2, зелёного E2E в CI и одного browser/API acceptance на чистом окружении.

## Объём и методика

Проверка проведена на `main`, `HEAD 14d3758`, без изменения продуктового кода и без платных live LLM-вызовов. Исследованы правила репозитория, актуальная история после live benchmark, benchmark artifacts, E2E/CI, исходные контракты demo и data-service. Запущенный на подключённом компьютере стек сообщил healthy для шести сервисов; затем были проверены HTTP product-paths и независимые тестовые цели. Визуальное браузерное подтверждение не выдано: браузер аудита изолирован от localhost подключённого компьютера, поэтому UI-выводы основаны на HTML, JS и реальных HTTP-ответах, а не на имитации кликов.

| Проверка | Результат | Что действительно доказывает |
|---|---:|---|
| `services/agent-db/tests/test_bench_core.py` | **107 passed**, 0.14 s | Evaluator, SSE parsing, verdict taxonomy, report aggregation и fixture rules регрессируют без сети. |
| `make ci-test-go` | **PASS** | Go unit/integration suites data-service и MCP gateway зелёные. |
| `make ci-admin` | **73 passed** | Admin frontend contracts и unit tests зелёные. |
| `make ci-docs` до обновления | **PASS**, 54 docs / 166 paths | Карта документов и локальные ссылки были целостны. |
| `make ci-test-py` | **FAIL: 1 / 72** в demo/web сегменте | Цель не герметична: локальный `WEB_ORIGIN=*` меняет ожидаемый default теста. |
| Current CI E2E job `32131005670` | **13 failed / 118 passed**, 98.58 s | Обязательный no-LLM gate на HEAD красный. |
| Native full E2E | **20 failed / 12 passed / 99 errors**, 4.97 s | Runtime tenant registration неработоспособна в текущем локальном состоянии. |
| Demo HTTP path | root 200; `autoparts` manifest 404 | Загрузка shell не равна работоспособной демонстрации данных. |

## P0 — блокирующие дефекты

### P0-1. Новый SQLite tenant не регистрируется в native runtime

Полный native E2E упал уже на fixture-регистрации: `POST /admin/tenants` возвращает `500 add_failed`, а минимальный временный read-only tenant на существующей SQLite-базе воспроизводит `sqlite: ping failed … unable to open database file: out of memory (14)`. Ошибка наблюдается и для базы `shop`, которая открыта у default tenant при startup; временная регистрация после 500 не сохраняется. Это блокирует ключевой onboarding сценарий, а массовые downstream errors делают native E2E непригодным как рабочий локальный gate.[4] [5]

Код `buildTenantInstance` всегда сначала открывает основной DSN как административный/read-write connection; затем adapter добавляет WAL/foreign-key pragmas, если DSN не является явным `mode=ro`. Значение config `read_only: true` само по себе не переводит этот основной connection в read-only URI. Это **подтверждённый риск реализации**, но не окончательно доказанная единственная причина macOS ошибки `SQLITE_CANTOPEN`: Linux CI имеет другой, но также красный E2E-профиль. Нельзя маскировать проблему очисткой `.data` или ослаблением тестов; нужен изолированный regression-test с реальным созданием tenant на macOS/Linux и явная политика connection modes.[4] [5]

| Impact | Acceptance criterion | Владелец |
|---|---|---|
| Клиент не может подключить новую SQLite БД; full native E2E каскадно ломается | Новый tenant с `read_only: true` регистрируется, manifest и `db_search` работают; тест не зависит от ранее созданных tenant | data-service |
| Реальный read-only доступ не отделён от admin connection | Явно определены DSN/connection-mode для introspection и query path; нет WAL write pragma на read-only client DB | data-service + security |

### P0-2. Runtime demo указывает на отсутствующий tenant `autoparts`

`DEMO_TENANTS=autoparts` и `DEFAULT_TENANT_ID=autoparts` заставляют demo выбрать `autoparts` при штатной инициализации. Но `/admin/tenants` data-service содержит только `default`, тестовые `e2e-*` и `test-grep-debug`; tenant `autoparts` отсутствует. Поэтому `/api/manifest` с header `X-Tenant-ID: autoparts` возвращает 404, а JavaScript повторяет запрос до десяти раз и завершает UI сообщением `Failed to load entities. Refresh the page.`.[6] [7]

Это может быть локальная drift-конфигурация, а не баг коммитного кода, однако для демонстрационного контура разницы нет: **показанный пользователю flow сейчас не работает**. Status 200 на корне и `/api/health` недостаточен, потому что он не проверяет выбранный tenant, manifest и данные.

| Impact | Acceptance criterion | Владелец |
|---|---|---|
| Demo не показывает live SQL данные и не может честно показать agent→DB path | Bootstrap либо materialize/register `autoparts`, либо конфиг demo выбирает существующий read-only tenant; `/api/manifest` и первая collection возвращают 200 | demo + operations |
| Зелёный health создаёт ложное впечатление готовности | Health/readiness проверяет configured demo tenant, его manifest и хотя бы один read endpoint | demo + CI |

## Benchmark: сильный evaluator, но нет текущего release KPI

Harness — зрелая часть проекта: 107 текущих тестов покрывают verdict `CORRECT/PARTIAL/WRONG/ERROR`, ошибки retrieval/answer, hallucination, budget/loop/dedupe, SSE и агрегацию infrastructure errors.[8] Полный canonical NIM-run содержит 49 autoparts cases с 45 `CORRECT`, 2 `PARTIAL`, 1 `WRONG`, 1 `ERROR`, то есть исторический `verdict_pass_rate` 95.9 %.[9]

Эта цифра **не является KPI текущего HEAD**. Raw report относится к `c0f3d62`, а после него в основной pipeline вошли изменения data-service filter contract, benchmark failure classification и полный переход MCP на Streamable HTTP v2, включая tenant authority и E2E.[10] Кроме того, raw artifact сохраняет нулевые duration/token/cost для всех cases и `infra_error_rate=0.0` при явно записанном request timeout. Код и unit-тесты уже исправляют агрегирующую инварианту, но сам historical report не превращается от этого в новый measurement.[8] [9]

| Что бенч доказывает сейчас | Что он не доказывает | Обязательный следующий evidence |
|---|---|---|
| Корректность evaluator и стабильность 49-case fixture | Качество текущего MCP v2 + agent pipeline с реальной моделью | 5–10 case paid live smoke после P0, с `HEAD`, dataset checksum, policy/model/provider/seed, tokens/cost/duration и `infra_error_rate` |
| Фактическое историческое поведение NIM на `c0f3d62` | Release-quality на `14d3758` | Полный 49-case run только после smoke green |

## E2E: покрытие сильное, но gate и пользовательские доказательства неполные

В наборе сейчас **131** тест, а не 124. Он содержательно покрывает tenant lifecycle, persistence, data isolation, Streamable HTTP security, composite MCP, generated tool surface, search strategies и ScriptedLLM chain. Отдельно ценны тесты canonical single-tenant names, hostile tenant header, authentication, Origin policy, empty calls и no-legacy tools.[3] [11]

Проблема не в объёме HTTP/integration coverage, а в том, что release evidence противоречиво. Mandatory CI E2E на HEAD красный; native path ещё хуже. В CI каскад затрагивает MCP Streamable HTTP, named-agent composite flow, product readiness и ScriptedLLM. В native режиме первые ошибки происходят при регистрации tenant. Пока не выделены и не устранены первопричины обоих профилей, зелёные unit suites нельзя интерпретировать как проход production path.[3] [4]

| Реальный сценарий | Текущее покрытие | Verdict |
|---|---|---|
| Создать/зарегистрировать tenant → config → manifest → read-only data | Есть API/E2E tests; native runtime сейчас ломается | **Red** |
| Agent → MCP tool → data-service → SSE final без реального LLM | ScriptedLLM покрывает, но CI на HEAD красный | **Red** |
| Composite scope и изоляция двух tenant | Хорошее HTTP/MCP coverage | **Yellow**: не считать release proof, пока suite красный |
| Админ изменяет config и видит runtime state | Admin contract tests зелёные; runtime UI не browser-tested | **Yellow** |
| Посетитель видит demo table и grounded chat | Нет browser acceptance; текущий configured demo tenant отсутствует | **Red** |
| Поведение реальной модели и выбор tools | Отдельный opt-in каталог, не CI | **Yellow/Red** для пилота |

## Demo и Admin Dashboard

Admin Dashboard отдаёт HTML и `/api/dashboard` с HTTP 200; его 73 contract/unit tests проходят. Но dashboard показывает девять tenant, среди которых исторические `e2e-*`, а отсутствует `autoparts`, нужный для demo и двух сохранённых agent configs. Значит административная страница доступна, но текущая runtime картина не пригодна как доказательство подготовленной демонстрации.

Demo shell также отдаёт 200, а JavaScript корректно умеет строить tabs из endpoints с `op: strategy`, что устраняет прошлый manifest-to-tabs дефект. Но configured `autoparts` отсутствует в data-service, поэтому обновлённый tabs-code не достигает входных данных. Browser acceptance должен проверять не только DOM, но всю цепочку `selected tenant → manifest → table → widget → SSE tool_call → final` на чистом окружении.[6] [7]

## Соответствие общей цели

Helperium соответствует цели **на уровне дизайна и большей части компонентов**: модель доступа к живой SQL БД, read-only policy, generated MCP surface, tenant scope и встраиваемый виджет реально присутствуют. Наиболее сильные заделы — data-service/MCP contracts, deterministic evaluator и широкая no-LLM тестовая матрица.[1] [2]

Но соответствие пока не доказано **как продукт**. Центральное обещание — подключить БД клиента без правки кода и показать полезный ответ в готовом сайте — требует чистого onboarding и действующего демонстрационного tenant. Оба условия сейчас нарушены. RAG сознательно не включён в verdict: он не является критическим путём этой цели.

## Приоритетный план восстановления

| Волна | Действие и измеримый результат | Зависимость |
|---:|---|---|
| 0 | Зафиксировать `14d3758` как **NO-GO**; не использовать 95.9 % как current KPI; сохранить CI/native логи | Нет |
| 1 | Воспроизвести P0-1 на чистом temp data-dir в Linux и macOS; добавить regression на `POST /admin/tenants` с read-only SQLite | Нет |
| 1 | Восстановить единую demo truth: зарегистрировать `autoparts` или изменить explicit demo config на существующий tenant; добавить readiness check | Нет |
| 2 | Довести mandatory E2E до green без skips/rate-limit noise; расследовать 13 CI failures отдельно от native storage error | P0-1 |
| 2 | Добавить browser/API acceptance: demo table, widget SSE, data tool call и final; проверить также saved/runtime Admin indicators | P0-2 |
| 3 | Выполнить budgeted live smoke и публиковать KPI только с complete provenance/telemetry | Waves 1–2 |
| 4 | Сделать `make ci-test-py` hermetic относительно `.env` либо явно изолировать тест default settings | Нет |

## Ограничения аудита

Аудит не выполнял платный live benchmark и не отправлял вопросы агенту `autoparts-assistant`, чтобы не расходовать внешние LLM credentials без отдельного согласования. Визуальный browser walkthrough не выполнен из-за сетевой изоляции между browser-runner и localhost подключённого компьютера; это не заменяется HTTP-проверкой и включено в P0/P1 acceptance. Graph-анализ репозитория был запрошен, но подключённый сервис structural memory был недоступен в рабочем окружении; выводы подтверждены чтением кода, git history, CI artifacts и живыми endpoints.

## References

[1]: [Технический паспорт и карта проекта](../../AGENTS.md)
[2]: [Основной README и продуктовая архитектура](../../README.md)
[3]: [CI workflow: обязательный no-LLM E2E](../../.github/workflows/ci.yml)
[4]: [Создание runtime tenant instance](../../services/data-service/internal/server/tenant_lifecycle.go)
[5]: [SQLite adapter и обработка DSN pragmas](../../services/data-service/internal/datasource/sqlite_adapter.go)
[6]: [Demo web server: tenant discovery и proxy contracts](../../demo/web/server.py)
[7]: [Demo frontend: tenant selection и manifest loading](../../demo/web/static/app.js)
[8]: [Core benchmark tests и implementation](../../services/agent-db/tests/test_bench_core.py)
[9]: [Canonical benchmark run registry (83.7% plateau)](../benchmark/runs/README.md)
[10]: [История изменений после live benchmark](../../CHANGELOG.md)
[11]: [Руководство и структура E2E](testing-guide.md)

---

**Last verified:** 2026-08-18 (HEAD `14d3758`) — benchmark harness, historical raw report, CI run `32131005670`, native E2E, Go/Admin/Python/docs targets, live service health и demo/admin HTTP contracts проверены; paid live LLM и browser walkthrough не выполнялись.
