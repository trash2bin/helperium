# Roadmap: выход на pre-prod уровень

Контракт между владельцем и исполнителем. Только решения, инварианты, контрольные точки. Детали реализации - в отдельных задачах.

## Текущее состояние → Цель

**Сейчас**: рабочий MVP. SQLite + ChromaDB + FastMCP-сервер с тулами + LiteLLM-агент в Starlette + статический web-чат. Тестов нет, контейнеров нет, CI/CD нет.

**Цель**: 4 long-running контейнера (`mcp`, `rag`, `api`, `web`) + 3 профильных (`agent` для генерации, `backup` для бэкапов, `caddy` для prod). `docker compose up` поднимает всё. Тесты ≥40% на Этапе 1, далее по мере рефакторингов. CI/CD на GitHub Actions. LLM-провайдер (Ollama / Mistral / OpenAI) - внешняя зависимость через URL, своего контейнера `ollama` нет.

## Глоссарий

- **Инвариант** - требование, которое нельзя нарушать без согласования.
- **Сервис** - long-running Docker-контейнер в compose.
- **One-shot сервис** - контейнер с профилем, запускается через `docker compose run --rm`, после exit не висит.
- **CLI-команда** - процесс в образе `mcp` (или отдельном `agent`), запускается вручную.

## Инварианты

- **I1**: каждый этап оставляет проект в рабочем состоянии. После каждого этапа `uv run mcp dev server.py` (stdio) и UI-чат работают.
- **I2**: пользовательские данные (`.data/`, `backlog/`, `chroma_db/`, `generated_materials/`) не теряются при перезапуске и не коммитятся.
- **I3**: не ломать существующие публичные API без явного согласования.
- **I4**: `api`-сервис работает в **1 воркер uvicorn**. Сессионные локи in-process, между воркерами не синхронизируются. Горизонтальное масштабирование - за рамками pre-prod.
- **I5**: отдельный Docker-образ на каждый сервис (Dockerfile в корне каждого пакета: `rag/Dockerfile`, `mcp_server/Dockerfile`, `demo/api/Dockerfile`, `demo/web/Dockerfile`) + общий образ для CLI-утилит (`tools/Dockerfile`). Все образы — multi-stage на `python:3.12-slim`.
- **I6**: `university.db` - фикстура для демо, **не** production-БД. На реальном проде источник данных - внешняя БД вуза; подключение к ней - отдельная задача, **не в этом roadmap**.
- **I7**: рефакторинг - после тестов. Тесты - на стабильную версию кода. Контейнеризация - на протестированный код.

## Принципы

- **Минимальная достаточность**: Kafka, K8s, PostgreSQL, Redis, gRPC, Prometheus - не используем. SQLite + FastAPI + compose + один VPS достаточно.
- **Тесты - на стабильное**: не пишем тесты на каждый цикл оркестратора (4 сценария достаточно), не тестируем обёртки над внешними библиотеками (chonkie, sentence-transformers).
- **Долгое - в CLI**: импорт PDF и генерация материалов - one-shot, не в long-running сервисах.
- **Токены - не в браузер**: `API_BEARER_TOKEN` хранится в `web`-сервисе, добавляется при reverse-proxy.

## Что НЕ делаем

- Свой контейнер `ollama` - внешний провайдер через URL.
- Миграции БД - `db/schema.py` остаётся идемпотентным (`CREATE TABLE IF NOT EXISTS`). На проде SQLite исчезнет или станет write-through кэшем.
- Полноценная авторизация пользователей (логин/пароль, OAuth) - достаточно Bearer-токена.
- PydanticAI - задача в `doc/TASK.md`, это смена архитектуры агента. Не в этом roadmap.
- In-memory тесты FastMCP - капризен между версиями SDK. Используем subprocess + HTTP.

---

## Целевая архитектура

### Сетевая топология

MCP обзается с базой данных (статичная sqlite сейчас просто формируется из `fixtures.json`)
Также MCP общается с RAG;
RAG хранит документы в ChromaDB и также их туда индексирует и тд;
CORE: API там агент который подключается к MCP для tools а также к провайдеру (ollama, openai, etc.);
DEMO: WEB - это просто фронт он общается только с API и также передает сессии (по сути создает но не хранит только если экзмепляр в свое js), отдает статическую страничку и умеет общаться с API по SSE чтобы передовать сообщения от агента;


### Сервисы

| Сервис | Режим | Стек | Порт (compose / host) | Volumes |
|---|---|---|---|---|
| `mcp` | long-running | FastMCP | 8083 / `127.0.0.1:8083` (dev) | `app_data:/data/app:ro` |
| `rag` | long-running | FastAPI | 8082 / `127.0.0.1:8082` (dev) | `rag_data:/data/rag`, `hf_cache:/home/app/.cache/huggingface` |
| `api` | long-running, 1 worker | FastAPI + uvicorn | 8081 / `127.0.0.1:8081` (dev) | `app_data:/data/app`, `backups:/data/backups` |
| `web` | long-running | FastAPI (статика + proxy) | 8080 / `127.0.0.1:8080` (dev) | - |

### Тома (в `./.data/`)

| Том | Контейнер-путь | Содержимое |
|---|---|---|
| `app_data` | `/data/app` | `university.db`, `demo_sessions.sqlite`, `backlog/` |
| `rag_data` | `/data/rag` | `chroma_db/` |
| `hf_cache` | `/home/app/.cache/huggingface` | embedding-модели и кеш transformers |
| `backups` | `/data/backups` | `*.db.gz` снапшоты |

### Ключевые env-переменные

`RAG_SERVICE_URL=http://rag:8082`, `MCP_SERVICE_URL=http://mcp:8083/mcp`, `OLLAMA_URL=http://host.docker.internal:11434`, `MISTRAL_API_KEY`, `API_BEARER_TOKEN` (обязателен в prod), `WEB_ORIGIN`, `MCP_TRANSPORT=http` (stdio только для `uv run mcp dev`), `ENABLE_THINK` (false в prod, true в dev). Полный список - в `.env.example` (Этап 2).

