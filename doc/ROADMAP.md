# Roadmap: выход на pre-prod уровень

Контракт между владельцем и исполнителем. Только решения, инварианты, контрольные точки. Детали реализации — в отдельных задачах.

## Текущее состояние → Цель

**Сейчас**: рабочий MVP. SQLite + ChromaDB + FastMCP-сервер с тулами + LiteLLM-агент в Starlette + статический web-чат. Тестов нет, контейнеров нет, CI/CD нет.

**Цель**: 4 long-running контейнера (`mcp`, `rag`, `api`, `web`) + 3 профильных (`agent` для генерации, `backup` для бэкапов, `caddy` для prod). `docker compose up` поднимает всё. Тесты ≥40% на Этапе 1, далее по мере рефакторингов. CI/CD на GitHub Actions. LLM-провайдер (Ollama / Mistral / OpenAI) — внешняя зависимость через URL, своего контейнера `ollama` нет.

## Глоссарий

- **Инвариант** — требование, которое нельзя нарушать без согласования.
- **Сервис** — long-running Docker-контейнер в compose.
- **One-shot сервис** — контейнер с профилем, запускается через `docker compose run --rm`, после exit не висит.
- **CLI-команда** — процесс в образе `mcp` (или отдельном `agent`), запускается вручную.

## Инварианты

- **I1**: каждый этап оставляет проект в рабочем состоянии. После каждого этапа `uv run mcp dev server.py` (stdio) и UI-чат работают.
- **I2**: пользовательские данные (`.data/`, `backlog/`, `chroma_db/`, `generated_materials/`) не теряются при перезапуске и не коммитятся.
- **I3**: не ломать существующие публичные API без явного согласования.
- **I4**: `api`-сервис работает в **1 воркер uvicorn**. Сессионные локи in-process, между воркерами не синхронизируются. Горизонтальное масштабирование — за рамками pre-prod.
- **I5**: один Docker-образ на все сервисы; выбор через `command` в compose.
- **I6**: `university.db` — фикстура для демо, **не** production-БД. На реальном проде источник данных — внешняя БД вуза; подключение к ней — отдельная задача, **не в этом roadmap**.
- **I7**: рефакторинг — после тестов. Тесты — на стабильную версию кода. Контейнеризация — на протестированный код.

## Принципы

- **Минимальная достаточность**: Kafka, K8s, PostgreSQL, Redis, gRPC, Prometheus — не используем. SQLite + FastAPI + compose + один VPS достаточно.
- **Тесты — на стабильное**: не пишем тесты на каждый цикл оркестратора (4 сценария достаточно), не тестируем обёртки над внешними библиотеками (chonkie, sentence-transformers).
- **Долгое — в CLI**: импорт PDF и генерация материалов — one-shot, не в long-running сервисах.
- **Токены — не в браузер**: `API_BEARER_TOKEN` хранится в `web`-сервисе, добавляется при reverse-proxy.

## Что НЕ делаем

- Свой контейнер `ollama` — внешний провайдер через URL.
- Миграции БД — `db/schema.py` остаётся идемпотентным (`CREATE TABLE IF NOT EXISTS`). На проде SQLite исчезнет или станет write-through кэшем.
- Полноценная авторизация пользователей (логин/пароль, OAuth) — достаточно Bearer-токена.
- PydanticAI — задача в `doc/TASK.md`, это смена архитектуры агента. Не в этом roadmap.
- In-memory тесты FastMCP — капризен между версиями SDK. Используем subprocess + HTTP.

---

## Целевая архитектура

### Сетевая топология

MCP обзается с базой данных (статичная sqlite сейчас просто формируется из `fixtures.json`)
Также MCP общается с RAG;
RAG хранит документы в ChromaDB и также их туда индексирует и тд;
CORE: API там агент который подключается к MCP для tools а также к провайдеру (ollama, openai, etc.);
DEMO: WEB — это просто фронт он общается только с API и также передает сессии (по сути создает но не хранит только если экзмепляр в свое js), отдает статическую страничку и умеет общаться с API по SSE чтобы передовать сообщения от агента;


### Сервисы

