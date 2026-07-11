# AGENTS.md — Технический паспорт проекта для AI-агентов

Этот документ является основной точкой входа для AI-агента. Он содержит архитектурный контекст, карту навигации и операционные инструкции, необходимые для внесения изменений в код без потери целостности системы.

## 🎯 1. О проекте и видении
**Проект**: Платформа для развертывания AI-агентов над произвольными базами данных клиентов.
**Текущий вектор**: Трансформация из доменного решения (один вуз) в **Generic B2B SaaS**.

**Ключевая идея**: Клиент подключает свою БД $\rightarrow$ Платформа интроспектирует схему $\rightarrow$ Автоматически генерируется REST API и MCP-инструменты $\rightarrow$ AI-агент получает доступ к данным без написания кода под каждую БД.

### 🔄 Архитектурный Pipeline (Как это работает)
Путь запроса от пользователя до данных в **single-tenant режиме** (backward compat):
`User Request` $\rightarrow$ `demo-web` (проксирует `X-Tenant-ID`) $\rightarrow$ `demo-api` (формирует Persona агента и системный промпт) $\rightarrow$ `mcp-gateway` (динамически запрашивает манифест инструментов из data-service для конкретного TenantID) $\rightarrow$ `data-service` (роутит запрос в конкретную БД клиента через `TenantStore` $\rightarrow$ generic query builder $\rightarrow$ SQL $\rightarrow$ DB).