---

## Этап 0. Подготовка и разбиение миграции

**Цель**: не делать большой рефакторинг одним коммитом. Сначала отделяем сервисы по границам ответственности, затем переводим клиентов на HTTP, затем мигрируем web/API-прокси и только после этого выносим CLI-утилиты.

**Правило этапа**: каждый подпункт - отдельный безопасный срез. Перед переходом к следующему подпункту обязательно выполняются локальные smoke/integration-проверки. Если проверка не проходит, следующий подпункт не начинаем.

**Порядок**: 0.0 → 0.1 → 0.2 → 0.3 → 0.4 → 0.5. Никаких параллельных миграций внутри этапа.

### 0.0. Базовая фиксация состояния ✅

До любых изменений зафиксировать текущую работоспособность:

- `uv run mcp dev server.py` (stdio) - все тулы отвечают.
- UI-чат отвечает на запрос с хотя бы одним tool call.
- `sqlite`/`chroma` данные читаются из текущего состояния.
- Зафиксировать текущие команды запуска в README/AGENTS.md, чтобы после миграции можно было сравнить поведение.

Это не отдельная фича, а контрольная точка для отката и диагностики.

> **✅ Выполнено**: Контрольная точка зафиксирована. Все тулы работают через stdio MCP.

### 0.1. Выделить `rag` как отдельный HTTP-сервис ✅

Оставляем API контракт прежним, но переносим реализацию в `rag/service.py`.

**Что меняется**:

- Создан Starlette-сервис `rag` в `rag/service.py` (FastAPI будет добавлен на Этапе 2).
- Вынесен HTTP-контракт (`/health`, `/search`, `/context`, `/documents/list`, `/documents/import`, `/documents/delete`) в `rag/http_models.py` и `rag/service.py`.
- Создан `rag/client.py` для вызовов из `mcp`.
- HTTP DTO модели вынесены в отдельный модуль `rag/http_models.py`.

**Проверка подпункта**:

- `uv run python -m rag.service` поднимается.
- `curl :8082/health` возвращает 200.
- `curl :8082/search` и `/documents/list` работают на тестовых данных.
- Smoke-тесты на `rag.client` проходят.

> **✅ Выполнено**: RAG-сервис выделен как отдельный HTTP-сервис на Starlette. HTTP-контракт реализован в `rag/http_models.py`, сервис в `rag/service.py`, клиент в `rag/client.py`.
> **⚠️ Примечание**: `tools/rag.py` не был удалён, а оставлен как **минималистичная заглушка для обратной совместимости** с `fixtures/document_generator.py`. Теперь использует `RagClient` из `rag/client.py`.

### 0.2. Перевести `mcp` на HTTP-клиент к `rag` ✅

Смысл подпункта - убрать прямую связность `mcp` с реализацией RAG, оставить только клиент.

**Что меняется**:

- `server.py` перестал импортировать внутренности `rag`-пайплайна.
- `mcp` вызывает только `RagClient(RAG_SERVICE_URL)` из `rag/client.py`.
- MCP-тулы `list_documents`, `search_documents`, `get_rag_context` становятся thin wrappers над HTTP-клиентом.
- Зависимость на `RAG_SERVICE_URL` добавлена в env-переменные.

**Проверка подпункта**:

- `uv run mcp dev server.py` продолжает работать.
- HTTP-вызовы к `rag` проходят из `mcp`.
- Каждый tool вызывается в интеграционном smoke-тесте через subprocess + HTTP.
- На этом шаге не трогали API/web.

> **✅ Выполнено**: MCP-сервер полностью переведён на HTTP-клиент к RAG-сервису. Прямая связность удалена, все вызовы идут через `RagClient`.

### 0.3. Перевести `api` на HTTP-клиент к `mcp` и на FastAPI ✅

На этом подпункте меняется только транспорт и фреймворк, но не бизнес-логика чата.

**Что меняется**:

- `mcp_server/` - вынесена директория для MCP-сервера (ранее `server.py` в корне)
- `demo/api/agent/mcp_client.py` переключается с `stdio_client` на `mcp.client.streamable_http.streamablehttp_client`.
- `demo/api/server.py` переводится со Starlette на FastAPI.
- SSE-стриминг сохраняется без изменения контракта.
- Публичные эндпоинты `/health`, `/api/data`, `/api/chat`, `/api/backlog`, `/api/backlog/{session_id}`, `/api/session/history` остаются прежними.
- Добавлена настройка `mcp_service_url` в `demo/settings.py` (дефолт: `http://127.0.0.1:8083/mcp`).

**Проверка подпункта**:

- `python -m mcp_server.server` поднимается (HTTP, порт 8083, mount_path `/mcp`)
- `python -m demo.api.server` стартует.
- `curl :8081/health` отвечает.
- Один полный чат-цикл через SSE проходит с HTTP-MCP.
- Отдельный тест проверяет, что `call_tool` в MCPClient работает через HTTP.

> **✅ Выполнено**: API сервис переведен на FastAPI и HTTP-клиент к MCP. SSE-стриминг сохранен, бизнес-логика чата не изменена.

### 0.4. Перевести `web` на FastAPI reverse-proxy и SSE-прокси ✅

После этого `web` становится тонким демо-фронтом без утечки токена в браузер.

**Что меняется**:

- `demo/web/server.py` переводится на FastAPI.
- `/` отдаёт HTML.
- `/static/*` обслуживается как статика.
- `/api/{path:path}` проксируется на `api:8081`.
- `Authorization: Bearer $API_BEARER_TOKEN` добавляется только на стороне `web`.
- SSE-проксирование остаётся потоковым через `httpx.AsyncClient.stream()`.
- CORS ограничен через `WEB_ORIGIN` (дефолт `*` для dev).
- HTTP-клиент для прокси создаётся в `lifespan` и хранится в `app.state`.
- Все env-переменные (`API_BEARER_TOKEN`, `WEB_ORIGIN`) вынесены в `demo/settings.py`.