| Сервис | Режим | Стек | Порт (compose / host) | Volumes |
|---|---|---|---|---|
| `mcp` | long-running | FastMCP | 8083 / `127.0.0.1:8083` (dev) | `app_data:/data/app:ro` |
| `rag` | long-running | FastAPI | 8082 / `127.0.0.1:8082` (dev) | `rag_data:/data/rag`, `hf_cache:/home/app/.cache/huggingface` |
| `api` | long-running, 1 worker | FastAPI + uvicorn | 8081 / `127.0.0.1:8081` (dev) | `app_data:/data/app`, `backups:/data/backups` |
| `web` | long-running | FastAPI (статика + proxy) | 8080 / `127.0.0.1:8080` (dev) | — |

### Тома (в `./.data/`)

| Том | Контейнер-путь | Содержимое |
|---|---|---|
| `app_data` | `/data/app` | `university.db`, `demo_sessions.sqlite`, `backlog/` |
| `rag_data` | `/data/rag` | `chroma_db/` |
| `hf_cache` | `/home/app/.cache/huggingface` | embedding-модели и кеш transformers |
| `backups` | `/data/backups` | `*.db.gz` снапшоты |

### Ключевые env-переменные

`RAG_SERVICE_URL=http://rag:8082`, `MCP_SERVICE_URL=http://mcp:8083/mcp`, `OLLAMA_URL=http://host.docker.internal:11434`, `MISTRAL_API_KEY`, `API_BEARER_TOKEN` (обязателен в prod), `WEB_ORIGIN`, `MCP_TRANSPORT=http` (stdio только для `uv run mcp dev`), `ENABLE_THINK` (false в prod, true в dev). Полный список — в `.env.example` (Этап 2).

---

## Этап 0. Подготовка и разбиение миграции

**Цель**: не делать большой рефакторинг одним коммитом. Сначала отделяем сервисы по границам ответственности, затем переводим клиентов на HTTP, затем мигрируем web/API-прокси и только после этого выносим CLI-утилиты.

**Правило этапа**: каждый подпункт — отдельный безопасный срез. Перед переходом к следующему подпункту обязательно выполняются локальные smoke/integration-проверки. Если проверка не проходит, следующий подпункт не начинаем.

**Порядок**: 0.0 → 0.1 → 0.2 → 0.3 → 0.4 → 0.5. Никаких параллельных миграций внутри этапа.

### 0.0. Базовая фиксация состояния ✅

До любых изменений зафиксировать текущую работоспособность:

- `uv run mcp dev server.py` (stdio) — все тулы отвечают.
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

Смысл подпункта — убрать прямую связность `mcp` с реализацией RAG, оставить только клиент.

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

- `mcp_server/` — вынесена директория для MCP-сервера (ранее `server.py` в корне)
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

> **✅ Выполнено**: CLI-утилиты `agent-ingest` и `agent-generate` выделены как one-shot команды. `agent-ingest` использует `RagClient` для работы с RAG-сервисом через HTTP. `agent-generate` работает через отдельный entrypoint. MCP-сервер не имеет прямых зависимостей на генерацию документов — использует только HTTP-клиент к RAG.

### 0.6. Критерии готовности этапа 0 (✅)

- Все подпункты 0.0–0.4 пройдены по отдельности.
- После каждого подпункта проект остаётся рабочим.
- `uv run mcp dev server.py` (stdio) и UI-чат работают на промежуточных состояниях.
- `uv run python -m rag.service` поднимается; `curl :8082/health` → 200.
- `uv run python -m demo.api.server` стартует с `MCP_SERVICE_URL` и `RAG_SERVICE_URL`.
- `uv run python -m demo.web.server` стартует; `curl :8080/api/health` → 200.
- `tools/rag.py` **не удалён** — оставлен как минималистичная заглушка для обратной совместимости с `fixtures/document_generator.py`, использует `RagClient` из `rag/client.py`.
- Перед переходом к Этапу 1 есть один общий smoke-прогон всего контура: `rag → mcp → api → web`.
**Выполнено на этапе 0.5***


## Этап 1. Тестовая инфраструктура

**Цель**: покрыть тестами модули, изменённые в Этапе 0 и которые будут меняться в Этапе 2. **Покрытие ≥ 40%** на Этапе 1, поднимается по мере рефакторингов.

### 1.1. Зависимости

Добавить в `pyproject.toml` (`[dependency-groups]` или `[project.optional-dependencies.test]`): `pytest>=8`, `pytest-asyncio>=0.24`, `pytest-cov>=5`, `pytest-mock>=3.14`, `respx>=0.21` (мок httpx), `freezegun>=1.5`, `httpx>=0.28`, `ruff>=0.7`.

