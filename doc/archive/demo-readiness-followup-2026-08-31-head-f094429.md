# Follow-up аудита готовности к демке: верификация на HEAD `f094429` (2026-08-31)

> **Тип документа:** архивный evidence-snapshot (разовая проверка состояния). Обновлениям не подлежит; выводы действительны только для указанного HEAD.
> **Verification marker:** HEAD `f094429` (`f094429695f37e5e24fe8ec3955813905aeeef2a`, "style(api): ruff-format four unit test files left unformatted on HEAD"), рабочее дерево чистое.
> **База:** `doc/archive/product-demo-readiness-audit-2026-08-28-head-53a3172.md` (HEAD `53a3172`), 26 коммитов спустя. Все ссылки на код сверены с HEAD `f094429`.
> **Метод:** read-only. Экспериментов с кодом не было; прогнаны `make ci` и изолированный Docker E2E по контракту `AGENTS.md` (ресурсы удалены `--profile test down -v`, autoparts-store не тронут).

## 1. Верификация инфраструктуры

| Прогон | Результат |
|---|---|
| `make ci` локально | exit 0: 543 Python-теста (38 pre-existing warnings), все Go-пакеты, vitest 5 файлов/73 теста + 7 файлов/88, admin 90+91, docs OK |
| GitHub Actions `CI` на `f094429` | success (8m12s, run 33372813706). Предыдущие red-прогоны 29–30.08 закрыты последующими фиксами |
| Docker E2E `--profile test` | Первый прогон: 4 failed / 134 passed. После пересборки образов: **138/138 за 95 с**. Контрактный cleanup выполнен |

### N1 — новая находка: ловушка устаревших Docker-образов (MEDIUM, операционная)

Первый E2E-прогон упал с 4 failures (`test_scripted_llm.py::test_basic_pipeline`, `test_error_recovery`, `test_empty_llm_round_guard`, `test_named_agent_composite_pipeline.py::test_named_agent_composite_scope_reaches_prefixed_mcp_tool`), все — generic `Sorry, an internal error occurred`.

Лог api-инстанса scripted-теста: `AttributeError: 'DemoSettings' object has no attribute 'spending_reservations_enabled'`.

Первопричина: образы `helperium-{api,data,mcp-gateway,admin}:latest` были собраны 2026-08-27 — до reserve/commit коммитов (`147fc8e`, `9cf5691`). E2E-контейнер (`infra/docker-compose.yml:269-275`) монтирует свежий workspace (новый код агента), но SDK импортируется запечённый в образ (`services/api-service/Dockerfile:36` копирует SDK на этапе build). `services/api-service/src/api_service/agent/adapters.py:84` читает новое поле `settings.spending_reservations_enabled`, которого нет в старом SDK → AttributeError в горячем пути orchestrator.

В GH Actions не воспроизводится: там образы строятся при каждом запуске. Локально `infra/scripts/compose.sh` не передаёт `--build`.

Последствия после пересборки: `docker compose --profile test build api data-service mcp-gateway admin-dashboard web` → полный E2E 138/138. Код не виноват; регрессии нет.

Рекомендация: передавать `--build` в `compose.sh` для test-профиля, либо добавить в e2e контрактный тест версии SDK с внятным сообщением «rebuild images». Зарегистрировано как задача T-4 в локальном untracked todo-файле репозитория.

## 2. Статус находок аудита 2026-08-28

Закрыто (проверено кодом и/или тестами на HEAD `f094429`):