**Проверка подпункта**:

- `python -m demo.web.server` стартует.
- `curl :8080/api/health` отвечает 200.
- В браузерный клиент токен не утекает.
- Чат через `web` работает end-to-end.

> **✅ Выполнено**: Web-сервис переведён на FastAPI с полным reverse-proxy функционалом. Bearer-токен добавляется на стороне web, не утекает в браузер. HTTP-клиент управляется через lifespan, CORS контролируется через env.

### 0.5. Выделить CLI-утилиты `agent-ingest` и `agent-generate`

Это финальный подпункт этапа: всё тяжёлое уходит в one-shot команды и не живёт в long-running сервисах.

**Что меняется**:

- `agent-ingest import/list/search/delete` работает только через HTTP к `rag`.
- `agent-generate` выносится в отдельный entrypoint и запускается через профиль `tools`.
- Генерация материалов остаётся отдельной задачей от поиска и чата.
- В `mcp` не остаются зависимости на генерацию документов и Ollama-клиент.

**Проверка подпункта**:

- `uv run agent-ingest list` возвращает документы.
- `uv run agent-generate ...` создаёт артефакты и импортирует их в `rag`.
- `mcp`, `api`, `web`, `rag` продолжают подниматься независимо друг от друга.

> **✅ Выполнено**: CLI-утилиты `agent-ingest` и `agent-generate` выделены как one-shot команды. `agent-ingest` использует `RagClient` для работы с RAG-сервисом через HTTP. `agent-generate` работает через отдельный entrypoint. MCP-сервер не имеет прямых зависимостей на генерацию документов - использует только HTTP-клиент к RAG.

### 0.6. Критерии готовности этапа 0 (✅)

- Все подпункты 0.0-0.4 пройдены по отдельности.
- После каждого подпункта проект остаётся рабочим.
- `uv run mcp dev server.py` (stdio) и UI-чат работают на промежуточных состояниях.
- `uv run python -m rag.service` поднимается; `curl :8082/health` → 200.
- `uv run python -m demo.api.server` стартует с `MCP_SERVICE_URL` и `RAG_SERVICE_URL`.
- `uv run python -m demo.web.server` стартует; `curl :8080/api/health` → 200.
- `tools/rag.py` **не удалён** - оставлен как минималистичная заглушка для обратной совместимости с `fixtures/document_generator.py`, использует `RagClient` из `rag/client.py`.
- Перед переходом к Этапу 1 есть один общий smoke-прогон всего контура: `rag → mcp → api → web`.
**Выполнено на этапе 0.5***


## Этап 1. Тестовая инфраструктура и стандартизация API ✅

**Цель**: обеспечить надежность системы через комплексное тестирование и внедрение строгого API-контракта. **Покрытие ≥ 40%** на данном этапе.

**Результат**: покрытие **84%** (выше 109 тестов), ruff - чисто, OpenAPI - у всех сервисов.

### 1.1. Зависимости ✅
Добавлены в `pyproject.toml`: `pytest>=8`, `pytest-asyncio>=0.24`, `pytest-cov>=5`, `pytest-mock>=3.14`, `respx>=0.21`, `freezegun>=1.5`, `httpx>=0.28`, `ruff>=0.7`.

### 1.2. Структура `tests/` ✅
```
tests/
├── conftest.py                    # temp_dir, db_path, test_db, mock_embedding, rag_config
├── unit/
│   ├── rag/                       # config, parser, chunker, vector_store, repository, pipeline, service, client
│   ├── db/                        # database (все методы + edge-кейсы)
│   ├── tools/                     # student, teacher, discipline, grade - 1+ позитивных, 1+ негативных
│   └── demo/
│       ├── test_backlog.py        # 13 тестов (все event-методы, чтение, изоляция)
│       ├── test_sessions.py       # 12 тестов (CRUD, trim, truncation, concurrency)
│       └── agent/                 # tool_parser, llm_client, mcp_client, orchestrator
├── integration/
│   ├── rag/                       # test_e2e_pipeline.py - 8 тестов (import → search → context → delete)
│   ├── mcp/                       # (пусто - отложено на Этап 2)
│   └── api/                       # (пусто - отложено на Этап 2)
└── fixtures/                      # paragraphs.txt в репозитории; PDF/DOCX генерируются в conftest
```

> **Примечание**: `integration/mcp/` и `integration/api/` оставлены как директории-заглушки.
> Тесты в них появятся на Этапе 2, когда Docker compose даст стабильные HTTP-границы сервисов.

### 1.3. Стандартизация API (OpenAPI/Swagger) ✅
Все FastAPI-сервисы (`rag`, `api`, `web`) имеют декларативный контракт:
- Pydantic `BaseModel` для Request/Response
- `response_model`, `summary`, `description` на каждом эндпоинте
- Работающие `/docs` (Swagger UI) и `/openapi.json`

### 1.4. Выполненные кейсы ✅

| Компонент | Статус | Детали |
|---|---|---|
| **RAG** - config / parser / chunker / vector_store / repository / pipeline / service / client | ✅ | pipeline 92%, service ~75%, всё остальное ~80%+. `client` через `respx`. E2E с реальной ChromaDB + мок embedding |
| **DB** - database (все методы + edge-кейсы) | ✅ | Все 12 методов, context manager, ping |
| **Tools** - student, teacher, discipline, grade | ✅ | По ≥1 позитивному + ≥1 негативному сценарию на каждый инструмент |
| **Core API** - sessions, backlog, tool_parser, orchestrator, mcp_client | ✅ | sessions 78%, backlog 77%, orchestrator 54% (4 сценария), mcp_client happy/error, tool_parser (native + JSON) |

### 1.5. Что сделано и что отложено

**Реализовано в Этапе 1**:
- RAG: все unit + e2e-тест с реальной ChromaDB
- DB: все методы + edge-кейсы
- Tools: позитивные + негативные сценарии
- Core API: sessions (round-trip, trim, concurrency), backlog (все события, чтение, изоляция), tool_parser (native + JSON), mcp_client (HTTP через stdio), orchestrator (4 сценария)

