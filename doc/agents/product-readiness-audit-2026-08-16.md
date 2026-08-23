# Product Readiness Audit — 2026-08-16

> **Статус:** 🗃️ архив: независимый PM- и технический аудит.
>
> **Контекст:** ревизия `0dbc8af`; аудит проведён 2026-08-16. Это снимок состояния и приоритизированный план, а не новая продуктовая спецификация.

## Резюме для решения

Helperium уже является **сильным техническим прототипом core-продукта**. Core-цепочка SQL → config → MCP → data-service → agent → embed реализована. На core-стеке, пересобранном из `HEAD`, детерминированный E2E-набор завершился: **124 из 124**. Это подтверждает data-path, но не production readiness.

> **Главный вывод:** core доступа к данным готов к controlled pilot. До заявления о production-ready AI-поддержке необходимо закрыть операционный контроль ролей, актуализировать live-оценку агента и ввести браузерные acceptance-сценарии.

| Контур | Оценка | Основание | PM-решение |
|---|---|---|---|
| SQL → config → MCP → data-service | **Зелёный** | Unit/E2E покрывают изоляцию, composite mode, dynamic tools и search strategies | Допускать в controlled pilot |
| Агент с реальной моделью | **Жёлтый** | Live benchmark не соответствует `HEAD`; реальная LLM E2E вынесена из CI | Не использовать текущий score как release KPI |
| Admin Dashboard и embed | **Жёлтый** | API, component tests и сборки проходят; browser E2E нет | Добавить пользовательские acceptance tests |
| Viewer-доступ в текущем окружении | **Красный** | `ADMIN_TOKEN == VIEWER_TOKEN`; viewer смог создать агента | Срочно разделить токены и добавить fail-fast |
| Документация и DX запуска тестов | **Жёлтый** | Compose E2E воспроизводим; host и Docker режимы легко смешать | Явно разделить режимы и добавить preflight |

## Выполненные проверки

Аудит охватил архитектуру и документацию `api-service`, `data-service`, `mcp-gateway`, `admin-dashboard`, `agent-db`, `demo-web`, benchmark и CI. Выполнялись только детерминированные проверки без платного внешнего LLM-вызова. Визуальный браузерный просмотр из изолированной среды невозможен: он не видит `localhost` подключённого Mac. Вместо этого подтверждены HTTP entry points, HTML entry pages, авторизованный admin API и embed assets.

| Проверка | Результат | Что подтверждает |
|---|---:|---|
| `uv run --package agent-db pytest services/agent-db/tests/test_bench_core.py -q` | **106 passed** | Evaluator, parser, SSE и отчёт benchmark |
| `make ci-test-py` | **91 passed**, 1 warning зависимости | Python unit/integration: API, demo, RAG, SDK |
| `make ci-test-go` | **passed** | Go unit: data-service и mcp-gateway |
| `go test ./services/admin-dashboard/... -count=1` | **passed** | Серверные маршруты и auth middleware Dashboard |
| `make ci-admin` | **72 passed** | TypeScript/Vitest и frontend contract checks |
| `make ci-test-embed` | **70 passed** | Widget config, DOM, SSE, storage и production build |
| Compose E2E после пересборки core из `HEAD` | **124 passed, 69.68 s** | Сквозной data-path без живой LLM |

## Benchmark: что он доказывает, а что нет

Текущий benchmark — зрелый **детерминированный регрессионный инструмент**, но не универсальная оценка качества AI-ассистента. Набор состоит из 49 active кейсов (всего 51 с двумя deprecated) по lookup, filter, aggregation/count, absence и search в одном домене автозапчастей. Он проверяет retrieval, delivery ответа, hallucination, refusal, допустимые tool paths и часть неэффективного поведения через verdict `CORRECT / PARTIAL / WRONG / ERROR`.[1] [2]

Canonical rebuilt NIM run от 2026-08-16 зафиксировал 45 `CORRECT`, 2 `PARTIAL`, 1 `WRONG`, 1 `ERROR`, то есть verdict pass rate **47/49 (95.9%)**. Но raw report сделан на `c0f3d62`, а после него в `HEAD` менялись runner/evaluator, agent policy и field-to-field filter contract. В этом же report tokens/duration/cost равны нулю; timeout отражён как case-level infra error, когда top-level `infra_error_rate = 0.0`. Последующие детерминированные фиксы адресуют именно эту классификацию.[3] [4]

| Ограничение | Риск ошибочного решения | Следующее действие |
|---|---|---|
| Live run не соответствует `HEAD` | 95.9% ошибочно воспринимаются как текущий KPI | Выполнить fresh 49-case run на фиксированных commit/model/seed |
| Один домен и один turn | Score не доказывает переносимость на клиента | Добавить минимум два контрастных tenant fixture и multi-turn suite |
| Реальная LLM проверяется opt-in | Основной CI подтверждает pipeline с scripted LLM, а не tool selection модели | Ночной budgeted live smoke на 5–10 вопросов |
| Telemetry неполна | Нельзя делать выводы о latency/cost | Метка `incomplete_telemetry=true` должна блокировать promotion report в canonical KPI |

## E2E: сильные стороны и пробелы

