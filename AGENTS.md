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

Детали: `services/api-service/src/api_service/agent/` (оркестратор `LLMAgent` в `orchestrator.py`, конвейер в `pipeline.py`, протоколы в `protocols.py`); guard'ы — [doc/agents/anti-abuse.md](doc/agents/anti-abuse.md), [doc/agents/tool-call-safety-layers.md](doc/agents/tool-call-safety-layers.md) и самая важный документ: [service/api-service/README](services/api-service/README)

### 3b. MCP — как агент получает данные

**MCP** (Model Context Protocol) — способ, которым агент вызывает данные: api-service ↔ mcp-gateway через SSE + JSON-RPC. Агент открывает MCP-сессию, получает **манифест тулов** (генерируется из конфига тенанта, кэшируется, можно сбросить), и вызывает тулы для получения данных.

Детали: [doc/agents/mcp-session-lifecycle.md](doc/agents/mcp-session-lifecycle.md) (рвущиеся сессии, GC), [doc/agents/search-strategies.md](doc/agents/search-strategies.md) (как устроены тулы/поиск) и самая важный документ: [service/mcp-getway/README](services/mcp-gateway/README)

### 3c. data-service — не semantic search

Поиск по данным — это **не семантический поиск**, а набор стратегий в `services/data-service/internal/search/`: текстовый поиск `grep`, фильтрация по полям `filter`, разведка схемы `schema` (distinct/min/max/общее число).

Для LLM-агента они экспонируются как MCP-тулы: N пер-энтити `filter_{entity}` + 5 консолидированных `db_*` (`db_map`, `db_describe`, `db_search`, `db_get`, `db_related`). Крупное изменение тулов — коммит `2cad540` (LLM-first tool surface: `filter_*` + `db_*`, убит id-enumeration). Это очень кратко на деле логика сложна и запутано и также является **core** всего проект в каком то смысле от того как это работает зависит смысл этого проекта.

Детали: [search-strategies.md](doc/agents/search-strategies.md), [adapter-pattern.md](doc/agents/adapter-pattern.md) и самое важное в [services/data-service/README](services/data-service/README)

### 3d. Tenant Lifecycle
При подключении клиента создаётся tenant (POST /admin/tenants), конфиг генерируется интроспекцией схемы (POST /admin/config/rewrite), хранится в `.data/tenants/{id}.json`. [tenant-lifecycle.md](doc/agents/tenant-lifecycle.md).

### 3e. Config
Декларативный JSON-конфиг тенанта: описание сущностей, эндпоинтов, MCP-тулов. Часть генерится автоматически (entities, endpoints, mcp_tools, read_only), часть правится вручную (custom_queries, auth, описания тулов, introspection). Стратегии эндпоинтов — grep/filter/schema. Для разработки править в ручную — наплодить хардкода, в идеале генерация уже должна справляться с 90% задач и максимум добавлять способы правки в админку - ручное вмешательнство **только** ради временных тестов или реальной бд клиента.

Схема: `services/helperium-go/config/types.go:Config`. [specs/config.schema.md](specs/config.schema.md), [config-migration.md](doc/agents/config-migration.md).

### 3f. Adapter Pattern
Каждый тип БД (SQLite, PostgreSQL) реализует `datasource.Adapter` — как подключаться, интроспектировать схему, транслировать SQL. Добавление нового типа БД — новая реализация адаптера. [adapter-pattern.md](doc/agents/adapter-pattern.md).

### 3g. HTTP Client Layer
Сервисы общаются по HTTP: mcp-gateway → data-service (конфиг, данные), api-service → mcp-gateway (MCP-сессия на tenant, SSE), demo-web → все (SSE streaming).

HTTP-матрица (11 каналов): [doc/api-flow.md](doc/api-flow.md). Детали: [http-clients.md](doc/agents/http-clients.md). А также сам openapi в specs/ .

### 3h. Tenant Isolation — database-level · tool-level · session-level
Тенанты изолированы на уровне БД и на уровне тулов (префикс `tenant-a__grep_products`); tenant_id не виден LLM как поле; PII-колонки исключаются из поиска. [security-isolation.md](doc/agents/security-isolation.md).

### 3i. Anti-Abuse
Защита от пустых/жадных LLM-вызовов: 3 уровня (JSON Schema на входе тулов, server-side guard с лимитами/таймаутами, подсказки LLM при пустых результатах). [anti-abuse.md](doc/agents/anti-abuse.md), [tool-call-safety-layers.md](doc/agents/tool-call-safety-layers.md). Работает от недобросовестных пользователей.

## 🛠️ 4. Карта сервисов

**Ключевая идея:** основная дотошная документация лежит в директории сервиса или модуля. Если правки в этой части обязательны — чтение полной документации **обязательно**.

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
| **helperium-go** (Go) | — | Config types, validation | [configgen/README.md](services/data-service/internal/configgen/README.md) |

**Web Service Multi-Tenancy:** [web-service.md](doc/agents/web-service.md)

## 📚 5. Карта документации

**Принцип (flow решения проблемы):**
```
есть проблема
  → понять, в какие сервисы задевает
  → начать читать: doc/agents/*.md (поверхностные deep-dives) или сервисный README (вглубь)
  → понять, какие кодовые файлы нужны
  → копать в графе знаний (если доступен) (как связано на уровне кода) или читать файлы напрямую
  → собрать картину: идея + общее описание + код
```

### doc/agents/ — deep dives по аспектам (читать при работе по теме)

