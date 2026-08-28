# Аудит готовности к публичной тех-демке (2026-08-28)

> **Verification marker:** HEAD `53a3172` (`53a3172d4f1c5e0440ebbfb19cefd755b7d08810`, "fix(api/logging): emit JSON for stdlib log records and thread correlation_id into MCP work", 2026-08-28T14:00:30+03:00), рабочее дерево чистое.
> **Дата аудита:** 2026-08-28. Все ссылки на код и тесты сверены с HEAD `53a3172`; после последующих коммитов выводы не действительны без перепроверки.
> **Метод:** read-only аудит. Все эксперименты откатывались, `git status` чист; Docker test-ресурсы удалены по контракту, autoparts-store не тронут.

## Сводка

Вердикт по продукту как по публичной тех-демке: **READY WITH NOTES**. Архитектурное ядро
(MCP Streamable HTTP, tenant-изоляция, read-only инвариант, SSE-пайплайн) спроектировано
корректно и реально покрыто тестами — устойчивость E2E-слоя к поломкам проверена
экспериментально. Главная зона риска — не код, а LLM-слой: прямой чат без named-агента
галлюцинирует данные. Именно это клиент увидит первым.

| Ось | Оценка | Комментарий |
|---|:---:|---|
| Архитектура и связность кода | 8/10 | Слои графа совпадают с документированным data flow |
| E2E-защита от регрессий | 7.5/10 | Ловит логику (проверено экспериментом), 5 продающих швов без покрытия |
| Security-контракты | 7/10 | Fail-closed везде, но есть точечные утечки (DSN, внутренние URL) |
| Демо-качество LLM-витрины | 5/10 | Прямой чат галлюцинирует; нужен named-agent путь |
| Операционная гигиена | 6/10 | 358 e2e-tenant конфигов в `.data/tenants`, дрейф dev/Docker профилей |

## Как проверялось

1. **CI-ядро:** ruff ✅, **444 Python-теста passed** (38 pre-existing warnings), все Go-пакеты `ok`.
2. **Изолированный Docker E2E** (`./infra/scripts/compose.sh --profile test`): **138/138 passed за 95 с**;
   после — `compose.sh --profile test down -v` (удалены только CI-контейнеры/volumes).
3. **Нативный E2E** (`./infra/scripts/dev.sh e2e-up` + `dev.sh e2e`): 137 passed + 1 fail →
   найден дрейф профилей (находка P1-2).
4. **Три эксперимента-саботажа** в критичной логике, каждый с полным прогоном E2E (раздел 1).
5. **Живой LLM-чат** через реальных провайдеров (Polza/DeepSeek, nvidia-nim) с побайтовой
   проверкой SSE, backlog-файлов, логов api-service и grep-проверкой выданных моделью
   данных по эндпоинтам data-service (раздел 2).
6. **Браузерный прогон** demo/web через Playwright (ARIA, console, network).
7. **Граф знаний** codebase-memory (~9.4k узлов / 46k рёбер на HEAD): слои, кластеры,
   complexity-хотспоты (раздел 4).
8. **Пять параллельных fresh-context ревью-субагентов**: go-review, e2e-map, demo-recon —
   завершены с отчётами; api-review и admin-embed упёрлись в таймаут 30 мин — их ключевые
   гипотезы верифицированы вручную по коду в этом аудите (пометки «проверено вручную»);
   неподтверждённые гипотезы в выводы не включены.
9. **Позитивные проверки** — что работает, подтверждено живьём (раздел 3).

## 1. Главный эксперимент: ловит ли E2E поломку важной логики

В код вносились три реальных повреждения; после каждого — пересборка изолированного
стека (`dev.sh e2e-up`) и полный нативный E2E-прогон. Каждое повреждение откатывалось,
чистота подтверждалась `git diff`.