**Отложено на Этап 2 (post-Docker)**:
- **MCP integration** (`integration/mcp/`): проверка каждого тула через HTTP к поднятому в compose контейнеру - вместо хрупкого subprocess-теста будет `httpx` к `http://mcp:8083/mcp`
- **API SSE** (`integration/api/`): сценарии чата через реальный SSE к `api`-контейнеру с моком LLM-провайдера
- **CLI smoke** (`agent-ingest --help`, subcommands): argparse-тест - простой, но не было времени; можно сделать в любой момент

**Обоснование откладывания** (согласовано с владельцем):
1. MCP subprocess + JSON-RPC - тест на 8/10 сложности, требует поднятых RAG + DB
2. API SSE - требует мока LiteLLM и живого MCP-сервера
3. После Docker: `docker compose up` даёт стабильные HTTP-адреса (`http://mcp:8083`, `http://rag:8082`), и тесты превращаются в простые `httpx`-вызовы
4. Инвариант I7 не нарушен: Docker идёт на протестированном коде (84%), оставшиеся интеграционные тесты пишутся уже под контейнеры

### 1.6. Критерии готовности этапа 1 ✅

| Критерий | Статус |
|---|---|
| `uv run pytest` - все тесты проходят | ✅ 109 passed, 0 failed (2.75s) |
| `uv run pytest --cov --cov-fail-under=40` | ✅ 84% (цель ≥40%) |
| `uv run ruff check .` - без ошибок | ✅ чисто |
| `uv run ruff format --check .` - без ошибок | ✅ чисто |
| OpenAPI спецификации (`rag`, `api`, `web`) | ✅ `/docs` и `/openapi.json` доступны |

> **Этап 1 закрыт 21.06.2026.** Покрытие 84% при цели 40%. Оставшиеся integration-кейсы (MCP, API SSE, CLI)
> перенесены на Этап 2 - они естественно ложатся на Docker compose как HTTP-тесты к контейнерам.

---

## Этап 2. Контейнеризация ✅

**Цель**: всё запускается через `docker compose up` в dev-режиме.

### 2.1. Dockerfile'ы ✅

Файлы: `rag/Dockerfile`, `mcp_server/Dockerfile`, `demo/api/Dockerfile`, `demo/web/Dockerfile` + `tools/Dockerfile`

Multi-stage на `python:3.12-slim`. Каждый сервис — свой образ (I5):

| Dockerfile | Образ | CMD |
|---|---|---|
| `rag/Dockerfile` | `agent-tutor-rag` | `python -m rag.service` |
| `mcp_server/Dockerfile` | `agent-tutor-mcp` | `python -m mcp_server.server` |
| `demo/api/Dockerfile` | `agent-tutor-api` | `python -m demo.api.server` |
| `demo/web/Dockerfile` | `agent-tutor-web` | `python -m demo.web.server` |
| `tools/Dockerfile` | `agent-tutor-agent` | (задаётся в compose) |

Все builder'ы: `python:3.12-slim` → установка `uv` → `uv sync --frozen --no-dev`. Runtime: `python:3.12-slim` → `curl` для healthcheck → копирование `/app` из builder. 

**Без прогрева embedding в образе.** Кеш HuggingFace монтируется через volume `hf_cache`. При первом запуске `rag` модель докачивается один раз в volume, далее берётся из кеша.

### 2.2. `docker-compose.yml` ✅

Файл: `docker-compose.yml`

| Сервис | `command` | Порты (dev) | Volumes | Зависит от |
|---|---|---|---|---|
| `rag` | `python -m rag.service` | `127.0.0.1:8082:8082` | `rag_data`, `hf_cache` | - |
| `mcp` | `python -m mcp_server.server` | `127.0.0.1:8083:8083` | `app_data:/data/app:ro` | `rag` (healthy) |
| `api` | `python -m demo.api.server` | `127.0.0.1:8081:8081` | `app_data` | `mcp` (healthy) |
| `web` | `python -m demo.web.server` | `127.0.0.1:8080:8080` | - | `api` (healthy) |
| `agent` (profile `tools`) | one-shot через `compose run --rm` | - | `app_data` | `rag` (healthy) |
| `backup` (profile `cron`) | `python /app/scripts/backup.py` | - | `app_data:ro`, `backups` | - |
| `caddy` (profile `prod`) | caddy:2, Caddyfile с авто-TLS | `80:80`, `443:443` | `caddy_data`, `caddy_config` | `web` (healthy) |

Одна bridge-сеть `agent-tutor-net`. Healthchecks:

- `rag`: `curl -f :8082/health`, `start_period=120s` (cold start с загрузкой embedding ~470 МБ), `retries=10`.
- `mcp`: `curl -f :8083/health`, `start_period=15s`. (Добавлен HTTP `/health` endpoint через Starlette-роутинг.)
- `api`: `curl -f :8081/health`, `start_period=15s`.
- `web`: `curl -f :8080/`, `start_period=10s`.

### 2.3. Prod-профиль ✅

Файл: `Caddyfile`

Prod-режим - `docker compose --profile prod up -d`. Поднимается `caddy:2` с авто-TLS через Let's Encrypt. Long-running сервисы с `restart: unless-stopped` через YAML-якорь.

### 2.4. `.env.example` ✅

Файл: `.env.example`

Полный список env-переменных с дефолтами, разбитый по сервисам (см. файл). Основные группы: LLM-провайдер (Ollama/Mistral), RAG, агент, безопасность, генерация, бэкапы.

### 2.5. `.dockerignore` ✅

Исключает: `.venv/`, `.git/`, `__pycache__/`, `.data/`, `backlog/`, `chroma_db/`, `generated_materials/`, `*.db`, `*.sqlite*`, `.env*`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `.coverage`, `tests/`, `*.md`.

### 2.6. Дополнительные изменения ✅

