# Технический паспорт Helperium

## 🎯 1. Что это

**Self-hosted AI-платформа для бизнеса с SQL-базой.** Любая компания (интернет-магазин, университет, логистика, больница) подключает свою БД, платформа автоматически интроспектирует схему, генерирует инструменты доступа к данным, и на сайте бизнеса появляется **AI-чат-виджет**, который отвечает клиентам на вопросы по живым данным («сколько стоит тормозной диск», «какие товары в наличии»).

Ключевое отличие от RAG: агент **не индексирует документы**, а **запрашивает живую БД в read-only** через сгенерированные инструменты. Бизнес через админку контролирует, какие таблицы/колонки/операции видит агент. При этом есть тул для RAG и он работает но это не приоритет проекта, а просто дополнение под клиента.

### Два контура

| Контур | Для кого | Вход | Что делает |
|---|---|---|---|
| **Admin Dashboard** (:8085) | администратор платформы | браузер | управление тенантами (подключение БД), конфигами, агентами, RAG-документами, anti-abuse, возможность отключать тулы удобно и быстро |
| **Embed Widget** (на сайте клиента) | конечные пользователи | JS на сайте | чат с AI-агентом, ответы по данным через api-service |

### Ключевые понятия

- **Tenant** — один подключённый клиент: его БД + JSON-конфиг (какие таблицы/тулы видны агенту). Хранится в `.data/tenants/{id}.json`. Затем их уже использует агент (их тоже пожет быть несколько) связь many-to-many логика сложная подробнее в data-service и его документации. Идея что можно подключить несколько баз данных и оно будет работать и настраиваться.
- **Config** — декларативное описание тенанта: `entities[]`, `endpoints[]`, `mcp_tools[]`, в идеале генерируеться но его можно подправить (не рекумендуется без точных указаний и понимания зачем во время разработки этого делать точно не стоит).
- **MCP-тул** — сгенерированный инструмент, которым агент запрашивает данные.

## 🔀 2. Data flows

**LLM Chat (SSE stream)** — как виджет отвечает на вопрос:

```
Embed Widget → api-service (:8081) → orchestrator → LLM (LiteLLM)
  → tool_call → MCPClient → mcp-gateway (:8083) → data-service (:8084) → Client DB
  → SSE → Widget
```

**Запрос данных (админка):** `Admin Dashboard (:8085)` проксирует к api-service (тот же chat-хендлер) и к data-service (tenant CRUD/config). Прямой путь к данным — `data-service (:8084) → Query Engine (Expression AST → SQL) → Adapter.Conn → Client DB`.

`demo/web` (:8080) — dev-only набор стрниц (пока что две) где уже встроен виджет, одно рудемент второе полноценный django сайт со своей контейнерами вместе с дб, оно живет независимо от проекта и на реальном VPS не должно находиться.

**SSE-события (EventType):** `status`, `token`, `tool_call`, `tool_result`, `final`, `error`, `audio` + серверный маркер конца потока `done` — Язык общение с чатом на итоговой странице с виджетом.

## 🏗️ 3. Архитектура

### 3a. Agent pipeline

Каждый chat-запрос проходит конвейер этапов: входной guard (анти-injection) → открыть MCP-сессию и получить тулы + схему → **цикл «LLM отвечает → выполняет тулы»** до финального ответа → выходной guard (проверка утечки system prompt/кредов) → fallback при отсутствии финала → сохранение turn'а. Между этапами — контроль трат (spending) и лимит контекста.

Зависимости оркестратора — Protocol'ы (DI): легко подменить, например, реальный LLM на `ScriptedLLMProvider` (мок, см. [§8 E2E](#8-e2e-тесты-структура)).

Детали: `services/api-service/src/api_service/agent/` (оркестратор `LLMAgent` в `orchestrator.py`, конвейер в `pipeline.py`, протоколы в `protocols.py`). → доки: §5b (api-service)

### 3b. MCP — как агент получает данные

**MCP** (Model Context Protocol) — способ, которым агент вызывает данные: api-service ↔ mcp-gateway через SSE + JSON-RPC. Агент открывает MCP-сессию, получает **манифест тулов** (генерируется из конфига тенанта, кэшируется, можно сбросить), и вызывает тулы для получения данных.