### 1.2. Структура `tests/`

```
tests/
├── conftest.py
├── unit/
│   ├── rag/         (config, parser, chunker, vector_store, repository, pipeline, service, client)
│   ├── db/          (database, fixtures)
│   ├── tools/       (test_tools.py — все 4 тулза)
│   └── demo/
│       ├── sessions.py, backlog.py, settings.py, server.py
│       └── agent/   (tool_parser, conversation, mcp_client)
├── integration/
│   ├── rag/         (e2e_pipeline.py — реальная ChromaDB + мок Embedding)
│   ├── mcp/         (server_http.py — subprocess + HTTP-вызов каждого тула)
│   └── api/         (chat_flow.py — SSE-сценарии с моками LLMClient/MCPClient)
└── fixtures/        (paragraphs.txt коммитится; PDF/DOCX генерируются в conftest.py)
```

PDF/DOCX-фикстуры не коммитятся — генерируются через `fixtures/document_generator.py` в `tmp_path`. Pytest: `asyncio_mode = "auto"`, маркеры `unit/integration/e2e/slow`, `coverage.fail_under = 40`.

### 1.3. Обязательный минимум кейсов

Без этих кейсов этап 1 не закрыт. Остальное — в `doc/TEST_CASES.md` (по мере надобности).

- **`rag/config.py`**: `from_env()`, дефолт `chroma_path`.
- **`rag/parser.py`**: пустой PDF → `[]`; текстовые форматы напрямую; несуществующий путь → `FileNotFoundError`.
- **`rag/chunker.py`**: 3 стратегии на `paragraphs.txt` стабильны; `chunk_pages` с многостраничным входом.
- **`rag/vector_store.py`** (реальная ChromaDB в `tmp_chroma_path`): `add_chunks`→`search`, `delete_by_document_id`, фильтр по `discipline_id`.
- **`rag/repository.py`**: `save_document_with_chunks` откатывает SQLite и чистит vector_store при ошибке chroma; `delete_document` каскадно.
- **`rag/pipeline.py`**: `import_document` happy + пустой результат → `ValueError`; `search_documents` пустой query → `[]`.
- **`rag/service.py`**: `/health` (200/503), `/search`, `/documents/import`.
- **`rag/client.py`** (через `respx`): все методы.
- **`db/database.py`**: все методы + edge-кейсы.
- **`tools/*.py`**: по 1 позитивному + 1 негативному на тул.
- **`server.py`**: каждый тул вызывается через HTTP MCP-клиент к поднятому subprocess.
- **`demo/api/sessions.py`**: round-trip, `_compact_message`, `_trim_session`.
- **`demo/api/backlog.py`**: каждый тип события пишется/читается; cleanup.
- **`demo/api/agent/tool_parser.py`**: native + markdown JSON + «голый» JSON + невалидный; `format_for_model`.
- **`demo/api/agent/orchestrator.py`**: **4 сценария** — (1) прямой ответ, (2) tool→final, (3) `max_iterations` → fallback, (4) исключение в MCPClient → `tool_result` с `ok=false`.
- **`demo/api/agent/mcp_client.py`**: `call_tool` happy + ошибка; парсинг `structuredContent`.
- **`demo/api/server.py`**: `/health`, `/api/data`, `/api/chat` с пустым сообщением → SSE error.
- **`fixtures/ingest.py`**: только smoke — argparse валидация, без сети.

### 1.6. Стандартизация API (OpenAPI/Swagger)

Для всех сервисов (`rag`, `api`, `web`) обеспечить полнофункциональное документирование API:

- **Типизация**: Использование Pydantic `BaseModel` для всех Request/Response моделей.
- **Аннотации**: Добавление `response_model`, `summary`, `description` для всех эндпоинтов.
- **Валидация**: Добавление описаний полей через `Field` и корректных кодов ответов.
- **Доступность**: Убедиться, что `/docs` (Swagger UI) доступен для каждого сервиса и корректно отображает структуру API.
- **Цель**: Использование OpenAPI-схем в интеграционных тестах для автоматической проверки контрактов (например, через `fastapi.testclient` или валидацию ответов).