- **MCP сервер**: добавлен HTTP `/health` endpoint (Starlette routing поверх FastMCP streamable-http). Позволяет docker healthcheck проверять не только живучесть процесса, но и статус зависимостей (БД + RAG).
- **Backup-сервис** (profile `cron`): файл `scripts/backup.py`. Python-скрипт с sqlite3.backup() для консистентных снапшотов. Цикл: бэкап каждые 6 часов → сжатие .db.gz → удаление старше 14 дней.
- **`build` в каждом сервисе**: `build: context: ., dockerfile: Dockerfile`. Позволяет `docker compose build` или `docker compose up` без предварительной сборки.

### 2.6. Критерии готовности этапа 2

> **⚠️ Файлы созданы, но этап требует проверки на реальном Docker.**

- `docker compose build` собирает образ без ошибок.
- `docker compose up -d` поднимает 4 long-running сервиса.
- `docker compose ps` - все 4 в `healthy` в течение 180 секунд.
- `curl :8081/health` → `{"api":"ok","ollama":{...}}`.
- `curl :8080/api/health` → 200.
- Web UI чат работает end-to-end.
- `docker compose --profile tools run --rm agent agent-ingest list` работает.
- `docker compose --profile cron up -d` запускает `backup` (через Python-цикл).
- `docker compose restart` не теряет данные в `./.data/`.
- `docker compose down -v` удаляет тома, не ломает хостовые файлы.

---

## Этап 2.6. Инкапсуляция сервисов: SDK и контракты

> **Статус**: НОВЫЙ этап. Добавлен постфактум после контейнеризации (Этап 2), когда
> при анализе архитектуры для CI/CD была обнаружена скрытая связанность сервисов
> через Python-импорты — несмотря на то, что HTTP-границы уже работали.

**Проблема**: сервисы формально разнесены по контейнерам и общаются по HTTP,
но на уровне Python-кода существуют прямые сквозные импорты, которые:

1. **Создают неявный граф зависимостей** между репозиториями сервисов:
   - `rag/client.py` (HTTP-клиент к RAG) лежит внутри `rag/`, но сам `rag` его не использует.
     Его потребители — `mcp_server` и `fixtures/ingest`. При переписывании rag на Go
     надо помнить, что в этой папке лежит ещё и клиент для других сервисов.
   - `rag/models.py` (Document, RagContext, RagSearchResult) импортируется `mcp_server/server.py`
     и `db/models.py` (реэкспорт для совместимости). Модели — Python-типы, но их семантика
     должна быть зеркалом HTTP-контракта.
   - `db/` (database.py, connector.py, models.py, schema.py) — импортируется четырьмя
     разными сервисами (rag, mcp_server, api, tools). При переписывании любого из них
     надо воспроизводить ту же SQL-логику. По сути — shared library, которая не оформлена
     как таковая.
   - `mcp_server/tools/rag.py` — остаток от рефакторинга Этапа 0, используется только
     `fixtures/document_generator.py`.

2. **Не дают взять сервис и переписать с нуля** без анализа смежных import'ов.
   В теории можно, но на практике приходится открывать чужой код и разбираться,
   какие типы откуда тянутся.

3. **Маскируют настоящий контракт**: то, что сервисы реально обмениваются HTTP-запросами
   (POST /search, POST /context и т.д.), не зафиксировано в явном виде.
   OpenAPI спецификация есть на каждом эндпоинте (`/docs`), но не хранится в репозитории
   как артефакт и не версионируется.

**Корень проблемы**: в MVP было удобно держать общие типы рядом — один `pyproject.toml`,
  одна папка `rag/` со всем подряд. При расщеплении на микросервисы эта общность
  превратилась в связанность.

### Цель этапа

Чёткое разделение:

- **SDK** (`agent-tutor-sdk/`) — Python-пакет для dev-удобства. Содержит `rag/client.py`,
  `rag/models.py`, `db/` (database + connector + schema + models). Используется Python-
  сервисами (mcp_server, fixtures) и local dev.
- **Сами сервисы** — self-contained пакеты с собственными `pyproject.toml`, зависящие
  только от SDK. Не импортируют ничего из соседних сервисов напрямую.
- **Контракты** (`specs/`) — OpenAPI-спецификации всех HTTP-сервисов, сохранённые
  в репозитории как source of truth. Изменение API начинается с правки spec,
  затем реализация. CI проверяет соответствие spec ↔ код.
  FastAPI дополнительно генерирует актуальную спецификацию на `/openapi.json` и `/docs`.

### Почему не protobuf / gRPC / Smithy

На данном этапе (pre-prod, < 10 HTTP-эндпоинтов) OpenAPI достаточно:
- FastAPI генерирует его автоматически — синхронизация spec ↔ код бесплатна.
- `oapi-codegen`, `openapi-generator` есть под все популярные языки.
- gRPC + protobuf дадут выигрыш только при тысячах RPS или bidirectional streaming
  (который уже есть через MCP-over-HTTP, а не gRPC).
- Smithy (AWS) — сверхгибкая IDL, но требует Java-тулчейна и редкого знания.

OpenAPI — «минимальная достаточность» в чистом виде.

### План перехода и статус выполнения

#### 2.6.1. Создать `specs/` с OpenAPI-спецификациями ✅

```
specs/
├── rag.openapi.yaml          # экспорт из rag/service.py
├── api.openapi.yaml          # экспорт из demo/api/server.py
└── README.md                 # как обновлять spec, как генерировать клиент
```

Spec — **первичен**. Изменение API начинается с правки spec, затем реализация.
CI (или тесты) проверяют:
```bash
diff <(curl -s http://rag:8082/openapi.json) <(yq -o json specs/rag.openapi.yaml)
```
Если не совпало — тест падает.

**Что сделано**:
- `specs/` создана с OpenAPI-спецификациями для rag и api сервисов (YAML).
- Тесты `rag/tests/unit/test_openapi_spec.py` и `demo/api/tests/unit/test_openapi_spec.py`
  проверяют, что код соответствует spec.
- `specs/README.md` описывает как обновлять spec и генерировать клиент.