→ доки: §5b (api-service, mcp-gateway)

### 3c. data-service — не semantic search

Поиск по данным — это **не семантический поиск**, а набор стратегий в `services/data-service/internal/search/`: текстовый поиск `grep`, фильтрация по полям `filter`, разведка схемы `schema` (distinct/min/max/общее число).

Для LLM-агента они экспонируются как MCP-тулы: N пер-энтити `filter_{entity}` + 5 консолидированных `db_*` (`db_map`, `db_describe`, `db_search`, `db_get`, `db_related`). Крупное изменение тулов — коммит `2cad540` (LLM-first tool surface: `filter_*` + `db_*`, убит id-enumeration). Это очень кратко на деле логика сложна и запутано и также является **core** всего проект в каком то смысле от того как это работает зависит смысл этого проекта. → доки: §5b (data-service)

### 3d. Tenant Lifecycle
При подключении клиента создаётся tenant (POST /admin/tenants), конфиг генерируется интроспекцией схемы (POST /admin/config/rewrite), хранится в `.data/tenants/{id}.json`. → доки: §5b (admin-dashboard)

### 3e. Config
Декларативный JSON-конфиг тенанта: описание сущностей, эндпоинтов, MCP-тулов. Часть генерится автоматически (entities, endpoints, mcp_tools, read_only), часть правится вручную (custom_queries, auth, описания тулов, introspection). Стратегии эндпоинтов — grep/filter/schema. Для разработки править в ручную — наплодить хардкода, в идеале генерация уже должна справляться с 90% задач и максимум добавлять способы правки в админку - ручное вмешательнство **только** ради временных тестов или реальной бд клиента.

Схема: `services/helperium-go/config/types.go:Config`. → доки: §5b (helperium-go)

### 3f. Adapter Pattern
Каждый тип БД (SQLite, PostgreSQL) реализует `datasource.Adapter` — как подключаться, интроспектировать схему, транслировать SQL. Добавление нового типа БД — новая реализация адаптера. → доки: §5b (data-service)

### 3g. HTTP Client Layer
Сервисы общаются по HTTP: mcp-gateway → data-service (конфиг, данные), api-service → mcp-gateway (MCP-сессия на tenant, SSE), demo-web → все (SSE streaming).

HTTP-матрица (11 каналов) — §5b (specs/общее) + `specs/api.openapi.yaml`.

### 3h. Tenant Isolation — database-level · tool-level · session-level
Тенанты изолированы на уровне БД и на уровне тулов (префикс `tenant-a__grep_products`); tenant_id не виден LLM как поле; PII-колонки исключаются из поиска. → доки: §5b (specs/общее)

### 3i. Anti-Abuse
Защита от пустых/жадных LLM-вызовов: 3 уровня (JSON Schema на входе тулов, server-side guard с лимитами/таймаутами, подсказки LLM при пустых результатах). Работает от недобросовестных пользователей. → доки: §5b (api-service)

## 🛠️ 4. Карта сервисов

**Ключевая идея:** основная дотошная документация лежит в директории сервиса или модуля. Если правки в этой части обязательны — чтение полной документации **обязательно**.

> **Полный список документов по сервису (включая doc/agents/ и specs/) — см. §5b.** Здесь только обзор: сервис → порт → роль → README.

| Сервис | Порт | Роль | README |
|---|---|---|---|
| **api-service** (Python) | :8081 | оркестратор, LiteLLM | [README](services/api-service/README.md) |
| **api-service/embed** (TypeScript) | :8081 | Embed-виджет | [README](services/api-service/embed/README.md) |
| **data-service** (Go) | :8084 | Expression AST → SQL, search strategies | [README](services/data-service/README.md) |
| **mcp-gateway** (Go) | :8083 | MCP SSE/JSON-RPC, composite, кэш манифеста | [README](services/mcp-gateway/README.md) |
| **admin-dashboard** (Go) | :8085 | Admin Web UI (Alpine.js) | [README](services/admin-dashboard/README.md) |
| **rag-service** (Python) | :8082 | ChromaDB, опционально | [README](services/rag/README.md) |
| **demo/web** (Python) | :8080 | Dev-only | [README](demo/web/README.md) |
| **agent-db** (Python) | — | Seedgen, materialize, e2e, core benchmark | [README](services/agent-db/README.md) |
| **helperium-go** (Go) | — | Config types, validation | [config.schema.md](specs/config.schema.md) · [config-migration.md](doc/agents/config-migration.md) |

