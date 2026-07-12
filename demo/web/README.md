# Web Service (demo-web)

FastAPI reverse-proxy для multi-tenant архитектуры helperium.

## Роль в системе

`demo-web` — тонкий reverse-proxy, который:
- Обслуживает статический фронтенд (HTML/JS/CSS)
- Проксирует API-запросы к `demo-api:8081` (агент, чат, сессии)
- Проксирует данные напрямую в `data-service:8084` (обход api-service для снижения latency)
- Проксирует RAG-запросы в `rag:8082` (документы)
- Пробрасывает `X-Tenant-ID` для multi-tenancy изоляции

## Multi-Tenancy поддержка

### Заголовок X-Tenant-ID

Основной механизм идентификации тенанта — HTTP заголовок `X-Tenant-ID`:
```bash
curl -H "X-Tenant-ID: school-a" http://localhost:8080/api/data/students
```

### Два режима маршрутизации

#### 1. Стандартный прокси (с заголовком)

Запросы приходят с `X-Tenant-ID` в заголовке:
```
Browser → /api/data/students (X-Tenant-ID: tenant-a)
       → web:8080
       → data-service:8084/students (X-Tenant-ID: tenant-a)
```

#### 2. Явный tenant в URL (демо-режим)

Для удобства тестирования и демонстрации:
```
Browser → /api/tenant/school-a/data/students
       → web:8080
       → data-service:8084/students (X-Tenant-ID: school-a)
```

## Маршруты

### Статические файлы
- `GET /` — индекс.html
- `GET /static/{path}` — статические ассеты

### Health
- `GET /health` — статус web-сервиса

### Data Service (прямой прокси)
- `GET /api/manifest` — манифест инструментов из data-service
- `GET /api/data/stats` — статистика данных
- `GET /api/data/{entity}` — проксирование к data-service (students, teachers, disciplines и т.д.)

### RAG Service
- `GET /api/rag/documents` — список документов

### API Service (через api-service)
- `GET /api/health` — health-check API
- `GET /api/backlog` — модель бэклога
- `GET /api/session/history` — история сессий
- `POST /api/chat` — SSE-стриминг чата с агентом

### Tenant Routing (демо-режим)
- `GET|POST|... /api/tenant/{tenant_id}/{path:path}` — универсальный маршрут с tenant в URL:
  - `/api/tenant/{tenant}/data/{entity}` → data-service
  - `/api/tenant/{tenant}/rag/{path}` → rag-service
  - `/api/tenant/{tenant}/api/{path}` → api-service (с SSE для chat)

## Как работает X-Tenant-ID

### Proxy functions

В `server.py` есть три основные proxy-функции:

```python
async def _proxy_to_api(request, api_path, stream=False)
  # Проксирует в demo-api
  # Headers: X-Tenant-ID из request.headers ИЛИ request.state.tenant_id

async def _proxy_to_data_service(request, data_path)
  # Проксирует напрямую в data-service
  # Headers: X-Tenant-ID из request.headers ИЛИ request.state.tenant_id

async def _proxy_to_rag(request, rag_path, method="GET", json_body=None)
  # Проксирует в rag-service
  # Headers: X-Tenant-ID из request.headers ИЛИ request.state.tenant_id
```

### Логика tenant_id в proxy_tenant_api

```python
@app.api_route("/api/tenant/{tenant_id}/{path:path}")
async def proxy_tenant_api(request, tenant_id, path):
    # 1. Сохраняем tenant_id в request.state
    request.state.tenant_id = tenant_id

    # 2. Определяем целевой сервис по префиксу path
    if path.startswith("data/"):
        return await _proxy_to_data_service(request, f"/{path.replace('data/', '', 1)}")
    elif path.startswith("rag/"):
        # Специальный case: rag/documents → POST /documents/list
        return await _proxy_to_rag(request, "/documents/list", method="POST", json_body={})
    else:
        # API service
        api_path = path.replace("api/", "", 1) if path.startswith("api/") else f"api/{path}"
        return await _proxy_to_api(request, f"/{api_path}", stream=is_sse)
```

### Заголовки в _get_proxy_headers

```python
async def _get_proxy_headers(request):
    headers = {
        "user-agent": ...,
        "accept": ...,
        # ... другие заголовки
    }

    # X-Tenant-ID: сначала из HTTP заголовка, затем из request.state
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id and hasattr(request.state, "tenant_id"):
        tenant_id = request.state.tenant_id
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id

    return headers
```