#### 2.6.2. Создать `agent-tutor-sdk/` как uv workspace member ✅

```
agent-tutor-sdk/
├── pyproject.toml
├── src/agent_tutor_sdk/
│   ├── db/                     # connector.py, database.py, models.py, schema.py, fixtures.py
│   └── rag/                    # client.py, models.py
└── tests/
    ├── test_client.py          # (из rag/tests/unit/test_client.py)
    └── test_database.py        # (из db/tests/unit/test_database.py)
```

**Что сделано**:
- SDK создан, собирается `uv build`, используется всеми сервисами.
- `rag/client.py` удалён из `rag/` — только в SDK.
- `rag/models.py` удалён из `rag/` — публичные модели в SDK,
  внутренние TypedDict'ы (`PageDict`, `ChunkDict`) — в `rag/_types.py`.
- Тесты SDK переехали: `rag/tests/unit/test_client.py` → `agent-tutor-sdk/tests/unit/test_client.py`.
  `db/tests/` → `agent-tutor-sdk/tests/unit/test_database.py`.

#### 2.6.3. Перевести сервисы на uv workspace ✅

```toml
# pyproject.toml (корень)
[tool.uv.workspace]
members = [
    "agent-tutor-sdk",
    "rag",
    "mcp_server",
    "demo/api",
    "demo/web",
    "fixtures",                # вместо tools/
]
```

Каждый сервис — отдельный `pyproject.toml` с собственными зависимостями:

| Пакет | pyproject.toml | Зависит от SDK |
|---|---|---|
| `agent-tutor-sdk` | `agent-tutor-sdk/pyproject.toml` | — |
| `rag` | `rag/pyproject.toml` | ✅ да |
| `mcp_server` | `mcp_server/pyproject.toml` | ✅ да |
| `demo-api` | `demo/api/pyproject.toml` | ✅ да |
| `demo-web` | `demo/web/pyproject.toml` | ❌ нет (только fastapi + httpx + pydantic) |
| `fixtures` | `fixtures/pyproject.toml` | ✅ да |

#### 2.6.4. Убрать перекрёстные импорты между сервисами ✅

| Было | Стало |
|---|---|
| `rag/client.py` внутри `rag/` | удалён из `rag/`, только в `agent-tutor-sdk` |
| `rag/models.py` в `rag/` | публичные модели в SDK, TypedDict'ы в `rag/_types.py` |
| `db/` в корне проекта | весь `db/` удалён из корня, всё в `agent-tutor-sdk` |
| `mcp_server/tools/rag.py` | удалён, `RagTools` перенесён в `fixtures/rag_tools.py` |
| `tools/Dockerfile` + `tools/pyproject.toml` | удалены (заменены на `fixtures/`) |
| `fixtures` зависел от `mcp_server` | убрана зависимость, `fixtures` импортирует только SDK |

Прямые импорты `from rag` или `from db` между сервисами отсутствуют.
Остатки: 2 теста были переведены с `db.schema` на `agent_tutor_sdk.db.schema`.

#### 2.6.5. Обновить Dockerfile'ы под workspace ✅

Все Dockerfile'ы (`rag/Dockerfile`, `mcp_server/Dockerfile`, `demo/api/Dockerfile`,
`demo/web/Dockerfile`) собираются из корня монорепозитория, runtime-слой содержит
только нужные пакеты (сервис + SDK при необходимости).

#### 2.6.6. Перенести тесты под SDK ✅

- `db/tests/unit/test_database.py` → `agent-tutor-sdk/tests/unit/test_database.py`
- `rag/tests/unit/test_client.py` → `agent-tutor-sdk/tests/unit/test_client.py`

### Что изменилось

| Компонент | Было | Стало (после этапа) |
|---|---|---|
| `rag/client.py` | внутри `rag/`, потребители импортируют | удалён из `rag/`, только в SDK |
| `rag/models.py` | внутри `rag/`, общие для всех | публичные модели в SDK, TypedDict'ы в `rag/_types.py` |
| `db/` (весь) | в корне проекта | удалён из корня, всё в SDK |
| `mcp_server/tools/rag.py` | в mcp_server, нужен fixtures | удалён из mcp_server, перенесён в `fixtures/rag_tools.py` |
| `tools/` | Dockerfile + pyproject.toml | удалён |
| `specs/` | нет | OpenAPI-спецификации в репозитории, source of truth |
| `pyproject.toml` | один на всё | workspace + по одному на сервис |
| Контракт | Python-типы (import) | SDK + HTTP / OpenAPI |

### Критерии готовности этапа 2.6

- [x] `agent-tutor-sdk/` — отдельный пакет с `pyproject.toml`, собирается `uv build`
- [x] `mcp_server`, `demo/api`, `fixtures` зависят от `agent-tutor-sdk`, не импортируют `rag/` и `db/` напрямую
- [x] `demo/web` **не** зависит от SDK (только fastapi + httpx + pydantic)
- [x] `rag/client.py` больше не лежит в `rag/` — только в SDK
- [x] `rag/models.py` почищен: публичные модели в SDK, внутренние типы в `rag/_types.py`
- [x] `mcp_server/tools/rag.py` удалён
- [x] `db/` в корне проекта удалён (весь — в SDK)
- [x] `tools/` удалён (Dockerfile + pyproject.toml)
- [x] `specs/` содержит OpenAPI-спецификации для rag и api сервисов (YAML)
- [x] Тесты `test_openapi_spec.py` проверяют соответствие кода спецификациям
- [x] `uv run pytest` из корня — 109 тестов, все зелёные
- [x] `uv run pytest rag/tests/` — только RAG-тесты
- [x] `uv run pytest mcp_server/tests/` — только MCP-тесты
- [ ] `docker compose build` собирает все образы
- [ ] `docker compose up -d` поднимает все сервисы, healthchecks зелёные

---

## Этап 2.5. Минимальный мониторинг и smoke-проверки