**Web Service Multi-Tenancy:** [web-service.md](doc/agents/web-service.md)

## 📚 5. Карта документации

**Как пользоваться:** задача → маршрут ниже → читай по порядку. 🕐 = обзор (быстрый ответ), 📖 = deep-dive, 🔧 = операционка/чекист.

### 5a. Маршруты по задачам (главное — начинай отсюда)

| Задача | Читать (по порядку) | Глубина |
|---|---|---|
| MCP-тулы не работают / рвутся сессии | 🕐 `doc/agents/mcp-session-lifecycle` → 📖 `doc/agents/search-strategies` → 🔧 `services/mcp-gateway/README` | поверхность→вглубь |
| Добавить новый тип БД (MySQL...) | 📖 `doc/agents/adapter-pattern` → 🔧 `services/data-service/README` | 📖→🔧 |
| Изменить config / миграция версий | 📖 `doc/agents/config-migration` → `specs/config.schema.md` → 🔧 `specs/README` | 📖→spec |
| Онбординг нового клиента | 🔧 `doc/RUNBOOK` → 📖 `doc/agents/tenant-lifecycle` → 🔧 `services/admin-dashboard/README` | 🔧→📖 |
| Утечка данных / безопасность | 📖 `doc/agents/security-isolation` → 📖 `doc/agents/tool-call-safety-layers` → 🔧 `doc/PENTEST-CHEK` | 📖→🔧 |
| Anti-abuse / жадные LLM-вызовы | 📖 `doc/agents/anti-abuse` → 📖 `doc/agents/tool-call-safety-layers` | 📖 |
| CI падает | 🔧 `doc/agents/ci-cd` → `.github/workflows/ci.yml` → 🔧 `Makefile` | 🔧 |
| Как протестировать | 🔧 `AGENTS.md §8` (полное руководство) → `services/agent-db/tests/e2e/` | 🔧 |
| Настроить мониторинг | 🔧 `doc/monitoring` → `infra/docker/grafana/` + `infra/docker/prometheus/` | 🔧 |
| Кросс-сервисная проблема / HTTP | 🔧 `doc/api-flow` → 📖 `doc/agents/http-clients` → 🔧 `demo/web/README` | 🔧→📖 |
| Разобраться в data-service (вглубь) | 🔧 `services/data-service/README` → 📖 `doc/agents/data-service-refactor-audit` → 📖 `doc/agents/search-strategies` | 🔧→📖 |
| Разобраться в api-service (вглубь) | 🔧 `services/api-service/README` → 📖 `doc/agents/mcp-session-lifecycle` → 📖 `doc/agents/anti-abuse` | 🔧→📖 |
| Бенчмарк / качество ответов | 🔧 `doc/benchmark/README` → 📖 `doc/benchmark/core-benchmark` → 📖 `doc/benchmark/incident-camry` → `doc/benchmark/runs/README` | 🔧→📖 |
| Аудит data-service / регрессии | 📖 `doc/agents/data-service-refactor-audit` → 📖 `doc/benchmark/data-service-audit` → 📖 `doc/benchmark/plan-for-review` | 📖 |
| Исторический контекст / миграция | 🔧 `doc/FINAL_TASK` (план к pre-final, исторический) → 📖 `doc/agents/config-migration` | 🔧→📖 |
| Операции / дебаг / dev-скрипты | 🔧 `doc/agents/operations` → 🔧 `infra/scripts/dev.sh` → 🔧 `doc/agents/web-service` | 🔧 |
| Новый HTTP-эндпоинт / контракт | 🔧 `doc/agents/api-contracts` → `specs/api.openapi.yaml` → 🔧 `doc/api-flow` | 🔧 |