| Файл | Когда читать | Размер |
|---|---|---|
| `search-strategies.md` | Поиск, MCP-тулы, интроспекция | 14.5 KB |
| `config-migration.md` | После изменения config типов | 16.9 KB |
| `testing-guide.md` | Написание/запуск тестов | 15.0 KB |
| `data-service-refactor-audit.md` | История аудита data-service | 24.2 KB |
| `tool-call-safety-layers.md` | Утечка сырого JSON пользователю | 8.9 KB |
| `mcp-session-lifecycle.md` | MCP-сессии рвутся, тулы не работают | 7.1 KB |
| `adapter-pattern.md` | Добавление нового типа БД | 6.3 KB |
| `web-service.md` | Web-роутинг, multi-tenancy | 5.1 KB |
| `ci-cd.md` | CI/CD | 5.1 KB |
| `http-clients.md` | Кросс-сервисные проблемы | 3.4 KB |
| `anti-abuse.md` | Пустые/жадные LLM вызовы | 3.0 KB |
| `tenant-lifecycle.md` | Настройка/отладка tenant | 2.2 KB |
| `operations.md` | Логи, дебаг, dev-скрипты | 1.8 KB |
| `security-isolation.md` | Безопасность, tenant leaks | 1.7 KB |
| `api-contracts.md` | Новые эндпоинты (сирота — см. ниже) | 1.1 KB |

### Service READMEs

| README | Когда читать | Размер |
|---|---|---|
| `services/api-service/README.md` | api-service (env, endpoints, troubleshooting) | 498 строк |
| `services/data-service/README.md` | data-service (search, skip rules, пакеты) | 364 |
| `services/mcp-gateway/README.md` | MCP (tools, composite, кэш манифеста, RAG) | 222 |
| `services/admin-dashboard/README.md` | Admin UI | 158 |
| `services/rag/README.md` | RAG/ChromaDB | 118 |
| `services/agent-db/README.md` | Seedgen, e2e orchestration | 160 |
| `demo/web/README.md` | Dev-only reverse proxy | 258 |
| `specs/README.md` | Config schema | 247 |
| `services/api-service/embed/README.md` | Widget API, Shadow DOM, CSP | 275 |
| `services/data-service/internal/configgen/README.md` | Config generation, mcp tools | 167 |
| `services/agent-db/agent_db/bench/README.md` | Core benchmark | 150 |

### Остальные доки

| Файл | Когда читать | Размер |
|---|---|---|
| `doc/api-flow.md` | HTTP-матрица (11 каналов между сервисами) | 11.6 KB |
| `doc/monitoring.md` | Мониторинг: метрики, PromQL, панели Grafana, алерты | 11.9 KB |
| `doc/benchmark/core-benchmark.md` | Core benchmark design | 8.9 KB |
| `doc/benchmark/data-service-audit.md` | Рой-аудит data-service (фиксы) | 9.4 KB |
| `doc/benchmark/plan-for-review.md` | План бенча для review | 7.6 KB |
| `doc/benchmark/incident-camry.md` | Расследование инцидента Camry | 3.8 KB |
| `doc/benchmark/README.md` | Benchmark design overview | 9.0 KB |
| `specs/config.schema.md` | Config schema (детально) | 8.1 KB |
| `specs/fixtures/README.md` | Fixtures/seed | 2.8 KB |

> **Сироты:** `doc/agents/api-contracts.md` — нет входящих ссылок, кандидат на встраивание в `specs/README.md`.

## 📦 6. Артефакты (известные решения/костыли)

Здесь фиксируются **решения, которые были приняты скриптом/настройкой и могут снова понадобиться**, а также известные костыли. Артефакты копятся и вычесываются. Полный журнал изменений — `CHANGELOG.md`.

- **`doc/monitoring.md`** — мониторинг: метрики, PromQL, панели Grafana, алерты (единый док).
- **`doc/benchmark/data-service-audit.md`** — рои-аудиты и их фиксы (TDD, 28 тестов).
- **`doc/benchmark/incident-camry.md`** — пример расследования (галлюцинация модели, реальная проблема).

## 🧬 7. Правила разработки (flow)

**Сначала изучи — потом правь.** Перед правками: доки по теме (Карта документации выше) → понять, какие сервисы/кодовые файлы задевает → граф знаний (как связано на уровне кода) → чтение кода/grep → только затем редактирование (TDD где уместно).

Проектные правила:
- **Запрещено: SQL в коде приложения** — только HTTP к data-service. SQL допустим в тестах / bash / context-mode (на тестовых БД даже требуется).

## 🧪 8. E2E-тесты (структура)

| Каталог | Что это | CI |
|---|---|---|
| `services/agent-db/tests/e2e/` | **124 теста**, без LLM, локальные SQLite | ✅ job `test-e2e` |
| `services/agent-db/tests/e2e-llm/` | реальный LLM (opt-in, скипается без ключа) | ❌ вне CI |
| `tests/external/` | внешние БД (PostgreSQL и т.п.) — только документация | ❌ |

**Запуск:** нативно `./scripts/dev.sh e2e` (нужны поднятые сервисы) · Docker `docker compose --profile test up e2e --abort-on-container-exit --exit-code-from e2e`.

**ScriptedLLMProvider** (`services/api-service/src/api_service/agent/scripted_provider.py`): мок LLM через env `USE_SCRIPTED_LLM=1 SCRIPTED_LLM_PATH=script.jsonl` — детерминированный прогон pipeline (chat → tool_call → tool_result → SSE) **без реальной модели и траты денег**. 11 тестов в `services/agent-db/tests/e2e/test_scripted_llm.py` (v5: `db_*`/`filter_*`), включая record mode, guard'ы, recovery.

## 🧬 Verification

```
Last verified: 2026-08-06 (реструктуризация репозитория: services/ + infra/; HEAD ca6c95a).
См. полный журнал: CHANGELOG.md
```

**Правило:** после любой правки документов — обновить дату + хеш здесь (одна строка). Полный лог — в `CHANGELOG.md`.