**Цель**: до CI и до прод-обвязки добавить минимальную наблюдаемость, чтобы быстро локализовать поломку по сервисам, не заводя полноценный стек мониторинга.

**Что делаем**:

- У каждого long-running сервиса есть обязательный `GET /health`.
- `healthcheck` в `docker compose` становится не формальностью, а реальным gate для `mcp`, `rag`, `api`, `web`.
- Добавить один повторяемый smoke-проход для всей локальной связки: health → один tool call → один chat turn → SSE.
- Зафиксировать единый способ диагностики: `docker compose ps`, `docker compose logs`, `curl /health`, smoke-скрипт.
- Логи пока остаются stdout-only; Prometheus/Grafana/Alertmanager сюда не тащим.

**Не делаем**:

- Не строим полноценную observability-платформу.
- Не вводим метрики/трейсы как обязательное условие этого этапа.
- Не меняем бизнес-логику сервисов ради мониторинга.

**Критерии готовности этапа 2.5**:

- `docker compose ps` показывает понятные health-состояния.
- Smoke-скрипт падает при недоступности любого сервиса.
- По набору health-checks можно локализовать проблему без чтения кода.
- Этот этап закрыт до начала CI-автоматизации.

## Этап 3. CI (GitHub Actions)

**Цель**: каждый push и PR в `main` проверяется линтером, тестами и сборкой образов.

### 3.1. `.github/workflows/ci.yml`

Триггеры: `push` в `main`, `pull_request` в `main`. Jobs:

- **`lint`**: `actions/checkout@v4`, `astral-sh/setup-uv@v3`, `uv tool install ruff`, `ruff check .`, `ruff format --check .`.
- **`test`**: `actions/checkout@v4`, `astral-sh/setup-uv@v3`, `uv sync --frozen --all-groups`, `uv run pytest -m "unit or integration" --cov --cov-fail-under=40 -v`. Артефакт `coverage.xml` через `actions/upload-artifact@v4`. Кеш `~/.cache/uv` через `actions/cache@v4` с ключом по `uv.lock`.
- **`build`**: после успешного `test`. `docker/setup-buildx-action@v3`, `docker build . --tag agent-tutor:test`. Без push, только smoke. Кеш Docker layers через `docker/build-push-action@v5` с `cache-from: type=gha`.

E2E тесты (`tests/e2e/`) - отдельный workflow, запускается через `workflow_dispatch` или nightly, **не** на каждый push.

### 3.2. Бейджи в `README.md`

```
![CI](https://github.com/<owner>/agent-tutor/workflows/ci/badge.svg)
![Coverage](https://img.shields.io/codecov/c/github/<owner>/agent-tutor)
```

### 3.3. Критерии готовности этапа 3

- Push в `main` запускает CI.
- Все три job зелёные.
- Бейджи показывают зелёный статус.

---

## Этап 4. Production-готовность

**Цель**: один VPS не падает от кривого промпта, не теряет данные, наблюдаем. **Не включает подключение к реальной БД вуза** - это отдельная задача с другой архитектурой (см. I6).

### 4.1. Аутентификация

Схема: `web`-сервис хранит `API_BEARER_TOKEN` из env и добавляет `Authorization: Bearer ...` при reverse-proxy `/api/*` (см. 0.4.1). Браузер токен не видит.

- Middleware в `demo/api/server.py` (FastAPI) проверяет `Authorization: Bearer <API_BEARER_TOKEN>`. Если токен не задан в prod - `api` не стартует (проверка в `main()` при создании `app`).
- `web`-сервис: если `API_BEARER_TOKEN` не задан и профиль `prod` - не стартует.
- CORS в `api`: `allow_origins` ограничивается `WEB_ORIGIN` (не `*`). В prod `api` не торчит наружу напрямую - трафик идёт через Caddy.

### 4.2. HTTPS через Caddy

Сервис `caddy` (профиль `prod`), образ `caddy:2`. `Caddyfile` автоматически получает TLS через Let's Encrypt для домена из env `DOMAIN`. Проксирует:
- `https://$DOMAIN/` → `http://web:8080/`
- `https://$DOMAIN/api/*` → `http://web:8080/api/*` (далее через web-прокси к api)

### 4.3. Структурированное логирование

Добавить `structlog`. JSON-формат во всех сервисах: `timestamp`, `level`, `logger`, `event`, `request_id`. Request-ID middleware в `api` и `rag`: генерирует UUID, кладёт в `contextvars`, прокидывает в логи и SSE-события. В `mcp` - через FastMCP-middleware.

### 4.4. Расширенный `/health`

Каждый сервис возвращает JSON со статусом зависимостей. При любой ошибке компонента - HTTP 503.

| Сервис | Проверки |
|---|---|
| `mcp` | `db.ping()`, `rag_client.health()` |
| `rag` | `client.heartbeat()` (ChromaDB), `_model is not None` (embedding) |
| `api` | `agent.health()` (LLM-клиент), `session_store.ping()`, `mcp_client.health()` |
| `web` | `http.get("http://api:8081/health")` (proxy health) |

### 4.5. Rate limiting

`slowapi`. `POST /api/chat`: 10 запросов/мин на IP. Остальные `/api/*`: 60 запросов/мин на IP. На `mcp` и `rag` внутри compose-сети rate limit не нужен.

### 4.6. Таймауты

| Переменная | Default | Назначение |
|---|---|---|
| `DEMO_REQUEST_TIMEOUT` | 90 (dev: 600) | Полный turn агента |
| `DEMO_STREAM_TIMEOUT` | 30 | Idle-таймаут между SSE-токенами |
| `MCP_HTTP_TIMEOUT` | 10 | Один вызов MCP-тула (steady-state) |
| `RAG_HTTP_TIMEOUT` | 60 | Один вызов RAG (включает embedding; на CPU 1-5 сек) |
| `ENABLE_THINK` | false в prod, true в dev | На маленьких моделях thinking тормозит и даёт пустые round'ы |

Таймауты - про **steady-state**. Cold-start `rag` (загрузка embedding) - до 120 секунд, покрывается `start_period` в compose healthcheck.