| Пункт аудита | Фикс | Проверка |
|---|---|---|
| P0-1 direct chat галлюцинирует — корень «продолжение без тулов» | `bfa16d3` — tool schemas сохраняются на continuation для relayed deepseek | код + unit |
| P0-1 качество direct chat | `54b1c2f` — `DIRECT_CHAT_AGENT` quality-профиль из Agent Store; tenant scope остаётся server-configured (`services/api-service/src/api_service/server/tenant_authority.py:43-100`) | код + unit |
| P1-1 голос обходил anti-abuse / buffered SSE / тихий фолбэк | `cfce4e7`: `check_abuse` в `chat_voice_endpoint` (`server/routes/chat.py:373`), `_buffered_agent_sse_events` (:416), явный 404 «Agent not found» (:300) | код + E2E green |
| P1-2 дрейф dev/Docker E2E-профилей | `1031402`: launcher шлёт `MCP_DEV=false`, secure-контракт восставлен (`infra/scripts/dev.sh:1074-1081`) | код + контракт-тест |
| P1-3 ReadonlyDSN в admin DTO / логах / ответах | `b926289`: `responseFromDataSource` отдаёт `HasReadonlyDSN` (`data-service/internal/server/admin.go:115-124`), masking viewer (`security_hardening_test.go:161-214`), round-trip без эха (`tenant_admin_dsn_redaction_test.go`) | unit-тесты в main suite |
| P1-4 plaintext llm_config | `a340c38`: `ENCRYPTION_KEY` fail-fast при зашифрованной БД (`agent_repository.py:75`) | код + unit |
| P1-5 утечка внутренних URL в ошибках gateway | `fc7574a`: клиенту generic `upstream_unavailable`, полный текст в slog (`mcp-gateway/cmd/main.go:595-606`) | код + unit |
| P1-6 `javascript:`-ссылки в embed markdown | `cfce4e7`: whitelist `SAFE_LINK_SCHEMES` + зачистка whitespace в схеме (`embed/src/markdown.ts:138-161`) | код + embed unit |
| P1-7 constant-time compare / Set-Cookie | `crypto/subtle` в admin (`server.go:253-256`) и gateway (`cmd/main.go:463`); `Set-Cookie` в стоп-листе прокси (`server.go:260-276`) | unit |
| P2: `?tenant=` как tenant authority | `01d8458` + `ceb6df7`: query fallback удалён полностью, fail-closed (`data-service/internal/server/tenant.go:385-403`) | unit + E2E |
| P2: stale tenant из localStorage ломал UI | `74419c6`: generation guard, 404-детекция без ретраев, Retry-состояния, `/api/tenants`-failure без фейкового `default` (`demo/web/static/app.js:53-182`) | код + demo web тесты |
| P2: тихий voice-фолбэк | покрывается P1-1 (явный 404) | код |
| P2: port-ownership в dev.sh | `55a2d6c`: `check_port_ownership` через lsof, fail-safe (`infra/scripts/dev.sh:153-194`) | код |
| P2: wipe+reseed 1.7M строк на старте | `4e51974`: seed пропускается при наличии данных, пересоздание только с `--force` (`catalog/management/commands/seed_data.py:27-28`) | код |
| P2: захардкоженный список грантов | `ceb6df7`: `demo/tests/test_grants_parity.py` (130 строк) — тест полноты грантов добавлен | тест в demo suite |
| P2: Caddy без HSTS/CSP | `ceb6df7`: HSTS + CSP + `demo/tests/unit/test_caddyfile_public_headers.py` (`Caddyfile.public:11,22`) | тест |
| P2: triple-документация | `e5c93be` + последующие: README + README.foreign, противоречия сняты | файлы |
| P2: e2e README test count drift (131 → 138) | `e5c93be` (README + compose-документация) | файлы. Смежный пункт «`test_v5_tool_chain` не тестирует заявленную цепочку» **остался**: docstring :383-395 обещает цепочку, тело — прямые `mcp_call` (перенесено в todo, T-5g) |
| P2: `_recent_messages` бесконечно рос, `display_name` мёртвый код | удалены в chat/SSE-фиксах (`7e6a999`, `bb8a0a7`) | grep по agent/ — пусто |
| P2: composite spending без решения | `147fc8e`+`9cf5691`+`7ddf4e0`: reserve/commit ledger (default OFF), principal = account/named agent (`doc/agents/spending-reserve-commit-decision.md`), `docker-compose.yml` — `SPENDING_RESERVATIONS_ENABLED` | код + unit + doc |
| P2: `SpendingChecker.record_spending` — sync fs в async-цикле | закрыт в reserve/commit работе: `_AsyncSpendingTracker.record` через `asyncio.to_thread` (`agent/adapters.py:26-36`) | код |

Осталось открытым (детализация и порядок — в локальном untracked todo-файле репозитория):

1. E2E write-зонд read-only (рекомендация №6 аудита) — по-прежнему отсутствует.
2. Upload SQLite path traversal — заявленный фикс не покрывает admin-upload-хендлер.
3. Rate-limit eviction + карточность метрик gateway.
4. Гигиена: `.data/tenants` (рост до 433 файлов / 5.6 МБ), admin-CORS-комментарий, widget default drift, CI dump-on-failure, `_check_services`, порядко-зависимая фикстура lifecycle.

Отложено решением владельца (не задачи): `.data/tenants` TTL на сервере (перенесено в этот артефакт как осознанное решение), TLS/sslmode DSN, токен в localStorage, LLM-уровневый circuit breaker (queue-plan №7), browser-приёмка виджета, session-quota E2E.

## 3. Что эта проверка доказывает и чего не доказывает

Доказывает: локальное CI-ядро, изолированный Docker E2E (138/138 после пересборки), миграцию reserve/commit без регрессии scripted-пайплайнов, исполнение security-контрактов из аудита на уровне кода и unit-тестов.

Не доказывает: живое качество LLM-ответов direct chat (acceptance 20–30 вопросов — за владельцем, LLM-конфигурация в данный момент не настроена), поведение на реальном домене (edge/WAF), multi-instance abuse state, RAG/prompt-injection assessment, поведение на сервере с длительным lifetime (включая рост `.data/tenants`).
