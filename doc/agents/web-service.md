# Web Service (demo-web) — Reverse Proxy для разработки и демо

> 📘 Полное описание сервиса: [`demo/web/README.md`](../../demo/web/README.md)
> 📘 Embed-виджет (основной клиент): [`services/api-service/embed/README.md`](../../services/api-service/embed/README.md)
> 📘 Админ-панель: [`services/admin-dashboard/README.md`](../../services/admin-dashboard/README.md)

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
| `POST /api/reports` | → api-service | `/api/reports` (отчёты виджета) |
| `GET /embed/{path}` | → api-service | `/embed/{path}` (статик виджета) |
| `GET /api/agents` | → api-service | `/api/agents`, ответ проецируется до `{"agents":[{"name":…}]}` |
| `GET /api/health` | → api-service | `/health` |
| `GET /api/session/history` | → api-service | `/api/session/history`; серверный bearer **не** подставляется — запрос аутентифицируется capability-токеном браузера (`X-Session-Token`) |
| `GET /api/tenants` | — | список из `DEMO_TENANTS` env (без discovery через data-service) |

**Allowlist вместо catch-all:** не перечисленные выше `/api/*` маршруты не проксируются вообще.
`proxy_tenant_api(api/…)` пропускает наверх только `chat`, `chat/*`, `health`, `reports`,
`embed/*`; всё остальное отвечает `404` (pentest H1 — demo-web не должен быть прокси
к admin-контуру api-service с серверным bearer).

**Универсальный маршрут:** `GET/POST /api/tenant/{tenant_id}/{path:path}` → `data/{entity}`, `rag/{subpath}`, `api/{path}`, `chat`.

**Обработка SSE:** demo-web стримит SSE-ответы побайтово, что важно для chat-эндпоинтов.
Стримовые запросы идут с `httpx.Timeout(WEB_PROXY_TIMEOUT, read=None)` — таймаут
применяется к connect/write, но не к паузе между чанками (агент может долго думать
или звать тулы), а настоящие дедлайны держит api-service. Сбой апстрима посреди
стрима не обрывает поток молча: прокси добивает его терминальной парой
`error` + `done` с `correlation_id` (иначе виджет навсегда остаётся в «thinking»);
нестримовый таймаут — `504`. Текст терминальной ошибки выбирается по
`Accept-Language` (как в api-service), а неожиданное исключение в лог уходит
целиком, наружу — `500 Proxy error` без деталей (public errors sanitised).

**Session capability:** `session_id` demo-страницы лежит в localStorage, поэтому каждый reload — это возобновление серверной сессии. demo/web пробрасывает токен браузера (`X-Session-Token`) и не подставляет на чтение транскрипта серверный bearer (`attach_bearer=False`); `demo/web/static/app.js` хранит токен по ключу «агент + session_id», предъявляет его и на чате, и на чтении истории и не шлёт токен чужой сессии.

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
./infra/scripts/dev.sh restart api   # без restart api-service отдаёт старый JS
```

## Запуск

```bash
# Через dev.sh (рекомендуется)
./infra/scripts/dev.sh start

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
| `WEB_ORIGIN` | `http://localhost:8080` | CORS origin: только явные origins через запятую; wildcard (`*`, в том числе в списке) отклоняется с error-логом и fallback на dev-дефолт (pentest F5) |
| `API_BEARER_TOKEN` | — | Bearer token для аутентификации |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса |
| `DEFAULT_TENANT_ID` | `default` | Fallback tenant ID |
| `DEMO_TENANTS` | — | Список tenant IDs для UI селектора |
| `WEB_PROXY_TIMEOUT` | `30.0` | Таймаут HTTP-клиента (секунды); для SSE применяется только к connect/write, чтение стрима не ограничено |
---
**Last verified:** 2026-09-15 (working tree, pentest follow-up) — добавлено поведение capability-токена на demo-странице (чат + чтение истории) и гигиена ошибок прокси (accept-language, sanitised 500). Ранее: 2026-09-14 (working tree) — маршрутная таблица сверена с `demo/web/server.py` (убран несуществующий backlog/catch-all, добавлены agents/reports/tenants, отмечен capability на history), `WEB_ORIGIN` — fail-closed по wildcard.