### 1.7. Критерии готовности этапа 1

- `uv run pytest -m "unit or integration"` — все зелёные.
- `uv run pytest --cov --cov-fail-under=40` показывает покрытие ≥ 40% для `db`, `tools`, `rag`, `demo`, `server` (исключая `demo/web/static/*`, `fixtures/document_generator.py`, `fixtures/generate.py`).
- `uv run ruff check .` и `uv run ruff format --check .` — без ошибок.
- Для всех эндпоинтов `rag`, `api`, `web` сгенерированы корректные спецификации OpenAPI (проверка через `curl :808x/openapi.json`).

---

## Этап 2. Контейнеризация

**Цель**: всё запускается через `docker compose up` в dev-режиме.

### 2.1. Dockerfile

Multi-stage, один на все сервисы.

**`builder`**: `python:3.12-slim` → установить `uv` → скопировать `pyproject.toml` + `uv.lock` → `uv sync --frozen --no-install-project` → скопировать исходники → `uv sync --frozen`.

**`runtime`**: `python:3.12-slim` → создать пользователя `app` (UID 1000, HOME=`/home/app`) → скопировать `/app` из `builder` с `--chown=app:app` → `ENV PATH=/app/.venv/bin:$PATH`, `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/home/app`, `HF_HOME=/home/app/.cache/huggingface` → `USER app`.

**Без прогрева embedding в образе.** Кеш HuggingFace монтируется через volume `hf_cache`. При первом запуске `rag` модель докачивается один раз в volume, далее берётся из кеша. Это даёт лёгкий образ, возможность поменять `RAG_EMBEDDING_MODEL` без пересборки, быстрый CI-build.

### 2.2. `docker-compose.yml`

| Сервис | `command` | Порты (dev) | Volumes | Зависит от |
|---|---|---|---|---|
| `mcp` | `python -m server` | `127.0.0.1:8083:8083` | `app_data:/data/app:ro` | `rag` (healthy) |
| `rag` | `python -m rag.service` | `127.0.0.1:8082:8082` | `rag_data`, `hf_cache` | — |
| `api` | `python -m demo.api.server` | `127.0.0.1:8081:8081` | `app_data`, `backups` | `mcp` (healthy) |
| `web` | `python -m demo.web.server` | `127.0.0.1:8080:8080` | — | `api` (healthy) |
| `agent` (profile `tools`) | one-shot через `compose run --rm` | — | `app_data` | `rag` (healthy) |
| `backup` (profile `cron`) | `cron -f` через контейнер с `crond` | — | `app_data:ro`, `backups` | — |
| `caddy` (profile `prod`) | `caddy run --config /etc/caddy/Caddyfile` | `80:80`, `443:443` | — | `web`, `api` (healthy) |

Одна bridge-сеть `agent-tutor-net`. Healthchecks:

- `mcp`: `curl -f :8083/health`, `interval=10s`, `timeout=5s`, `start_period=20s`, `retries=3`.
- `rag`: `curl -f :8082/health`, `start_period=120s` (cold start с загрузкой embedding-модели), `retries=10`.
- `api`: `curl -f :8081/health`, `start_period=15s`.
- `web`: `curl -f :8080/`, `start_period=10s`.
- `caddy`: `wget -q -O- :2019/metrics || exit 1`.

`start_period=120s` у `rag` — критично: `SentenceTransformerEmbedding` лениво грузит ~470 МБ модели. 30 секунд приведёт к ложным unhealthy на cold start.

### 2.3. Prod-профиль

Не отдельный файл. Prod-режим — `COMPOSE_PROFILES=prod` или `docker compose --profile prod up -d`. В этом режиме поднимается `caddy` с авто-TLS через Let's Encrypt. Для long-running сервисов `restart: unless-stopped` через YAML-якорь.

### 2.4. `.env.example`