### 5b. Каталог документов (файл → сервис → повод читать → глубина)

Единый справочник: если задача не легла в маршрут §5a — ищи здесь по сервису или файлу. Сгруппирован по сервисам; бенчмарк и архивные отчёты выделены отдельными блоками.

**api-service**

| Файл | Когда читать | Глубина |
|---|---|---|
| `services/api-service/README.md` | env, endpoints, troubleshooting | 🔧 |
| `services/api-service/embed/README.md` | Widget API, Shadow DOM, CSP | 🔧 |
| `doc/agents/mcp-session-lifecycle.md` | MCP-сессии рвутся, тулы не работают | 🕐 |
| `doc/agents/anti-abuse.md` | Пустые/жадные LLM-вызовы | 📖 |
| `doc/agents/tool-call-safety-layers.md` | Утечка сырого JSON пользователю | 📖 |

**data-service**

| Файл | Когда читать | Глубина |
|---|---|---|
| `services/data-service/README.md` | search, skip rules, пакеты | 🔧 |
| `services/data-service/internal/configgen/README.md` | Config generation, mcp tools (пакет data-service) | 🔧 |
| `doc/agents/search-strategies.md` | Поиск, MCP-тулы, интроспекция | 📖 |
| `doc/agents/adapter-pattern.md` | Добавление нового типа БД | 📖 |

**helperium-go** (библиотека типов/валидации, без порта)

| Файл | Когда читать | Глубина |
|---|---|---|
| `doc/agents/config-migration.md` | Изменение config типов / миграция версий | 📖 |
| `specs/config.schema.md` | Config schema (детально) | 📖 |

**mcp-gateway**

| Файл | Когда читать | Глубина |
|---|---|---|
| `services/mcp-gateway/README.md` | MCP (tools, composite, кэш манифеста, RAG) | 🔧 |
| `doc/agents/mcp-session-lifecycle.md` | MCP-сессии рвутся, тулы не работают | 🕐 |

**admin-dashboard**

| Файл | Когда читать | Глубина |
|---|---|---|
| `services/admin-dashboard/README.md` | Admin UI | 🔧 |
| `doc/agents/ci-cd.md` | CI/CD | 🔧 |
| `doc/agents/tenant-lifecycle.md` | Настройка/отладка tenant | 📖 |
| `doc/agents/operations.md` | Логи, дебаг, dev-скрипты | 🔧 |
| `doc/agents/web-service.md` | Web-роутинг, multi-tenancy | 📖 |
| `doc/agents/http-clients.md` | Кросс-сервисные проблемы | 📖 |

**rag-service**

| Файл | Когда читать | Глубина |
|---|---|---|
| `services/rag/README.md` | RAG/ChromaDB | 🔧 |

**agent-db**


| Файл | Когда читать | Глубина |
|---|---|---|
| `services/agent-db/README.md` | Seedgen, e2e orchestration | 🔧 |
| `services/agent-db/agent_db/bench/README.md` | Core benchmark | 📖 |
| `AGENTS.md §8` | **Полное руководство по тестированию** (запуск, дебаг, написание) | 🔧 |
| `doc/agents/testing-guide.md` | Unit/Integration, Mutation testing, LLM E2E best practices | 📖 |

**demo** (demo/web + demo/autoparts-store)

| Файл | Когда читать | Глубина |
|---|---|---|
| `demo/web/README.md` | Dev-only reverse proxy | 🔧 |
| `demo/README.md` | Демо-сценарии и внешние интеграции | 🔧 |
| `demo/autoparts-store/README.md` | Демо-магазин автозапчастей | 🔧 |

**specs / общее**

| Файл | Когда читать | Глубина |
|---|---|---|
| `specs/README.md` | Config schema | 🔧 |
| `specs/fixtures/README.md` | Fixtures/seed | 🔧 |
| `doc/api-flow.md` | HTTP-матрица (11 каналов) | 🔧 |
| `doc/monitoring.md` | Мониторинг: метрики, PromQL, Grafana, алерты | 🔧 |
| `doc/RUNBOOK.md` | Онбординг нового клиента (cheat-sheet) | 🔧 |
| `doc/PENTEST-CHEK.md` | Pentest/security чеклист | 🔧 |
| `doc/agents/security-isolation.md` | Безопасность, tenant leaks | 📖 |
| `doc/agents/api-contracts.md` | Новые эндпоинты | 🔧 |