## Конфигурация

### Environment Variables

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEMO_API_HOST` | `127.0.0.1` | Хост API сервиса |
| `DEMO_API_PORT` | `8081` | Порт API сервиса |
| `DEMO_WEB_HOST` | `127.0.0.1` | Хост web сервиса |
| `DEMO_WEB_PORT` | `8080` | Порт web сервиса |
| `WEB_ORIGIN` | `*` | CORS origin |
| `API_BEARER_TOKEN` | — | Опциональный bearer token для API |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service (прямой прокси) |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса (прямой прокси) |
| `DEFAULT_TENANT_ID` | `default` | Fallback tenant ID для UI селектора |
| `DEMO_TENANTS` | — | Comma-separated список tenant IDs для явного отображения в UI |
| `WEB_PROXY_TIMEOUT` | `30.0` | Таймаут HTTP-клиента для проксирования (секунды) |

### Docker Compose

В `docker-compose.yml` web-сервис запускается с переменными:
```yaml
environment:
  - DEMO_API_HOST=api
  - DEMO_API_PORT=8081
```

## Запуск

### Нативный (Mac/Linux)
```bash
# Через dev.sh
./scripts/dev.sh start

# Или напрямую
uv run --package demo-web python -m demo.web.server
```

### Docker
```bash
docker compose up -d web
```

## Тестирование

### Unit-тесты
```bash
uv run pytest demo/web/tests/unit/ -v   # 50 тестов (22 proxy + 4 urls + 24 CORS)
```

### E2E-тесты
```bash
# Полный пайплайн: materialize БД → register tenants → proxy check → SSE chat
uv run agent-db e2e --tenants default,shop

# Только data isolation + admin API (8 тестов)
uv run agent-db e2e-data

# Только MCP dynamic tool resolution (3 теста)
uv run agent-db e2e-mcp

# Все три уровня разом
uv run agent-db e2e-full
```

### Интеграционные тесты
```bash
# Все 591 Go-тест (data-service: 470 в 14 пакетах, mcp-gateway: 121 в 5)
go test ./data-service/... ./mcp-gateway/... -count=1
```

## Ключевые особенности

1. **Stateless** — не хранит состояние сессий локально (кроме кэша в SQLite через api-service)
2. **Multi-tenant aware** — корректно пробрасывает X-Tenant-ID во все downstream сервисы
3. **SSE proxy** — поддерживает streaming для chat endpoint
4. **Correlation ID** — пробрасывает x-correlation-id для трейсинга
5. **Bearer token** — пробрасывает Authorization если настроен

## Ограничения

- Нет прямого доступа к БД (только через data-service)
- Не выполняет бизнес-логику (только проксирование)
- Read-only проксирование в data-service (мутации через API)

---

## 🔧 Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `RuntimeError: Directory '.../demo/web/static' does not exist` | Запуск не из корня проекта | `cd /project/root && uv run python -m demo.web.server` |
| 404 на `/api/manifest` для tenant | Тенант не зарегистрирован в data-service | `uv run agent-db tenant list` → `uv run agent-db register <id> <scenario>` |
| Web не проксирует на data-service | Не тот `X-Tenant-ID` или data-service giù | Проверить логи data-service, заголовок `X-Tenant-ID` |
| SSE chat: `Connection refused` на 8081 | api-service не запущен | `cd api-service && uv run python -m api_service.server --port 8081` |
| 502 Bad Gateway | Downstream сервис упал | Проверить логи соответствующего сервиса (data-service:808084, api:8081, rag:8082) |

### Быстрый smoke-тест
```bash
# 1. Все сервисы запущены? (порты 8080-8084)
lsof -ti:8080,8081,8083,8084

# 2. Health web
curl -s http://127.0.0.1:8080/health

# 3. Proxy → data-service (нужен запущенный data-service + registered tenant)
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8080/api/manifest | jq '.entities | length'
# Должен вернуть > 0

# 4. Proxy → demo-api (SSE chat endpoint)
curl -s -X POST http://127.0.0.1:8080/api/chat \
  -H "Content-Type: application/json" -H "X-Tenant-ID: default" \
  -d '{"message":"test","session_id":"test"}' | head -c 200
```

### Логи
- Ручное запуск: stdout/stderr терминала
- Через `dev.sh`: `.data/logs/web.log`
