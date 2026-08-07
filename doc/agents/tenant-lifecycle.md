# Tenant Lifecycle

## Создание tenant'а

**Admin API (основной способ):**
```bash
POST /admin/tenants
Authorization: Bearer $ADMIN_TOKEN
{
  "id": "autoparts",
  "config": { "version": 1, "data_source": { "driver": "postgres", "dsn": "..." }, "entities": [], "endpoints": [] }
}
```
→ `adminAddTenantHandler()` → `AddTenant()` (коннект к БД + создание роутера) → `SaveTenantConfig()` (пишет `.data/tenants/{id}.json`)

**Bootstrap при старте:** `--config` / `$DS_CONFIG` → tenant `"default"`. Все `.json` из `$TENANTS_DIR` (.data/tenants/) восстанавливаются.

**Через e2e helpers (рекомендуется для CI/тестов):**
```python
from tests.e2e.helpers import register_tenant, seed_database
seed_database(db_path, seed_path, project_root)
result = register_tenant("autoparts", config)
```

## Rewrite — Автогенерация конфига из БД

```bash
POST /admin/config/rewrite
X-Tenant-ID: autoparts
Authorization: Bearer $ADMIN_TOKEN
```
→ `adminRewriteHandler()`:
1. `adapter.Connect(ctx, cfg.DSN)` → коннект к БД
2. `adapter.Introspect(ctx, conn)` → читает схему
3. `configgen.Hydrate(configgen.ExtractIntent(inst.Config), schema)` → Config с entities, endpoints, MCP tools (старый путь `configgen.Generate(schema, dsConfig, nil)` больше не вызывается из rewrite — `tenant_admin.go:526`)
4. `SaveTenantConfig()` → пишет `.data/tenants/{id}.json`
5. `ReloadTenant(ctx, id, path)` — без даунтайма

**Что генерируется:** entity + strategy-эндпоинты (grep/filter/schema) на каждую таблицу, health/stats, mcp_tools (Фаза 2/2.5: 5 `db_*` + N `filter_{entity}`), read_only: true. `find`/`list` больше не генерируются (v4).

## Persistence

```
.data/tenants/
├── autoparts.json
├── default.json
└── shop.json
```
При старте: `os.ReadDir` → `config.Load()` → `store.AddTenant()`.

## Удаление

```bash
DELETE /admin/tenants/{id}
Authorization: Bearer $ADMIN_TOKEN
```
→ graceful drain: закрыть пул, удалить из мапы, стереть config.
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)