**benchmark**

| Файл | Когда читать | Тип |
|---|---|---|
| `doc/benchmark/README.md` | Дизайн и цели core-бенчмарка | 📖 живая документация |
| `doc/benchmark/core-benchmark.md` | Запуск, метрики и интерпретация результатов | 🔧 живая документация |
| `doc/benchmark/runs/README.md` | Реестр машинных прогонов и отчётов | 🔧 живая документация |

| `doc/benchmark/runs/2026-08-16-nvidia-nim-rebuilt-final-full-run-analysis.md` | Case-level разбор clean rebuilt full NIM run; содержит timeout и границу runtime/agent | 🗃️ архив: run analysis |
| `doc/benchmark/incident-camry.md` | Историческое расследование инцидента Camry | 🗃️ архив: расследование |
| `doc/benchmark/data-service-audit.md` | Исторический аудит data-service по итогам бенчмарка | 🗃️ архив: аудит |
| `doc/benchmark/demo-integration-audit.md` | Исторический аудит интеграции demo-web и виджета | 🗃️ архив: аудит |
| `doc/benchmark/plan-for-review.md` | Исполненный исторический план ревью бенчмарка | 🗃️ архив: план |

> `doc/benchmark/` — не целиком архив. Основные документы бенчмарка остаются живой документацией; отдельные расследования, аудиты и исполненные планы помечены как архивные прямо в этом каталоге.

**Архивные артефакты вне benchmark (не навигационные доки)**

| Файл | Что это · когда создано | Статус |
|---|---|---|
| `doc/agents/data-service-refactor-audit.md` | Аудит data-service после 4-дневного рефакторинга (2026-08-01) | ✅ все фиксы применены |
| `doc/FINAL_TASK.md` | План миграции к pre-final версии (исторический) | ✅ исполнен |

> **Архивные артефакты — это записи о завершённых событиях, а не навигационные доки.** Их нельзя удалять молча: это след расследований и решений. Читать их нужно со скепсисом — текст может быть устаревшим, но сохраняет контекст, причины решений и найденные ограничения.

> **Правило архивных отчётов:** новый аудит/инцидент/исполненный план по бенчмарку — в `doc/benchmark/` с шапкой (дата, контекст, статус) и пометкой `🗃️ архив` в каталоге §5b; новый аудит по другой теме — в `doc/agents/*-audit.md`. Чистить раз в N дней: устаревшее без ценности свести к сноске или удалить. Не превращать живую документацию в архив только из-за её расположения.

> 🕐 = обзор (быстрый ответ) · 📖 = deep-dive · 🔧 = операционка/чекист.

## 📦 6. Известные костыли и решения (что и почему так сделано)

Здесь фиксируются **решения, которые были приняты скриптом/настройкой и могут снова понадобиться**, а также известные костыли. Копятся и вычесываются. Полный журнал изменений — `CHANGELOG.md`. (Отчётные доки-артефакты — аудиты/инциденты/планы — см. блок «Артефакты» в §5b.)

> **Контракт CHANGELOG:** пиши сюда **только при коммите** (одна запись = один коммит, отражает коммит-месседж). Не дополняй каждой рабочей правкой — иначе журнал превращается в мусор. Во время работы над задачей CHANGELOG не трогается; запись добавляется в момент фиксации изменений.

## 🧬 7. Правила разработки (flow)

**Сначала изучи — потом правь.** Перед правками: доки по теме (Карта документации выше) → понять, какие сервисы/кодовые файлы задевает → граф знаний (как связано на уровне кода) → чтение кода/grep → только затем редактирование (TDD где уместно).

### 🛑 Когда остановиться и спросить

**Все доки в карте — источник правды.** Доверяй им (никаких меток «проверено/не проверено» — проверяй по коду при сомнении). Маршруты в §5 работают, **пока реальность совпадает с документацией**.