Полный список env-переменных с дефолтами, разбитый по сервисам: **RAG** (только в `rag`-сервисе): `RAG_EMBEDDING_MODEL`, `RAG_EMBEDDING_BATCH_SIZE`, `RAG_DEVICE`, `RAG_CHUNKER_TYPE`, `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`, `RAG_PAGE_OVERLAP_TOKENS`, `CHROMA_PATH`, `CHROMA_COLLECTION`, `RAG_CONTEXT_MAX_TOKENS`, `RAG_LOCAL_FILES_ONLY`. **MCP**: `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `RAG_SERVICE_URL`. **API**: `MCP_SERVICE_URL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `MISTRAL_API_KEY`, `MISTRAL_MODEL`, `DEMO_API_HOST`, `DEMO_API_PORT`, `DEMO_REQUEST_TIMEOUT` (90 в prod, 600 в dev), `ENABLE_THINK` (false в prod), `DEMO_HISTORY_TURNS`, `DEMO_HISTORY_CONTENT_CHARS`, `BACKLOG_DIR`, `BACKLOG_RETENTION_DAYS`, `BACKLOG_ROTATE_MAX_BYTES`, `BACKLOG_FULL_PAYLOADS` (0 в prod), `AGENT_TEMPERATURE`, `AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOKENS_THINKING`, `AGENT_MAX_EMPTY_ROUNDS`, `DEMO_SESSION_DB_PATH`, `DB_PATH`. **Web**: `WEB_HOST`, `WEB_PORT`, `WEB_ORIGIN`, `API_BEARER_TOKEN`. **Генерация** (только в `agent`): `DOCGEN_MODEL`, `DOCGEN_OLLAMA_URL`, `DOCGEN_NUM_PREDICT`, `DOCGEN_MAX_ATTEMPTS`, `DOCGEN_MIN_RESPONSE_CHARS`, `DOCGEN_FAKE_SEED`, `DOCGEN_OUTPUT_DIR`. **Общие**: `DEMO_DEBUG`, `LITELLM_DEBUG`.

### 2.5. `.dockerignore`

Исключает: `.venv/`, `.git/`, `__pycache__/`, `.data/`, `backlog/`, `chroma_db/`, `generated_materials/`, `*.db`, `*.sqlite*`, `fixtures.json`, `.env*`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `.coverage`, `tests/`, `*.md`.

### 2.6. Критерии готовности этапа 2

- `docker compose up -d` поднимает 4 long-running сервиса.
- `docker compose ps` — все 4 в `healthy` в течение 180 секунд.
- `curl :8081/health` → `{"api":"ok","ollama":{...}}`.
- `curl :8080/api/health` → 200.
- Web UI чат работает end-to-end.
- `docker compose --profile tools run --rm agent agent-generate --discipline-id <id>` создаёт PDF/DOCX и кладёт в индекс.
- `docker compose --profile cron up -d` запускает `backup`; через час появляется `backups/*.db.gz`.
- `docker compose restart` не теряет данные в `./.data/`.
- `rm -rf .data && docker compose down -v && docker compose up -d` поднимает чистый стек без ошибок.

---



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

E2E тесты (`tests/e2e/`) — отдельный workflow, запускается через `workflow_dispatch` или nightly, **не** на каждый push.

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

**Цель**: один VPS не падает от кривого промпта, не теряет данные, наблюдаем. **Не включает подключение к реальной БД вуза** — это отдельная задача с другой архитектурой (см. I6).

### 4.1. Аутентификация

Схема: `web`-сервис хранит `API_BEARER_TOKEN` из env и добавляет `Authorization: Bearer ...` при reverse-proxy `/api/*` (см. 0.4.1). Браузер токен не видит.

- Middleware в `demo/api/server.py` (FastAPI) проверяет `Authorization: Bearer <API_BEARER_TOKEN>`. Если токен не задан в prod — `api` не стартует (проверка в `main()` при создании `app`).
- `web`-сервис: если `API_BEARER_TOKEN` не задан и профиль `prod` — не стартует.
- CORS в `api`: `allow_origins` ограничивается `WEB_ORIGIN` (не `*`). В prod `api` не торчит наружу напрямую — трафик идёт через Caddy.

### 4.2. HTTPS через Caddy

Сервис `caddy` (профиль `prod`), образ `caddy:2`. `Caddyfile` автоматически получает TLS через Let's Encrypt для домена из env `DOMAIN`. Проксирует:
- `https://$DOMAIN/` → `http://web:8080/`
- `https://$DOMAIN/api/*` → `http://web:8080/api/*` (далее через web-прокси к api)

### 4.3. Структурированное логирование

Добавить `structlog`. JSON-формат во всех сервисах: `timestamp`, `level`, `logger`, `event`, `request_id`. Request-ID middleware в `api` и `rag`: генерирует UUID, кладёт в `contextvars`, прокидывает в логи и SSE-события. В `mcp` — через FastMCP-middleware.

