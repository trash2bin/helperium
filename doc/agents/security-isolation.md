# Security & Tenant Isolation

Изоляция на трёх уровнях (см. также [раздел 2f в AGENTS.md](../../AGENTS.md) — кратко).

## Data-service level
`TenantStore` — изолированные конфиги и подключения для каждого tenant'а. `X-Tenant-ID` → выбор пула. Физическая изоляция (отдельный SQLite файл или PG схема/БД).

**Write-tool approval:** read_only: true по умолчанию. Активация через `POST /admin/tools/{toolName}/approve`.

**resolvePath() баг-фикс:** для `postgres://` DSN не склеивать с путём (см. `tenant.go:resolvePath()`).

## mcp-gateway level
Tools регистрируются с tenantID в closure. Составные имена: `tenant-a__list_students`. Даже с мульти-tenant SSE запрос идёт строго к data-service своего tenant'а.

## api-service level
`tenant_ids: list[str]` передаётся web → orchestrator → MCPClient.

## Admin Dashboard level (RBAC)
- **admin** (`ADMIN_TOKEN`) — полный CRUD
- **viewer** (`VIEWER_TOKEN`) — только GET, POST/PUT/DELETE → 403

Публичные пути без auth: `/health`, `/api/health`, статика `/`.

## Верификация
- `pytest tests/e2e/test_data_isolation.py -v` — data-level
- `pytest tests/e2e/test_mcp_dynamic.py -v` — tool-level
- `pytest tests/e2e/test_mcp_composite.py -v` — composite routing