**Свежесть дока** — по метке `**Last verified:** <дата> (HEAD <hash>)` внизу дока: если дата старая или «после верификации были изменения» — сверь с кодом перед доверием. Это не повод избегать дока, а повод проверить его актуальность.

**Контракт метки:** в скобках указывается коммит, **на котором док проверялся** (прошлый, например `HEAD 07f7515`), а НЕ текущий. Не гоняй `git rev-parse` — это контракт, а не живой статус. Если метка устарела и док правится — обнови дату и хеш до актуального коммита.

**Док не совпал с реальностью?** Не паникуй и не игнорируй:
- **Уточни по коду** (граф знаний, git log, чтение файлов) — возможно, док устарел или ты читаешь не тот файл.
- **Если расхождение реальное — ИСПРАВЬ док** (сделай его соответствующим коду). Это твоя работа, не «порча доков».
- **Если не можешь определить, что правильно (док или код) — спроси пользователя** (см. ниже).
- Не «чини» код под док и не подгоняй док под код без понимания.

**Остановись и задай вопрос пользователю, если:**

1. **Прочитал маршрут, но реальность не совпадает** — и ты не можешь понять, что первично (док устарел или код сломан). Не выдумывай «правильную» версию.
2. **Тесты падают по непонятной причине** — если за 2-3 попытки не видно корня (не «почему-то не работает», а именно «не знаю почему»). Не начинай рефакторинг «на всякий случай».
3. **Требуется изменить контракт API/БД** — новый эндпоинт, смена JSON-поля, миграция схемы, новый тип БД. Это меняет межсервисные границы (см. `doc/api-flow`).
4. **Нужно ввести новую сущность/концепцию** — новая таблица, новый сервис, новый тул. Вайбкодинг-ловушка: «добавлю кажется полезное» → потом 3 часа переделки.
5. **Док и код противоречат друг другу** — сначала проверь, что ты не устарел (переиндексируй граф, git log). Если противоречие реальное — спроси, что первично: док или код.

**Правило: лучше 1 вопрос, чем 3 часа переделки.** Вопрос должен быть конкретным:
- «Док X ссылается на Y, но в коде только Z — обновить док или добавить Y?»
- «Тест T падает с E, корень не найден за 2 попытки. Дальше копать или пересобрать?»
- «Нужен новый эндпоинт P. Это меняет контракт api-service ↔ data-service. Подтвердишь?»

**Что НЕ делать:**
- ❌ «Чинить» то, что не сломано (док уже актуален, а ты его переписываешь)
- ❌ Выдумывать новые сущности «под задачу»
- ❌ Молча менять контракт и «ломать» другие сервисы
- ❌ Гадать, что имел в виду автор дока — спроси

**Исключения (когда можно действовать без вопроса):**
- Очевидные опечатки/битые ссылки в доках (проверено: путь не существует, аналог есть рядом)
- Скучные механические правки (форматирование, устаревшие пути после реструктуризации)
- Всё, что уже явно санкционировано пользователем в этой сессии

### 📝 Как задокументировать новую фичу (чтобы другие агенты её нашли)

Сделал фичу / изменил контракт / добавил тул — **документируй сразу, в том же коммите**, иначе через неделю это уже никто не вспомнит.

**Минимум (обязательно):**
1. **Обнови существующий док** по теме — если фича меняет поведение, описанное в маршруте §5a, обнови этот док (README сервиса, `search-strategies`, `api-flow`, `config-migration` и т.п.).
2. **Обнови метку верификации** — в каждом доке внизу есть строка `**Last verified:** <дата> (HEAD <hash>)` (в AGENTS.md — секция «🧬 Verification»). Любая правка дока = обновление этой метки: дата + комментарий, что именно сверено/изменено. **Контракт:** в скобках указывай коммит, на котором док проверялся (обычно прошлый/текущий рабочей ветки), а НЕ будущий — агенту не нужно гонять `git rev-parse`; метка просто говорит «этот док сверялся на коммите X». Если метка старая (например 2026-08-02) и «после верификации были изменения» — это сигнал, что док мог устареть, сверь с кодом при работе.
3. **Обнови каталог §5b** — добавь строку с новым доком в соответствующий сервис-блок (или артефакты, если это аудит/инцидент/план).
4. **Впиши док в маршрут §5a** — добавь или расширь строку задачи, из которой агент придёт к этому доку. Если фича — новый тип задачи (например «RAG»), добавь новый маршрут.
5. **CHANGELOG.md обновляй ТОЛЬКО при коммите** — не каждой правкой. Запись = то, что уходит в коммит-месседж (что сделано, какие сервисы/доки затронуты). Во время работы не трогай — напишешь при коммите.
6. **Прогони `make ci-docs`** (CI-джоба `docs-links` в `.github/workflows/ci.yml`) — проверяет, что все пути в доках существуют (нет мёртвых ссылок). Если он падает — поправь пути до коммита, не отключай чек.