| Саботаж | Что сломано | Результат прогона | Вывод |
|---|---|---|---|
| №1: `isValidFilterExpression` → безусловный `return true` (`services/helperium-go/config/types.go:941`) | SQL-гвард конфига (слой read-only защиты) | Go unit `TestIsValidFilterExpression` — **11 FAILED** (semicolon, DROP, INSERT, UPDATE, DELETE, ALTER, CREATE, TRUNCATE, EXEC, EXECUTE, UNION_SELECT). Полный E2E — **137 passed = без изменений от baseline** | Гвард надёжно закрыт юнит-тестами, но **E2E никогда не зондирует попытку записи через HTTP/MCP** — сценарий «сломали гвард, CI зелёный» реален |
| №2: `resolveTenant` → константа `"default"` (`services/data-service/internal/server/tenant.go:350`) | Tenant-изоляция (критичнейший шов) | **E2E: 51 failed из 138** — каскад через test_data_isolation, test_mcp_validation, admin-lifecycle и др.; все tenant-запросы ушли в `default` | Детекция отличная. Минус: сигнатура падения шумная (`TaskGroup unhandled errors` без указания на изоляцию) — первопричину по отчёту не видно |
| №3: подавлено SSE-событие `tool_call` (`services/api-service/src/api_service/agent/loop.py:286`) | Контракт виджет↔бэкенд | **E2E: 4 failed**, включая точно целевой `test_scripted_llm.py::TestScriptedPipeline::test_basic_pipeline` («Tool name is empty») | Эталонная работа: тест ловит ровно то, что сломано |

Побочный результат: контракт-тест `test_test_profile_exposes_matching_secure_service_credentials`
**сам поймал** дрейф нативного профиля (см. P1-2) — то есть контрактный контроль работает,
даже когда ломается конфигурация запуска, а не код.

## 2. Живой прогон LLM-витрины: пошаговая верификация P0

Вопрос: что реально увидит клиент-посетитель демки? Проверялось через реальный SSE-чат
с живыми провайдерами (в `.env` — Polza/DeepSeek, Ollama; pool из 6 воркеров).

Шаги верификации:

1. `POST /api/chat` (direct chat, без named-агента) через web-прокси → SSE 200,
   ответ «проверил базу через MCP-инструмент, данных о BMW X5 нет».
2. **SSE-поток содержал только `final` + `done`** — ни одного `tool_call`/`tool_result`
   события клиенту (473 байта всего потока).
3. Grep по data-service: `Lenovo`, `Logitech`, `Samsung` (товары из «финального» ответа
   второго прогона) → **NOT IN DB** — данные выдуманы.
4. Лог api-service: pool выбрал `nvidia_nim/nemotron-3.5-lightning`; после первого тула
   (`db_describe`, 8509 chars) продолжение пошло с `tools_sent=False, supports_function_calling=False`,
   `untrusted_tool_results_in_context=1` — модель получила схему, но не данные, и
   импровизировала.
5. Контрольный прогон на локальном api напрямую: модель выдала псевдо-XML
   `<use_mcp_tool><server_name>university-server</server_name><tool_name>count_products</tool_name>`
   вместо нативного tool call — ссылается на несуществующий сервер и не умеет в
   function calling без него.
6. Корень в коде: дефолтный `SYSTEM_PROMPT` — **«университетский ассистент»**
   (`services/api-service/src/api_service/agent/prompts.py:21-59`), не соответствующий
   autoparts-демо; агент `default` в `agents.sqlite` без `provider_priority`; pool
   выбирает провайдеров без подтверждённого function calling.
7. Позитивный контраст: именованный агент `autoparts-assistant` (prio `["polza","ollama"]`,
   собственный llm_config) — рабочий путь; scripted E2E-пайплайн через него проходит.

Попутно вживую подтверждено: **anti-abuse блокирует curl по User-Agent**
(`data: {"type":"error","text":"Request blocked: Blocked User-Agent: curl/8.7.1"}`) —
защита не бумажная.

Оговорка честности: в одном из прогонов web-прокси указывал на занятый посторонним
ssh-туннелем порт (см. P2-1), из-за чего часть запросов уходила в другой инстанс;
вывод P0 опирается на прогоны, где целевой инстанс подтверждён backlog-файлом
(`backlog/direct_pm-direct-local-1.jsonl`) и логом. Отдельное наблюдение — отсутствие
`tool_call` событий в SSE direct-чата при том, что бэкенд-лог фиксирует исполнение тула:
механизм на scripted/named-агент пути покрыт E2E (саботаж №3), на живом direct-пути
события до клиента не дошли; причинность (буферизация vs режим без function calling)
не установлена — зафиксировано как наблюдение, не как диагноз.

## 3. Проверенные сильные стороны (подтверждено, а не заявлено)

- **Tenant-изоляция держит поломку**: единственный повреждённый шов дал 51/138 failed —
  каскадная детекция работает (саботаж №2).
- **SSE-контракт виджета охраняется тестом**: точечное подавление события поймано
  целевым тестом (саботаж №3).