Набор `services/agent-db/tests/e2e/` хорошо покрывает технические контракты: tenant CRUD/config persistence, isolation, composite MCP, generated v5 tool surface, required-argument validation, ScriptedLLM pipeline, search strategies в auto-shop и clinic, SSE/JSON-RPC и fixture tenants.[5] Повторный прогон против core images, собранных из `HEAD`, прошёл полностью.

Однако набор почти не тестирует путь глазами пользователя. В проекте нет Playwright/Cypress/Selenium suite для Dashboard, demo-web или Shadow DOM embed-widget. `test_agent_chat_http_handshake` проверяет SSE acceptance, а не содержательный ответ реальной модели; LLM E2E вынесены из CI. В CI запускаются Go tests только data-service и mcp-gateway, хотя server/OpenAPI tests admin-dashboard существуют.[5] [6]

| Цепочка | Сейчас | Нужное дополнение |
|---|---|---|
| Подключить DB → rewrite config → tools | E2E есть | PostgreSQL acceptance с read-only и cleanup |
| Вопрос → agent → MCP → DB → SSE final | ScriptedLLM есть; live LLM отдельно | Nightly live smoke с assertion на user-facing final |
| Администратор создаёт tenant/agent через UI | API и component tests | Browser E2E: login, tenant, rewrite, agent, tool preview |
| Клиентский сайт получает widget | Asset/DOM/SSE unit tests | Browser E2E: injection, mobile, reconnect, error state |
| Viewer не меняет настройки | Unit test с разными токенами | Runtime smoke и guard от равных токенов |

### DX-ограничение локального E2E

Host-команда `uv run pytest services/agent-db/tests/e2e/ -q`, направленная на Docker services, дала `12 failed, 14 passed, 98 errors`: Python materializes SQLite по абсолютному пути хоста `/Users/...`, а data-service container видит workspace как `/workspace`. Это **не дефект core-data-path**: тот же набор в поддерживаемом compose-контейнере прошёл целиком. Но это реальный DX-риск: документация должна явно разделять native и compose режимы.[5]

## Приоритизированный backlog

| Приоритет | Риск | Доказательство | Рекомендация | Критерий закрытия |
|---|---|---|---|---|
| **P0** | Viewer-role неэффективна в текущем окружении | Равные токены; viewer POST `/api/agents` вернул 201. Код при разных токенах корректно запрещает mutating `/api/*` | Немедленно задать разные секреты; добавить fail-fast или health degradation при их равенстве | Viewer GET=200, POST/PUT/DELETE=403; одинаковые токены отклоняются |
| **P0** | Live benchmark не подтверждает `HEAD` | Canonical run на `c0f3d62`; затем менялся core benchmark/data contract | Re-run на `HEAD`, сохранять report, trace и metadata | Report содержит заполненную или явно unavailable telemetry и разобранные ERROR |
| **P1** | Нет browser acceptance | Нет project-level browser test config | Три Playwright smoke flows: admin lifecycle, widget chat/error/reconnect, tenant isolation | Flows запускаются на compose profile test в CI |
| **P1** | CI пропускает Go tests admin-dashboard | Локальные tests существуют, workflow их не запускает | Добавить отдельный Go job или включить сервис в matrix | Go tests Dashboard запускаются на каждом PR |
| **P1** | Native и compose E2E пути смешиваются | Host pytest против Docker даёт 500 при tenant registration | Обновить docs и добавить preflight namespace/path check | Один воспроизводимый путь без ручной диагностики |
| **P2** | Runtime audit log меняет tracked source file | API-действия изменили `services/admin-dashboard/internal/audit/*.jsonl` | Перенести default audit storage в `.data/` | Runtime не меняет tracked files |
| **P2** | «Любая SQL-БД» шире support scope | Реализованы адаптеры SQLite/PostgreSQL | В product copy назвать поддерживаемые драйверы; MySQL вести как roadmap | Публичные docs не обещают несуществующий adapter |

## Рекомендуемый порядок следующего инкремента

Сначала закрепить безопасную эксплуатацию: разные токены, guard, runtime auth smoke и перенос audit storage. Затем восстановить доверие к метрикам: fresh benchmark на `HEAD`, policy полноты telemetry и малый nightly live-model suite с бюджетом. После этого добавить три браузерных сценария, подтверждающих реальный admin/widget путь.

RAG-качество и semantic search намеренно не входят в этот аудит: по текущей цели они дополнительны, а не core-контур.

## References

[1]: ../benchmark/README.md "Benchmark design"
[2]: ../benchmark/core-benchmark.md "Core benchmark"
[3]: ../benchmark/runs/README.md "Canonical run registry"
[4]: ../benchmark/runs/README.md "Benchmark run registry (canonical run metadata, no raw artifacts)"
[5]: ../../services/agent-db/tests/e2e/README.md "E2E suite"
[6]: ../../.github/workflows/ci.yml "CI workflow"

**Last verified:** 2026-08-16 (HEAD `0dbc8af`) — код, CI workflow, benchmark artifacts и результаты локальных deterministic/compose E2E проверок сверены в рамках аудита.

**Автор:** Manus AI.

---

**Архивная запись.** Следующий аудит создаётся отдельным файлом; этот снимок состояния не переписывается задним числом.