**Структура дока (чтобы быстро читался):**
- Заголовок `# Тема` + 1-2 строки «когда читать это»
- Что делает фича (2-4 предложения)
- Как это связано с остальным (ссылки на другие доки/README, если есть)
- Код-примеры (если уместно)
- Известные костыли/ограничения (если есть)

**Проверка «агент найдёт»:** после написания дока пройдись по §5a/§5b — сможет ли новый агент, не знающий твоей фичи, прийти к этому доку по задаче? Если нет — добавь маршрут или ссылку. Правило «один вход»: каждый док достижим из маршрутов (§5a) или каталога (§5b). CI-чек `make ci-docs` подтвердит: док упомянут в AGENTS.md (не сирота), пути существуют.

Проектные правила:
- **Запрещено: SQL в коде приложения** — только HTTP к data-service. SQL допустим в тестах / bash / context-mode (на тестовых БД даже требуется).

## 🧪 8. Тестирование

```bash
make ci-test-py    # Python unit/integration (API, RAG, web, SDK)
make ci-test-go    # Go unit (data-service, mcp-gateway, helperium-go)
make ci-test-embed # TS widget tests + build
make ci-admin      # Admin dashboard tests
```

**E2E (нужны сервисы):**
```bash
./infra/scripts/dev.sh start
uv run pytest services/agent-db/tests/e2e/ -v
./infra/scripts/dev.sh stop
```

**Docker (`--profile test`) — только для CI:** см. `doc/agents/testing-guide.md`

**Полное руководство:** `doc/agents/testing-guide.md` — unit/integration, e2e, ScriptedLLMProvider, mutation testing, troubleshooting.

> Последний раз проверено: 2026-08-07

## 🧬 Verification

```
Last verified: 2026-08-10 (HEAD be9a991) — разделены живая документация benchmark и архивные отчёты
2026-08-11 (рабочая ветка) — переработка бенча: verdict (CORRECT/PARTIAL/WRONG/ERROR) + таксономия ErrorClass, новые проверки (SKU, LOST_TOTAL, FALSE_UNCERTAINTY, budget, loop, dedupe, error payload, derived), отчёт (verdicts/percentiles/run_metadata), diff_reports, кейсы обогащены, smoke починен и прогнан. 73 теста. Первый реальный baseline: 80% CORRECT / 16% PARTIAL / 4% WRONG (reports/baseline-c1d7f81). Обновлены README бенча + core-benchmark.md + CHANGELOG.
2026-08-15 (рабочая ветка) — payment value aliases, versioned autoparts benchmark policy и generic filter contract были проверены на NIM runs; старые raw/re-evaluation artifacts теперь сохранены только в локальном reversible archive, а active registry содержит canonical rebuilt run от 2026-08-16.
2026-08-16 (рабочая ветка) — `run.errors` классифицируется как `ERROR`/`INFRA_ERROR`; benchmark fixtures дополнены допустимыми deterministic tool paths; FilterStrategy поддерживает whitelist-validated field comparison (`old_price__gt_field=price`); policy обновлена до `autoparts-benchmark-v2`, а leaked-thinking preamble не может стать final. `make ci-test-go`, `make ci-test-py` и `make ci-docs` пройдены; Docker live manifest и filter total=72 подтверждены после seed=42 + tenant rewrite. Новый NIM benchmark не запускался.
См. полный журнал: CHANGELOG.md
```
