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
3. `configgen.Generate(schema, dsConfig, nil)` → Config с entities, endpoints, MCP tools
4. `SaveTenantConfig()` → пишет `.data/tenants/{id}.json`
5. `ReloadTenant(ctx, id, path)` — без даунтайма

**Что генерируется:** entity + endpoint get_by_id/find на каждую таблицу, health/stats, mcp_tools, read_only: true.

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