- **SQL-гвард конфига закрыт юнит-тестами**: 11 негативных кейсов сработали мгновенно
  (саботаж №1).
- **Anti-abuse живой**: curl-запрос заблокирован по User-Agent на реальном стеке.
- **Contract-тест профиля E2E ловит дрейф окружения** (поймал расхождение dev/Docker).
- **Read-only роль demo-стора создаётся правильно** (demo-recon, сверено с кодом):
  `NOSUPERUSER/NOINHERIT/NOBYPASSRLS`, `REVOKE ALL` + отбор TEMP у PUBLIC
  (`helperium_readonly_bootstrap.py:107-146`), GRANT SELECT ровно на 7 таблиц,
  writer-пароль не попадает в DSN — покрыто юнит-тестами `demo/tests/unit/test_autoparts_readonly_bootstrap.py`.
- **Публичный периметр Caddy узкий и fail-closed** (`Caddyfile.public`): только
  `/embed/*`, `POST /api/chat*`, `GET /api/agents/{id}/widget-config`; остальное `/api/*`
  и `/admin*` → 404 (включая `?tenant=`-обход).
- **Секреты не текут в git**: `.env*` под ignore (проверено `git check-ignore`),
  в трекинге только placeholders; корневые `*.db`/`*.sqlite` проигнорированы.
- **State-изоляция E2E**: named volumes, `E2E_TENANTS_DIR`/`E2E_DB_DIR`, cleanup без WAL-хвостов.
- **Graph ↔ код согласованы**: HTTP_CALLS-рёбра api→gateway→data-service и Leiden-кластеры
  соответствуют документированному data flow — код живёт так, как нарисован в доке.

## 4. Сопровождаемость по графу знаний

Слои графа читаются (entry: mcp-gateway, agent-db → core: helperium-go, helperium-sdk,
admin-dashboard), дубли rate-limiter'ов и httpclient'ов — главные точки консолидации.

Complexity-хотспоты (cognitive, без тестов) — сосредоточены в configgen/query-движке:
`config.Validate` 184, `generateSchemaForLLM` 148, `coerceNative` 144,
`NewRouterFromConfig` 129, `renderCondition` 124, `ParseRequest` 119, `validateArgs` 118.
Ручная арифметика placeholder-индексов в `strategy_handler.go` (~350 строк) и
классификация DB-ошибок по подстрокам (`database_error.go:20-40`) — зоны хрупкости.

Скор сопровождаемости: **7/10**. Помогают: слоистость, регрессионные тесты с именами
по багам (C1-fix, B1, P0-1), общий `helperium-go`, честные doc-комментарии.

Демо-стор (autoparts-store): **7/10, READY WITH NOTES** (детали в P2/P1 ниже).

## 5. Находки

### P0 — блокирует качественную публичную демку

1. **Прямой чат (`POST /api/chat`) без named-агента галлюцинирует** — полная верификация
   в разделе 2: выдуманные товары (NOT IN DB), ссылка на несуществующий тул
   `get_products`, псевдо-XML `university-server`, продолжение без инструментов после
   первого тула. Корни: университетский `SYSTEM_PROMPT` (`agent/prompts.py:21`),
   pool без фильтра по function calling, агент `default` без `provider_priority`.
   Для публичного виджета direct chat — основной путь.

### P1 — до публичной экспозиции

1. **Голосовой путь обходит anti-abuse** (проверено вручную по коду): `chat.py` —
   текстовый путь вызывает `check_abuse` (L220), `chat_voice_endpoint` (L256+) — не
   вызывает ни разу. Обход `max_user_turns_per_session`, `min_interval_ms`, лимитов
   длины и repeat-детекции; вдобавок voice идёт мимо `_buffered_agent_sse_events`
   (без защиты от обрыва продюсера). Публичный storefront с голосом = вектор абьюза.
2. **Локальный E2E-профиль разошёлся с Docker-профилем**: `dev.sh e2e` поднимает стек с
   `MCP_DEV=true` и двумя origin'ами, контракт-тест требует `MCP_DEV=false` и ровно
   `http://localhost:8080` → нативный прогон всегда 137+1 fail. Два «официальных»
   локальных пути проверяют разные вещи.
3. **DSN с кредами доступен через admin config DTO** (проверено вручную):
   `adminConfigResponseFromConfig` → `responseFromDataSource` возвращает `ReadonlyDSN`
   (`services/data-service/internal/server/admin.go:115-123`) — viewer-роль может
   прочитать его по GET. Плюс `dbTestHandler` логирует DSN через `slog.Info` и эхом
   возвращает его в HTTP-ответе (`services/admin-dashboard/internal/server/server.go:600,617-620`).
