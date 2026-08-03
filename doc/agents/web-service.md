# Web Service (demo-web) — Reverse Proxy для разработки и демо

> 📘 Полное описание сервиса: [`demo/web/README.md`](../../demo/web/README.md)
> 📘 Embed-виджет (основной клиент): [`api-service/embed/README.md`](../../api-service/embed/README.md)
> 📘 Админ-панель: [`admin-dashboard/README.md`](../../admin-dashboard/README.md)

`demo/web/server.py` — reverse-proxy для локальной разработки и демонстрации.

## Роль в системе

**Важно:** demo-web — **не основной entry point** в production-сценарии. Это наследие MVP, сохраняемое для удобства разработки, локального тестирования и демо-презентаций.

Основные клиенты ходят напрямую:
- **Embed виджет** (`embed.js`) → `POST /api/chat/{name}` напрямую в **api-service:8081**, минуя demo-web
- **Admin Dashboard** → напрямую в **admin-dashboard:8085**, который проксирует к data-service и api-service

### Потенциал развития

demo-web может эволюционировать в полноценный production entry point (API gateway) — для этого потребуется:
- Добавить обработку большего количества Content-Type (сейчас только GET + SSE)
- Убрать прямые прокси к data-service (это нарушает архитектурную изоляцию)
- Добавить rate limiting, auth, CORS на уровне gateway

## Два режима маршрутизации

1. **Стандартный (X-Tenant-ID):**
   ```
   Browser → GET /api/data/students (X-Tenant-ID: tenant-a)
       → demo-web:8080 → data-service:8084/students
   ```

2. **Явный tenant в URL (демо):**
   ```
   GET /api/tenant/tenant-a/data/students → demo-web → data-service с X-Tenant-ID: tenant-a
   ```

## Ключевые маршруты

| Маршрут | Прокси | Куда |
|---|---|---|
| `GET /api/manifest` | → data-service | `/mcp/manifest` |
| `GET /api/data/{entity}` | → data-service | `/{entity}` |
| `GET /api/data/stats` | → data-service | `/stats` |
| `GET /api/rag/documents` | → rag-service | `POST /documents/list` |
| `POST /api/chat` | → api-service | `/api/chat` (SSE) |
| `POST /api/chat/{agent_name}` | → api-service | `/api/chat/{agent_name}` (SSE) |
| `GET /embed/{path}` | → api-service | `/embed/{path}` (статик виджета) |
| `GET /api/backlog` | → api-service | `/api/backlog` |
| `GET /api/backlog/{session_id}` | → api-service | `/api/backlog/{session_id}` |
| `GET /api/session/history` | → api-service | `/api/session/history` |
| `ANY /api/{path:path}` | → api-service | catch-all для /api/* |

**Универсальный маршрут:** `GET/POST /api/tenant/{tenant_id}/{path:path}` → `data/{entity}`, `rag/{subpath}`, `api/{path}`, `chat`.

**Обработка SSE:** demo-web корректно стримит SSE-ответы побайтово, что важно для chat-эндпоинтов.

## Embed Widget

Путь виджета (без demo-web):
```
Браузер → <script src="https://server.com/embed/embed.js">
   → initWidget() → POST /api/chat/{name} → напрямую в api-service:8081
```

Через demo-web (для разработки):
```
Браузер → localhost:8080 → прокси GET /embed/{path} → api-service:8081/embed/{path}
```

После изменений в виджете:
```bash
cd api-service/embed && npm run build
./scripts/dev.sh restart api   # без restart api-service отдаёт старый JS
```

## Запуск

```bash
# Через dev.sh (рекомендуется)
./scripts/dev.sh start

# Напрямую
uv run --package demo-web python -m demo.web.server
```

## Тесты

```bash
uv run pytest demo/web/tests/unit/ -v    # ~50 тестов
```

## Конфигурация

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEMO_API_HOST` | `127.0.0.1` | Хост api-service |
| `DEMO_API_PORT` | `8081` | Порт api-service |
| `DEMO_WEB_HOST` | `127.0.0.1` | Хост web сервиса |
| `DEMO_WEB_PORT` | `8080` | Порт web сервиса |
| `WEB_ORIGIN` | `http://localhost:8080` | CORS origin |
| `API_BEARER_TOKEN` | — | Bearer token для аутентификации |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса |
| `DEFAULT_TENANT_ID` | `default` | Fallback tenant ID |
| `DEMO_TENANTS` | — | Список tenant IDs для UI селектора |
| `WEB_PROXY_TIMEOUT` | `30.0` | Таймаут HTTP-клиента (секунды) |
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)