### 4.7. Graceful shutdown

SIGTERM в `uvicorn` workers дожидается завершения текущего SSE-стрима. В `LLMAgent.stream_events` при `CancelledError` - отменить `litellm.acompletion`, послать финальный SSE `error` event, закрыть `MCPClient`. В `MCPClient` и `RagClient`: при закрытии контекста - отменить текущие `httpx`-запросы через `asyncio.timeout`.

### 4.8. Бэкапы SQLite

Сервис `backup` (профиль `cron`), контейнер с `crond` + Python-скриптом `scripts/backup.py`:

- Cron `0 */6 * * *` - каждые 6 часов.
- `sqlite3.Connection.backup` (Python) для `university.db` и `demo_sessions.sqlite` → `/data/backups/<name>_$(date +%Y%m%d_%H%M%S).db`.
- Файлы старше 1 дня сжимаются через `gzip` → `.db.gz`. `_read_records` (если потребуется) читает через `gzip.open`.
- `find /data/backups -name "*.db*" -mtime +14 -delete`.
- Логи в stdout (`docker compose logs backup`).

`./.data/backups/` не коммитится.

### 4.9. Ротация и сжатие бэклога

`ModelBacklog` сейчас растёт неограниченно. Добавить:

- **Ротация по размеру**: `BACKLOG_ROTATE_MAX_BYTES` (default 10 МБ). При превышении текущий `.jsonl` закрывается, начинается новый с суффиксом `.1.jsonl`.
- **Сжатие старых**: `.jsonl` старше 1 дня → `.jsonl.gz`.
- **Опциональные полные payloads**: `BACKLOG_FULL_PAYLOADS` (1 - полные messages/tools в `model_request`, 0 - только summary). Default 0 в prod, 1 в dev.
- Cleanup (`BACKLOG_RETENTION_DAYS=30`) удаляет и `.jsonl`, и `.jsonl.gz`.

### 4.10. Убрать хардкод

Пройтись по коду, заменить числовые порты на env через `demo.settings`, bind-адреса через `*_HOST`, пути к файлам через env с дефолтами от `PROJECT_ROOT`.

### 4.11. Критерии готовности этапа 4

- Запрос к API без `Authorization: Bearer ...` → 401.
- С неверным токеном → 401, с верным → 200.
- `curl https://$DOMAIN/health` (через Caddy) → 200 с полным JSON.
- `curl https://$DOMAIN/api/chat` без токена → 401.
- 11-й запрос за минуту на `/api/chat` с одного IP → 429.
- SIGTERM в `api` во время стрима → SSE закрывается с `done`/`error`, соединение разрывается чисто.
- Сервис `backup` создаёт `/data/backups/*.db.gz` каждые 6 часов, старые (>14 дней) удаляются.
- В браузере devtools показывает запросы на `web:8080/api/*`, не на `api:8081` - токен остался в web-сервисе.

---

## Этап 5. CD

**Цель**: автоматическая публикация образов и раскатка на один VPS.

### 5.1. `.github/workflows/release.yml`

Триггеры: push тега `v*` (regex `^v\d+\.\d+\.\d+$`), `workflow_dispatch`. Job `build-and-push`: `docker/setup-buildx-action@v3`, `docker/login-action@v3` с `GHCR_TOKEN`, `docker buildx build` с тегами `ghcr.io/${{ github.repository_owner }}/agent-tutor:${{ github.sha }}`, `:${{ github.ref_name }}`, `:latest`. Push в GHCR.

### 5.2. `.github/workflows/deploy.yml`

Триггер: `workflow_run` после успешного `release.yml`, или `workflow_dispatch`. Job `deploy` через `appleboy/ssh-action@v0.1.10` с `DEPLOY_SSH_KEY`. На сервере:
```
cd /opt/agent-tutor
git pull
BACKUP_TAG=$(cat .current_image_tag 2>/dev/null || echo "")
docker compose --profile prod pull
docker compose --profile prod up -d
```
Проверка `/health` всех сервисов через `curl` с таймаутом 60 секунд. Если `/health` не отвечает: `down` → `docker tag $BACKUP_TAG ...` → `up -d` (откат). Сохранить текущий тег в `.current_image_tag`.

### 5.3. Secrets

`DEPLOY_SSH_KEY`, `SERVER_HOST`, `SERVER_USER` в GitHub Secrets. `GHCR_TOKEN` - auto-provided.

### 5.4. Критерии готовности этапа 5

- Push тега `v0.1.0` → образ в GHCR.
- `workflow_dispatch` на `deploy.yml` → на сервере `compose pull && up -d` отрабатывает за <2 минут.
- `/health` всех сервисов 200 после деплоя.
- Откат: повторный `workflow_dispatch` со старым тегом восстанавливает предыдущую версию за <2 минуты.

---

## Контрольные точки

| После этапа | Проверка |
|---|---|
| 0 | `python -m rag.service` + `python -m mcp_server.server` + `python -m demo.api.server` + `python -m demo.web.server` - все 4 сервиса поднимаются, UI чат работает end-to-end через HTTP MCP |
| 1 | `uv run pytest` - unit + integration зелёные, coverage ≥ 40%, ruff без ошибок |
| 2 | ✅ Файлы созданы: Dockerfile, compose (7 сервисов), Caddyfile, .env.example, .dockerignore, backup-скрипт. **Требуется проверка на Docker Engine.** |
| 2.5 | `docker compose ps` и smoke-проход локализуют падение по сервисам без чтения кода |
| 3 | push в `main` → CI зелёный (lint + test + build). E2E - отдельный workflow |
| 4 | Bearer enforced через web-прокси, `/health` агрегирует зависимости, таймауты разумные, `backup`-сервис создаёт `.db.gz` каждые 6 часов |
| 5 | тег `v0.1.0` → образ в GHCR → `compose --profile prod up -d` на VPS отрабатывает |

Каждый этап проверяется на чистом клонированном репо по инструкции в README. Если точка не проходит - этап не закрыт.
