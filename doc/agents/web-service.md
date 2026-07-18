# Web Service — Multi-Tenancy Architecture

`demo/web/server.py` — тонкий reverse-proxy.

## Два режима маршрутизации

1. **Стандартный (X-Tenant-ID):**
   ```
   Browser → GET /api/data/students (X-Tenant-ID: tenant-a) → web:8080 → data-service:8084/students
   ```

2. **Явный tenant в URL (демо):**
   ```
   GET /api/tenant/tenant-a/data/students → web → data-service с X-Tenant-ID: tenant-a
   ```

## Ключевые маршруты

| Маршрут | Прокси | Куда |
|---|---|---|
| `GET /api/manifest` | → data-service | `/mcp/manifest` |
| `GET /api/data/{entity}` | → data-service | `/{entity}` |
| `GET /api/data/stats` | → data-service | `/stats` |
| `GET /api/rag/documents` | → rag-service | `/documents/list` |
| `GET/POST /api/chat` | → api-service | `/api/chat` (SSE) |
| `GET /embed/{path}` | → api-service | `/embed/{path}` |

**Универсальный маршрут:** `GET/POST /api/tenant/{tenant_id}/{path:path}` → `data/{entity}`, `rag/{path}`, `api/{path}`, `chat`.

## Embed Widget

После изменений в `api-service/embed/src/` или `api-service/embed/css/`:
```bash
cd api-service/embed && npm run build
./scripts/dev.sh restart api
```

> ⚠️ Без `restart api` api-service отдаёт старый JS.

## Тесты web

```bash
uv run pytest demo/web/tests/unit/ -v    # 50 тестов
```