### 4.4. Расширенный `/health`

Каждый сервис возвращает JSON со статусом зависимостей. При любой ошибке компонента — HTTP 503.

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
| `RAG_HTTP_TIMEOUT` | 60 | Один вызов RAG (включает embedding; на CPU 1–5 сек) |
| `ENABLE_THINK` | false в prod, true в dev | На маленьких моделях thinking тормозит и даёт пустые round'ы |

Таймауты — про **steady-state**. Cold-start `rag` (загрузка embedding) — до 120 секунд, покрывается `start_period` в compose healthcheck.

### 4.7. Graceful shutdown

SIGTERM в `uvicorn` workers дожидается завершения текущего SSE-стрима. В `LLMAgent.stream_events` при `CancelledError` — отменить `litellm.acompletion`, послать финальный SSE `error` event, закрыть `MCPClient`. В `MCPClient` и `RagClient`: при закрытии контекста — отменить текущие `httpx`-запросы через `asyncio.timeout`.

### 4.8. Бэкапы SQLite

Сервис `backup` (профиль `cron`), контейнер с `crond` + Python-скриптом `scripts/backup.py`:

- Cron `0 */6 * * *` — каждые 6 часов.
- `sqlite3.Connection.backup` (Python) для `university.db` и `demo_sessions.sqlite` → `/data/backups/<name>_$(date +%Y%m%d_%H%M%S).db`.
- Файлы старше 1 дня сжимаются через `gzip` → `.db.gz`. `_read_records` (если потребуется) читает через `gzip.open`.
- `find /data/backups -name "*.db*" -mtime +14 -delete`.
- Логи в stdout (`docker compose logs backup`).

`./.data/backups/` не коммитится.

### 4.9. Ротация и сжатие бэклога

`ModelBacklog` сейчас растёт неограниченно. Добавить:

- **Ротация по размеру**: `BACKLOG_ROTATE_MAX_BYTES` (default 10 МБ). При превышении текущий `.jsonl` закрывается, начинается новый с суффиксом `.1.jsonl`.
- **Сжатие старых**: `.jsonl` старше 1 дня → `.jsonl.gz`.
- **Опциональные полные payloads**: `BACKLOG_FULL_PAYLOADS` (1 — полные messages/tools в `model_request`, 0 — только summary). Default 0 в prod, 1 в dev.
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
- В браузере devtools показывает запросы на `web:8080/api/*`, не на `api:8081` — токен остался в web-сервисе.

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

`DEPLOY_SSH_KEY`, `SERVER_HOST`, `SERVER_USER` в GitHub Secrets. `GHCR_TOKEN` — auto-provided.

### 5.4. Критерии готовности этапа 5

- Push тега `v0.1.0` → образ в GHCR.
- `workflow_dispatch` на `deploy.yml` → на сервере `compose pull && up -d` отрабатывает за <2 минут.
- `/health` всех сервисов 200 после деплоя.
- Откат: повторный `workflow_dispatch` со старым тегом восстанавливает предыдущую версию за <2 минуты.

---

## Контрольные точки

| После этапа | Проверка |
|---|---|
| 0 | `python -m rag.service` + `python -m mcp_server.server` + `python -m demo.api.server` + `python -m demo.web.server` — все 4 сервиса поднимаются, UI чат работает end-to-end через HTTP MCP |
| 1 | `uv run pytest` — unit + integration зелёные, coverage ≥ 40%, ruff без ошибок |
| 2 | `docker compose up -d` — 4 long-running сервиса `healthy`, UI чат работает. `tools`/`cron`/`prod` профили стартуют |
| 2.5 | `docker compose ps` и smoke-проход локализуют падение по сервисам без чтения кода |
| 3 | push в `main` → CI зелёный (lint + test + build). E2E — отдельный workflow |
| 4 | Bearer enforced через web-прокси, `/health` агрегирует зависимости, таймауты разумные, `backup`-сервис создаёт `.db.gz` каждые 6 часов |
| 5 | тег `v0.1.0` → образ в GHCR → `compose --profile prod up -d` на VPS отрабатывает |

Каждый этап проверяется на чистом клонированном репо по инструкции в README. Если точка не проходит — этап не закрыт.