4. **LLM-ключи агентов хранятся plaintext**: api-service при старте пишет
   `ENCRYPTION_KEY not set — llm_config stored as plaintext`; ключ виден в `agents.sqlite`.
5. **Утечка внутренних URL в ошибках metadata-прокси gateway** (`cmd/main.go:495/516/541`,
   текст ошибок httpclient содержит `http://data-service:8084/...`) — против санитарного
   контракта публичных ошибок.
6. **Embed-виджет: `inlineMarkdown` не валидирует схему ссылок** (`embed/src/markdown.ts:143`):
   markdown-ссылка с `javascript:`-URL из недоверенного tool_result рендерится кликабельной
   ссылкой. Для встраиваемого на чужие домены виджета, показывающего контент клиентской БД,
   — XSS-вектор.
7. **Token compare не в constant-time** (admin-dashboard `server.go:288-291`,
   gateway `cmd/main.go:423`); `proxyToDataService` копирует все upstream-заголовки,
   включая `Set-Cookie` (`server.go:436`).
8. **Пять продающих швов без E2E-покрытия** (ревью e2e-map, сверено с файлами):
   session quota / восстановление сессии (только unit `test_anti_abuse.py`, `test_sessions.py`),
   RBAC viewer/admin (`VIEWER_TOKEN` — 0 вхождений в tests/e2e), embed widget (только TS unit),
   RAG-шов (`rag_url` в conftest объявлен, не используется), direct chat `POST /api/chat`
   (только CORS-preflight; защита от browser `X-Tenant-ID` — только unit).

### P2 — качество жизни и устойчивость

**Безопасность-гигиена (проверено вручную, если не указано иное):**
- **Upload SQLite: path traversal** — `tenantUploadSQLiteHandler` собирает
  `savePath := filepath.Join(dataDir, tenantID+ext)` (`server.go:752`) без валидации
  `tenantID` (никакого regex как в MCP-контракте) → `../`-tenantID выводит запись
  за пределы dataDir. Требует admin-токен, но defense-in-depth нарушен.
- **Admin-токен живёт в localStorage** без бэкенд-сессий (`src/domains/auth.ts:2` —
  «pure localStorage»): любой XSS в админ-фронтенде = кража токена.
- **PG read-only без `readonly_dsn` обеспечен только app-слоем**
  (`tenant_lifecycle.go:306-311`: SQLite автоматически выводит `mode=ro`-DSN, PG — нет;
  остаётся `ReadOnlyConn`). SQLite-путь сильнее.
- **Комментарий admin-CORS поощряет `CORS_ALLOW_ORIGINS=*`** (`server.go:219-222`) —
  против репозиторного контракта «не возвращай wildcard». Сам middleware не уязвим
  (не эхоит Origin, отдаёт конфигурируемое значение — проверено вручную).
- **`mcpRateLimitHits` строится из невалидированного `X-Tenant-ID`** до валидации
  (`ratelimit.go:105-110`, ревью go) — неограниченная карточность Prometheus-лейблов.

**Устойчивость:**
- Rate-limit map gateway без eviction; scope-кэш 256 без вытеснения (заполняется →
  постоянные 503 до рестарта); circuit breaker в Go-сервисах отсутствует
  (деградировавший data-service = каждый tool call ждёт до 30 с).
- Composite spending: стоимость записывается каждому tenant'у, лимиты проверяются
  только при одном tenant'е — зафиксировать решением (ADR).
- `SpendingChecker.record_spending` делает синхронную файловую персистенцию в async-цикле
  (ревью api, кодом не перепроверено — кандидат на профилирование).
- `_recent_messages` растёт безбавно; `display_name` — мёртвый код в agent loop.

**UX и операции:**
- `dev.sh start` может отдать ложный «ready»: healthcheck проходит через посторонний
  слушатель порта (в ходе аудита 8081 занимал ssh-туннель; api упал с
  `address already in use`, web молча проксировал в туннель — потеря ~20 минут отладки).
- Stale tenant из localStorage ломает UI (воспроизведено в браузере): `app.js` шлёт
  `X-Tenant-ID`, которого нет в стеке → все `/api/*` 404 + цикл из 10 retry манифеста
  без валидации tenant'а и без сообщения пользователю.
