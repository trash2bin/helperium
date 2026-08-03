# Tenant config schema

Human-readable reference for the JSON config that drives `data-service`.
Current version: **4** (`config.CurrentConfigVersion`).

Validation lives in Go types (`helperium-go/config/types.go` — `Config.Validate()`).
This file is documentation only.

---

## Top-level structure

```jsonc
{
  "version": 4,                          // schema version, Normalize() upgrades old ones
  "meta": {                              // who/when generated this config
    "config_version": 4,
    "generated_at": "2026-07-11T12:00:00Z",
    "generator_version": "1.2.0"
  },
  "data_source": {                       // client DB connection (required)
    "driver": "sqlite|postgres",
    "dsn": "path or connection string",
    "pool_size": 10,                     // max connections, optional
    "read_only": true,                   // default true
    "readonly_dsn": "..."                 // separate read-only user for agent (optional)
  },
  "introspection": {                     // auto-discovery settings (optional)
    "enabled": true,
    "include_schemas": ["public"],        // Postgres only
    "exclude_tables": ["^audit_"]
  },
  "entities": [/* see below */],         // table → API mapping
  "endpoints": [/* see below */],        // published REST endpoints
  "custom_queries": {},                  // whitelist SQL for escape-hatch
  "stats": { "counters": [] },           // counters for /stats
  "mcp_tools": [/* see below */],        // MCP tool definitions
  "llm_tool_policy": {                   // MCP-эмиссия для LLM (optional, Фаза 2)
    "expose_get_by_id": false,           // get_* per-entity (default false — анти-перебор)
    "expose_count": false,
    "expose_distinct": false
  },
  "auth": {/* see below */},             // multi-tenancy isolation (optional)
  "server": {                            // HTTP limits (optional, env overrides)
    "request_timeout_seconds": 30,
    "body_limit_mb": 10,
    "max_concurrent": 100
  },
  "skip_rules": [],                      // tables to exclude from generation
  "disabled_default_rules": [],          // default skip rules to disable
  "display_prefixes": [],                // table name prefixes to strip in display names
  "custom_plurals": {}                   // plural overrides for tool naming
}
```

All fields except `data_source` are optional. Missing optional fields are
treated as safe defaults by the runtime.

---

## data_source

```jsonc
{
  "driver": "sqlite",        // "sqlite" | "postgres"
  "dsn": "university.db",    // file path or connection string, supports ${ENV} subst
  "pool_size": 10,           // optional, default varies by driver
  "read_only": true,         // default true — write disabled at app level
  "readonly_dsn": "..."      // DB-level read-only user (optional, overrides app-level)
}
```

Env substitution works in any string field:

```jsonc
"dsn": "postgres://user:${DB_PASSWORD}@host:5432/db?sslmode=require"
```

---

## entities[]

One entity per database table. Maps table columns to public API fields.

```jsonc
{
  "name": "student",                    // public name (snake_case, used in API paths)
  "table": "students",                  // real table name in DB
  "id_column": "id",                    // primary key column
  "description": "A student record",    // optional, used in MCP tool descriptions

  "fields": [
    {
      "name": "full_name",             // public field name
      "column": "name",                // DB column name
      "type": "string",                // see FieldType enum below
      "nullable": false,               // optional, default false
      "primary_key": false,            // optional, default false
      "description": "Full name"       // optional
    }
  ],

  "relations": [                      // optional, documentation only (no auto-JOINs)
    {
      "field": "group",
      "kind": "many_to_one",          // many_to_one | one_to_many | many_to_many
      "table": "groups",
      "local_fk": "group_id",
      "target_fk": "",                // optional, for many_to_many
      "junction_table": ""            // required for many_to_many
    }
  ]
}
```

Field types: `string | int | float | bool | json | datetime | date`

**Relations are documentation, not JOINs.** JOINs go through `custom_queries`.

---

## endpoints[]

```jsonc
{
  "method": "GET",                     // GET | POST | PUT | PATCH | DELETE
  "path": "/students/{id}",            // supports {param} placeholders
  "op": "get_by_id",                   // operation type
  "entity": "student",                 // entity name (required for get_by_id, strategy)
  "strategy": "schema",               // search strategy: grep | filter | schema (required for strategy)
  "query_id": "student_grades",        // custom_queries key (required for custom_query)
  "description": "Returns a student by ID",
  "params": [
    {
      "name": "limit",
      "in": "query",                   // path | query | body
      "type": "int",                   // string | int | float | bool
      "array_of": "int",               // for array params (optional)
      "enum_values": ["a", "b"],       // for enum params (optional)
      "required": false,
      "description": "Max records"
    }
  ]
}
```

| op | What it does | Requires |
|----|-------------|----------|
| `builtin_health` | `/health` — service + DB status | — |
| `builtin_stats` | `/stats` — counters per entity | `stats` block |
| `get_by_id` | Record by PK (`GET /{entity}/{id}`) | `entity` |
| `strategy` | Strategy-based endpoint (grep/filter/schema) | `entity`, `strategy` |
| `distinct` | Distinct values of a field | `entity` |
| `count` | Count of records | `entity` |
| `custom_query` | Whitelist SQL from `custom_queries[key]` | `query_id` |

---

## custom_queries{}

Only place where arbitrary SQL appears. Strictly controlled.

```jsonc
{
  "student_grades": {
    "sql": "SELECT g.id, g.grade FROM grades g WHERE g.student_id = ?",
    "params": ["student_id"],             // names match ? placeholders in order
    "result_mapping": {                    // required for every result column
      "id":    { "type": "string" },
      "grade": { "type": "string" }
    },
    "max_rows": 500,                       // 1..10000, hard limit
    "description": "Grades for a student"
  }
}
```

Rules:
- Only `SELECT` (enforced by regex `^\s*SELECT\b`)
- No `;` (multi-statement)
- `?` placeholders for all user input
- `max_rows` required (1..10000)
- `result_mapping` required for every column

Adapters translate `?` to native dialect (`?` for SQLite, `$1` for Postgres).

---

## mcp_tools[]

MCP tool definitions for the AI agent. Typically auto-generated by `configgen`,
but can be overridden.

```jsonc
{
  "name": "find_student",              // tool name (snake_case)
  "display_name": "Find Student",      // UI label (optional)
  "endpoint": "/students",             // must match an endpoints[].path
  "description": "Search students by name. Used when the user asks about a student.",
  "params": []                         // same shape as endpoints[].params
}
```

`mcp_tools[].endpoint` must reference an existing `endpoints[].path`.

---

## auth{}

Multi-tenancy isolation. Optional. Without it — no row-level filtering.

```jsonc
{
  "strategy": "header",                // "none" | "header"
  "tenant_header": "X-Tenant-ID",      // header name for tenant ID
  "row_filters": [                     // added as AND (column = :tenant_id)
    { "entity": "customer", "where": "tenant_id = :tenant_id" }
  ]
}
```

---

## Config lifecycle

```
Write path:
  POST /admin/config/rewrite → configgen.Generate() → SaveTenantConfig()
  POST /admin/tenants        → inline JSON → Validate() → SaveTenantConfig()

Read path on startup:
  Load(path):
    1. os.ReadFile(path)
    2. Envsubst(raw, os.LookupEnv)
    3. json.Unmarshal → Config struct
    4. Normalize()   → upgrades old versions to current
    5. Validate()    → checks types, enums, cross-references
    6. → chi.Router  → REST handlers → DB
```

See `doc/agents/config-migration.md` for schema versioning and migrations.
See `doc/agents/tenant-lifecycle.md` for creation/deletion/persistence.