Путь запроса в **composite multi-tenant режиме** (один агент — N tenant'ов):
`User Request` $\rightarrow$ `demo-web` (проксирует `X-Tenant-ID` как comma-separated список: `tenant-a,tenant-b`) $\rightarrow$ `api-service/src/api_service/server.py` (парсит в `tenant_ids: list[str]`) $\rightarrow$ `api-service/src/api_service/agent/orchestrator.py` (передаёт `tenant_ids` в MCPClient) $\rightarrow$ `api-service/src/api_service/agent/mcp_client.py` (открывает одну SSE сессию с `X-Tenant-ID: tenant-a,tenant-b`, получает инструменты от всех tenant'ов) $\rightarrow$ `mcp-gateway` (`resolveTenantIDs()` → если один tenant: legacy MCPServer без префикса; если N > 1: `createCompositeServer([]string)` с префиксом `{tenantID}__` для каждого инструмента) $\rightarrow$ каждый вызов инструмента направляется хендлером с замыканием tenantID в data-service с соответствующим `X-Tenant-ID: {tenantID}` $\rightarrow$ `TenantStore` → generic query builder → SQL → DB.

---

## 🛠️ 2. Карта сервисов и навигация
Каждый сервис независим и общается по HTTP. Для детального изучения архитектуры каждого модуля используйте ссылки ниже.

| Сервис | Порт | Ответственность | Документация |
|---|---|---|---|
| **Data-service** (Go) | `:8084` | Generic CRUD/Query прокси. Интроспекция БД, генерация API, config hot-reload, write-tool approval flow. | [README](data-service/README.md) |
| **MCP-gateway** (Go) | `:8083` | MCP сервер (SSE/JSON-RPC). Динамическая генерация инструментов из data-service. | [README](mcp-gateway/README.md) |
| **Admin Dashboard** (Go) | `:8085` | Веб-интерфейс для администрирования: tenant CRUD, конфиги, тулы, RAG, агенты. Alpine.js UI. | [README](admin-dashboard/README.md) |
| **RAG** (Python) | `:8082` | Поиск по документам (ChromaDB), чанкинг, эмбеддинги (local/LiteLLM), кэш (Local/Redis), admin config API, Prometheus метрики, re-embedding pipeline. | [README](rag/README.md) |
| **API** (Python) | `:8081` | Оркестратор агента, LiteLLM, Agent Store (CRUD), rate limiter, управление сессиями и бэклогом. Встраиваемый чат-виджет: [embed/README.md](api-service/embed/README.md). | [AGENT_WORKFLOW](api-service/README.md) |
| **Web** (Python) | `:8080` | UI-интерфейс + reverse-proxy. Проксирует `X-Tenant-ID` и поддерживает tenant routing. | [README](demo/web/README.md) |
| **SDK** (Python) | — | Общие Pydantic-модели и клиенты для сервисов. | [pyproject.toml](agent-tutor-sdk/pyproject.toml) |

> **Мониторинг (v1.1.0):** Все сервисы отдают Prometheus-метрики на `/metrics`.
> См. [секцию 10](#-10-monitoring--observability): Prometheus (:9090) + Grafana (:3000) с предустановленным дашбордом (12 панелей).

### 🚩 Глобальные документы

- **Стратегия**: [doc/FINAL_TASK.md](doc/FINAL_TASK.md) — план к pre-final версии и критерий готовности.
- **Конфигурация**: [.env.example](.env.example) — все 180+ переменных окружения.
- **API-контракты и config schema**: [specs/README.md](specs/README.md) — OpenAPI specs, JSON Schema валидация конфига data-service.
- **Agent Store**: [api-service/src/api_service/agent_store.py](api-service/src/api_service/agent_store.py) — SQLite-регистр агентов с CRUD API.

### 🌐 Web Service — Multi-Tenancy Architecture

Web-сервис (`demo/web/server.py`) — тонкий reverse-proxy с поддержкой multi-tenancy:

**Два режима маршрутизации:**

1. **Стандартный (через заголовок `X-Tenant-ID`):**
   ```
   Browser → GET /api/data/students (X-Tenant-ID: tenant-a)
          → web:8080
          → data-service:8084/students (X-Tenant-ID: tenant-a)
   ```

2. **Явный tenant в URL (демо-режим):**
   ```
   Browser → GET /api/tenant/tenant-a/data/students
          → web:8080
          → data-service:8084/students (X-Tenant-ID: tenant-a)
   ```

**Ключевые маршруты:**
- `GET /api/manifest` → data-service `/mcp/manifest` (с tenant)
- `GET /api/data/{entity}` → data-service `/{entity}` (students, teachers, disciplines...)
- `GET /api/data/stats` → data-service `/stats`
- `GET /api/rag/documents` → rag-service `/documents/list`
- `GET/POST /api/chat` → api-service `/api/chat` (SSE)
- `GET /embed/{path}` → прокси на api-service `/embed/{path}` (виджет: [api-service/embed/README.md](api-service/embed/README.md))
- `GET/POST /api/tenant/{tenant_id}/{path:path}` — универсальный маршрут:
  - `data/{entity}` → data-service
  - `rag/{path}` → rag-service
  - `api/{path}` / `chat` → api-service (SSE для chat)

**Тесты:**
```bash
uv run pytest demo/web/tests/unit/ -v  # 30 тестов (proxy + urls)
uv run agent-db e2e-full               # полный e2e пайплайн
```

---

## 🚀 3. Эксплуатация и разработка (Manual)

### 🛠️ Нативный запуск: `scripts/dev.sh`
Скрипт `dev.sh` — основная точка управления в среде Mac/Linux.

**Управление сервисами:**
- `./scripts/dev.sh start` — поднять весь стек в правильном порядке (data $\rightarrow$ rag $\rightarrow$ mcp $\rightarrow$ api $\rightarrow$ web).
- `./scripts/dev.sh stop` / `restart` / `status` — управление жизненным циклом.
- `./scripts/dev.sh logs {service|all}` — просмотр логов из `.data/logs/`.

### 🐳 Docker-запуск
Если нативная среда недоступна или требуется изоляция:
- `docker compose up -d` — запуск всех 6 сервисов в Dev-режиме.
- `docker compose --profile prod up -d` — запуск с Caddy (HTTPS через Let's Encrypt) для Production.
- `docker compose build` — пересборка образов после изменений в Dockerfile.
- **Тома**: Данные хранятся в `./.data/` (БД, индексы ChromaDB, кэш моделей).

### 🗄️ Работа с данными и сценариями (Критично для тестов)
Сервис `data-service` поддерживает фабрику тестовых БД через CLI-утилиту `agent-db`.
- `uv run agent-db scenario list` — список сценариев (`sqlite-testseed`, `big-testseed`, `shop`...).
- `uv run agent-db materialize <name>` — создать/пересоздать БД из сценария.
- `uv run agent-db tenant register <name>` — зарегистрировать тенанта.
- `uv run agent-db tenant list` — список активных тенантов.
- `uv run agent-db e2e --tenants default,shop` — полный E2E: materialize + register + proxy + SSE chat.
- `uv run agent-db e2e-data` — детерминированные тесты изоляции данных и admin API.
- `uv run agent-db e2e-mcp` — детерминированные тесты MCP-инструментов.
- `uv run agent-db e2e-full` — все три уровня (data + mcp + chat).

---

## 🧪 4. Регрессионное тестирование
Перед коммитом или после правок **обязательно** проверить следующие уровни:

### 1. Python Unit/Integration тесты
```bash
uv run pytest rag/tests/                   # RAG (индексация, поиск, pipeline, repository)
uv run pytest api-service/src/api_service/tests/              # API (OpenAPI spec, backlog, sessions, rate limiter)
uv run pytest demo/web/tests/              # Web (26 proxy + 4 url mapping тестов)
uv run pytest demo/tests/                  # Settings (18 тестов конфигурации из env)
uv run pytest agent-tutor-sdk/tests/       # SDK модели и seedgen
```

> Примечание: тесты MCP-клиента и оркестратора помечены `@pytest.mark.skip` — ожидают переписывания под новый MCP SDK протокол.

### 2. Go Unit/Integration тесты
```bash
go test ./data-service/... ./mcp-gateway/...  # 391 тестов в 14 пакетах
```

### 3. Сквозные интеграционные скрипты
- `uv run agent-db e2e-data` — изоляция данных между tenant'ами (8 детерминированных тестов).
- `uv run agent-db e2e-mcp` — динамические MCP-инструменты (3 детерминированных теста).
- `uv run agent-db e2e-full` — все три уровня: data + mcp + SSE chat.

---

## 🧠 5. Использование Knowledge Graph (Graphify)

Проект содержит граф зависимостей (`graphify-out/`). **Не читай код вслепую — используй граф.**

**Алгоритм работы для агента:**
1. **Ориентирование**: Вместо `grep` используй `graphify_explain({ concept: "ClassName" })`, чтобы увидеть всех, кто вызывает этот класс и от кого он зависит.
2. **Трассировка**: Чтобы понять, как данные текут от API до БД, используй `graphify_path({ from: "APIHandler", to: "DatabaseAdapter" })`.
3. **Поиск**: Используй `graphify_query({ question: "...", mode: "bfs" })` для поиска взаимосвязей в архитектуре.
4. **Обновление**: После внесения правок в код выполни `graphify_update({ path: "." })`, чтобы граф оставался актуальным.

---

## 🔒 6. Security & Tenant Isolation

Изоляция данных и инструментов между tenant'ами обеспечивается на трёх уровнях:

### Data-service level
`TenantStore` хранит изолированные конфиги и подключения к БД для каждого tenant'а. Каждый tenant имеет свою БД (отдельный SQLite файл или PG схему). `X-Tenant-ID` определяет, к какой БД идёт запрос. Нет единой таблицы с tenant_id колонкой — физически разные БД.

**Write-tool approval flow:** тулы с операциями записи (`INSERT`, `UPDATE`, `DELETE`) по умолчанию выключены. Администратор подтверждает каждый такой тул через admin API (`POST /admin/tools/{toolName}/approve`) перед тем, как он появится в MCP-манифесте. Подробнее: [data-service/README.md](data-service/README.md).

### mcp-gateway level
Инструменты регистрируются с tenantID в замыкании (closure) через `makeHandler(td, client, tenantID)`. Даже если клиент укажет `X-Tenant-ID: tenant-a,tenant-b`, вызов `tenant-a__list_students` пойдёт строго в data-service с `X-Tenant-ID: tenant-a`. Инструменты tenant-c не существуют в этой сессии, если tenant-c не был указан при открытии SSE.

### api-service level
Список tenant'ов определяется заголовком `X-Tenant-ID` от web-прокси и передаётся как `tenant_ids: list[str]` через orchestrator → MCPClient. Если tenant не указан в заголовке, его данные и инструменты недоступны.

### Верификация изоляции
- `e2e-data` — data-level: tenant-a не видит БД tenant-b (разные SQLite файлы).
- `e2e-mcp` — tool-level: tenant-shop не может вызвать инструмент `list_student` tenant-uni (возвращается ошибка).
- `e2e-mcp-composite` — composite routing: `tenant-uni__list_student` идёт строго в data-service tenant-uni, `tenant-shop__list_product` строго в data-service tenant-shop, несмотря на одну SSE сессию.

Никаких cross-tenant утечек.

---

## 📄 7. API контракты и specs/ — как это работает

[specs/README.md](specs/README.md) — полное описание. Кратко:

```
specs/
├── config.schema.json        # JSON Schema — runtime-валидация конфига data-service
├── config.example.json       # Пример конфига (SQLite, тесты/dev)
├── config.postgres.json      # Пример конфига (PostgreSQL, production)
├��─ api.openapi.yaml — автогенерация из FastAPI
├── rag.openapi.yaml          # OpenAPI rag — автогенерация из FastAPI
└── ...
```

**Два типа контрактов:**
- `config.schema.json` — загружается при старте data-service, без него сервер **не стартанёт**. Меняешь → обнови примеры → `go test`.
- `api.openapi.yaml` / `rag.openapi.yaml` — **слепки** автогенерации FastAPI. Первичен код. Тесты ловят рассинхрон:
  ```bash
  uv run pytest api-service/src/api_service/tests/unit/test_openapi_api.py
  uv run pytest rag/tests/unit/test_openapi_spec.py
  ```

---

## ⚠️ 8. Важные ограничения и правила
- **Никакого SQL в Python**: Весь доступ к данным идет ТОЛЬКО через HTTP-запросы к `data-service`.
- **Generic-подход**: При добавлении новых полей или сущностей не хардкодь их в коде — конфиг data-service описывает сущности декларативно.
- **Stateless**: Сервисы не должны хранить состояние сессии локально (кроме кэша сессий в SQLite), чтобы обеспечить масштабируемость.
- **Config schema — runtime-обязательна**: `config.schema.json` должен быть доступен по одному из путей поиска. Если удалить — data-service не стартанёт.

---

## ✅ 9. CI/CD и Quality Gates

Проект проходит полный CI-пайплайн на GitHub Actions при каждом пуше в `main`/`master`/`develop`.

### 🔄 CI Pipeline (`.github/workflows/ci.yml`)

| Job | Что проверяет | Команда |
|---|---|---|
| `lint-python` | Ruff lint, Ruff format check, Pyright type check | `ruff check`, `ruff format --check`, `pyright` |
| `test-python` | Все Python unit/integration тесты | `pytest api-service/src/api_service/tests/` |
| `lint-go` | golangci-lint v2 (errcheck, staticcheck, unused, ineffassign, govet) | `golangci-lint run ./...` |
| `test-go` | Go тесты в data-service и mcp-gateway | `go test ./... -count=1 -timeout 180s` |

**Pipeline считается зелёным**, когда все 4 джобы проходят. Каждая джоба падает независимо — если хоть одна красная, CI красный.

### 🐶 Pre-commit hooks (`.pre-commit-config.yaml`)

```bash
pre-commit install          # установить хуки (однократно)
pre-commit run --all-files  # прогнать на всех файлах
```

| Hook | Источник | Проверяет |
|---|---|---|
| `ruff` | astral-sh/ruff-pre-commit | Lint errors |
| `ruff-format` | astral-sh/ruff-pre-commit | Code formatting |
| `Pyright` | jordemort/action-pyright | Type correctness |
| `go vet (data-service, mcp-gateway)` | классический go vet | Подозрительные конструкции (быстро) |
| `trailing-whitespace` | pre-commit-hooks | Лишние пробелы в конце строк |
| `end-of-file-fixer` | pre-commit-hooks | Пустая строка в конце файла |
| `check-yaml` | pre-commit-hooks | Валидность YAML |
| `check-added-large-files` | pre-commit-hooks | Файлы >500KB в коммите |
| `check-merge-conflict` | pre-commit-hooks | Маркеры merge conflict |

> Pre-commit — быстрый (`go vet`, не `golangci-lint` — он медленный). Полный линтинг только в CI.

### 🔧 Линтеры — настройка и прогон

#### Python (ruff + Pyright)

```bash
# Ruff — быстрый линтер / форматтер
uv run ruff check api-service/src/           # lint
uv run ruff format --check api-service/src/  # check formatting
uv run ruff format api-service/src/          # apply formatting

# Pyright — статическая типизация
npx pyright                                  # проверить всё
```

Конфиг Pyright: `pyrightconfig.json` (excludes: `tests/`, `node_modules/`, `.venv/`, `.data/`, `graphify-out/`).

#### Go (golangci-lint v2)

```bash
# Установка (однократно)
go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest

# Вручную — быстрая проверка одного пакета при изменении
cd data-service && golangci-lint run ./...
cd mcp-gateway && golangci-lint run ./...
```

Оба модуля должны выдавать **0 issues**. Конфиг: `.golangci.yml` (v2, errcheck exclude-functions для стандартных идиом Go).

### 🏁 Makefile — локальная симуляция CI

```bash
make ci         # полный прогон (линт + тесты Python и Go)
make ci-lint-py # только Python линт + typecheck
make ci-test-py # только Python тесты
make ci-lint-go # только Go линтинг (data-service + mcp-gateway)
make ci-test-go # только Go тесты
make ci-audit   # полный security audit (uv audit + govulncheck)
```

**Перед каждым пушем:** `make ci` — занимает ~30–60 сек, ловит ~95% проблем, которые упадут в CI.

### 🐳 act — точная симуляция GitHub Actions

```bash
brew install act           # установка (однократно)
act -j lint-go             # одна джоба в Docker
act --pull=false           # весь пайплайн
```

Требует Docker Desktop. Использует **те же раннеры, те же версии тулов** — 100% совпадение с CI. Полезно, когда `make ci` прошёл, но CI падает на невоспроизводимых отличиях (macOS vs Ubuntu, версии тулов).

### 📦 Версионирование

Все 6 Python-пакетов (`agent-db`, `agent-tutor-sdk`, `api-service`, `demo-web`, `rag`, `pyproject.toml`) и 2 Go-модуля (`data-service`, `mcp-gateway`) синхронизированы на одной версии:
- Текущая: **`1.1.0`**
- Go: `go 1.26.4`

### 🧪 Критерий готовности перед коммитом

1. [ ] `make ci` — зелёный (или его части)
2. [ ] Pre-commit hooks — все Passed
3. [ ] `uv run agent-db e2e-full` — зелёный (если менялась логика data-service / mcp-gateway / orchestrator)
4. [ ] `make ci` — зелёный целиком (не обязательно перед каждым коммитом, но перед пушем — обязательно)

---

## 📊 10. Monitoring & Observability

Каждый сервис отдаёт Prometheus-метрики на `/metrics`:

| Сервис | Порт | Ключевые метрики |
|---|---|---|
| **data-service** | :8084 | `data_requests_total`, `data_request_duration_ms` |
| **mcp-gateway** | :8083 | `mcp_tool_calls_total`, `mcp_sessions_active`, `mcp_rate_limit_hits_total` |
| **admin-dashboard** | :8085 | `admin_requests_total` |
| **api-service** | :8081 | `chat_sessions_total`, `chat_messages_total`, `llm_calls_total`, `llm_duration_ms`, `llm_token_usage`, `llm_cost_total`, `abuse_blocked_total`, `backlog_*` |

### Docker monitoring profile

```bash
docker compose --profile monitoring up -d
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000 (admin/admin)
```

Grafana дашборд предустановлен (12 панелей) — `docker/grafana/dashboards/agent-tutor-overview.json`.

### Logging

- **api-service**: structlog, JSON-логи (`LOG_FORMAT=json`). `LOG_LEVEL` поддерживается.
- **data-service / mcp-gateway / admin-dashboard**: slog, structured JSON. `LOG_LEVEL` поддерживается.

### Anti-Abuse

api-service имеет встроенный anti-abuse engine:

- **TokenBucket**: per-сессия, конфигурируемый RPS/burst (`ABUSE_RPS`, `ABUSE_BURST`).
- **UA block**: curl, wget, python-requests, Go-http-client и др. User-Agent'ы.
- **Message limits**: max length 2000 chars, min interval 1s, session budget 50 messages.
- **Repeated text**: >3 повторов блокируется.
- **Emergency presets**: Normal / Cautious / Lockdown (через admin-dashboard).

### Admin dashboard (v1.1.0 additions)

- **Anti-Abuse tab**: настройки abuse engine для глобал и per-agent.
- **Emergency Big Red Button**: Normal → Cautious → Lockdown одним кликом.
- **i18n**: bilingual RU/EN (309 ключей). Language switcher в хедере.
- `LOG_LEVEL=debug` для детальной трассировки запросов.