- Voice: при ненайденном named-агенте тихий фолбэк в direct chat (`chat.py`:
  `named_agent_scope(agent_data) if agent_data else None` → `direct_chat_scope()`)
  вместо явной ошибки.
- `?tenant=` query fallback в data-service (`tenant.go:390`) — формально против контракта
  «query parameter не выбирает tenant». Публичный Caddy блокирует (проверено), dev-прокси
  `demo/web` пропускает насквозь.
- `.data/tenants` замусорен 358 e2e-конфигами (4.7 MB), все грузятся в dev-runtime health.

**Тест-гигиена:**
- Порядко-зависимые module-фикстуры (`test_admin_lifecycle.py:34` — 11 тестов в цепочке
  register→…→delete; падение в середине каскадирует).
- Shared scenario-DB (`scenarios/shop`, `scenarios/auto-shop`) мутируется напрямую в
  `test_mcp_composite.py:44-49` и `test_mcp_validation.py:54` против правила приватных
  копий (`helpers.py:297-306`).
- `time.sleep(1)` ожидания подхвата tenant (`test_mcp_validation.py:171`);
  `_check_services` проверяет только data-service/mcp-gateway — недоступность api/web
  даёт сырые ConnectionError.
- CI dump-on-failure логирует только api и data-service (`.github/workflows/ci.yml:194-201`),
  у e2e-job нет явного timeout.
- Doc drift: e2e README заявляет 131 тест (фактически ~137); `test_v5_tool_chain` не
  тестирует заявленную цепочку.

**Демо-стор:**
- Wipe+reseed 1.7M строк при каждом старте public-стека (`docker-compose.public.yml:31`,
  `seed_data.py:25-29`) → долгий рестарт и окно без данных для демо.
- Захардкоженный список таблиц в грантах (`helperium_readonly_bootstrap.py:20-28`):
  новая таблица Django-миграции молча выпадет из SELECT-грантов и MCP-поверхности;
  теста на полноту грантов нет.
- Расхождение дефолтов виджета: compose `HELPERIUM_WIDGET_ENABLED=true` по умолчанию
  (`docker-compose.public.yml:70`) против false в `.env.public.example` — голый
  `docker compose up` включит чат против возможно несуществующего агента.
- Caddy без HSTS/CSP для проксируемых путей (`Caddyfile.public:3-11` — только
  nosniff/Referrer/Permissions); `sslmode=disable` в tenant DSN с паролем RO-роли
  в plaintext (`helperium_readonly_bootstrap.py:160`, `.data/tenants/autoparts.json`).
- Triple-документация (README / README.foreign / .foreign) с противоречивыми обещаниями.

## 6. Рекомендованный порядок работ до публичной демки

Неделя 1 (безопасность и корректность):
1. Голосовой путь: `check_abuse` + buffered SSE + явная ошибка вместо тихого фолбэка.
2. Убрать DSN из admin DTO/логов/ответов (`ReadonlyDSN` → уже существующий boolean
   `HasReadonlyDSN`), логирование `dbTestHandler`; regex-валидация `tenantID` в upload.
3. Sanitise ошибки metadata-прокси gateway; scheme-whitelist ссылок в embed markdown.
4. Синхронизировать `dev.sh e2e` с Docker-профилем (`MCP_DEV=false`, один origin).

Неделя 2 (демо-качество):
5. Починить прямой чат: system prompt под demo tenant, pin провайдера с подтверждённым
   function calling, acceptance-прогон 20-30 живых вопросов.
6. E2E-зонд read-only: тест, который через MCP/HTTP реально пытается выполнить запись
   и ждёт отказ (закрывает дыру саботажа №1).
7. E2E для session quota и RBAC viewer.

Постоянно (гигиена): TTL/cleanup для `.data/tenants`, eviction для rate-limit map и
scope-кэша, ADR про composite spending, валидация tenant из localStorage в UI,
port-ownership чек в `dev.sh start`.

## Приложение: состояние окружения после аудита

- Рабочее дерево чистое (саботажи откатаны), Docker CI-ресурсы удалены по контракту,
  autoparts volumes/контейнеры не тронуты.
- Единственное вмешательство в runtime: остановлены нативные Helperium-сервисы
  (`dev.sh stop`) для освобождения портов под изолированный E2E; впоследствии стек
  перезапущен (api на 18091 — порт 8081 занят внешним ssh-туннелем владельца).
