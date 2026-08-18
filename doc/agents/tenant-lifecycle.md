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

## SQLite read-only connection contract

> `data_source.read_only: true` одновременно запрещает write-методы в API и, для file-backed SQLite tenant, создаёт отдельное database-level read-only соединение. Основное соединение остаётся внутренним путём data-service для admin-операций и schema introspection.

| Конфигурация | Admin / introspection connection | Runtime query connection |
|---|---|---|
| SQLite, `read_only` выключен или отсутствует | Resolved `dsn` | То же соединение под API write guard |
| SQLite, `read_only: true`, без `readonly_dsn` | Resolved `dsn` | Derived `file:<resolved dsn>?mode=ro`, затем API write guard |
| SQLite, явный `readonly_dsn` | Resolved `dsn` | Явный `readonly_dsn` имеет приоритет |
| PostgreSQL | Resolved `dsn` | Явный `readonly_dsn`, если он задан; SQLite derivation не применяется |

SQLite adapter распознаёт `mode=ro`/`immutable=1` и добавляет только безопасный `busy_timeout`; он **не добавляет `journal_mode=WAL`** на database-level read-only path. `:memory:` остаётся без изменения: для него не существует file-backed read-only URI.

Regression `TestTenantAdmin_AddTenant_ReadOnlySQLite_AutoDatabaseLevelDSN` вызывает реальный `POST /admin/tenants` с обычным SQLite path и `read_only: true`, проверяет успешный query и доказывает, что прямой write через `ReadonlyConn` отвергается. Это закрывает gap прежнего unit coverage, где `file:?mode=ro` создавался вручную.

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
**Last verified:** 2026-08-09 (HEAD `be9a991`) — жизненный цикл тенантов сверен с кодом
