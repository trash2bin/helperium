# Config Schema Migration

> **Who this is for:** developers adding or changing fields in the tenant config
> JSON schema (`services/helperium-go/config/types.go`).
>
> **Core principle:** every historical config on disk must continue to load
> without manual intervention. The loader (`config.Load()`) detects the schema
> version and upgrades it transparently.

---

## Table of Contents

1. [What gets migrated](#1-what-gets-migrated)
2. [Migration architecture](#2-migration-architecture)
3. [How to add a new migration step](#3-how-to-add-a-new-migration-step)
4. [Worked example: v1 → v2](#4-worked-example-v1--v2)
5. [Testing migrations](#5-testing-migrations)
6. [Rules & pitfalls](#6-rules--pitfalls)
7. [Reference: config version map](#7-reference-config-version-map)

---

## 1. What gets migrated

The only persistent configuration that outlives a deployment is the
tenant config file — `data_source.dsn`, `entities[]`, `endpoints[]`,
`mcp_tools[]`, etc.

| Data | Format | Has migrations? |
|------|--------|----------------|
| `.data/tenants/*.json` (tenant config) | JSON / Config struct | ✅ **Yes** — `Normalize()` |
| `.data/providers.json` (LLM providers) | JSON | ❌ No — replaced via admin UI |
| `.env` | env vars | ❌ No — new vars added over time |

---

## 2. Migration architecture

### Pipeline

```
config.json on disk
    │
    ▼
config.Load(path)                    [helperium-go/config/loader.go]
    │
    ├─ 1. os.ReadFile(path)
    ├─ 2. Envsubst(raw, os.LookupEnv)
    ├─ 3. json.Unmarshal(envsubsted, &cfg)
    ├─ 4. cfg.Normalize()            ← MIGRATION STEP
    └─ 5. cfg.Validate()             ← semantic check on final shape
```

`Normalize()` and `Validate()` live in the config struct:

```
helperium-go/config/
├── types.go            # Config struct, Validate(), all enum types
├── loader.go           # Load() — the pipeline above
├── migration.go        # Normalize(), CurrentConfigVersion, ConfigMeta
├── validate.go         # Validate(rawJSON) — convenience wrapper for admin API
├── filter_validation_test.go
├── loader_test.go
├── mcp_loader_test.go
└── migration_test.go   # migration-specific tests
```

### Version chain

Schema versions are chained: `v0 → v1 → v2 → … → CurrentConfigVersion`.
Each step handles only its own delta and bumps the version once.

```go
const CurrentConfigVersion = 4   // always the latest

func (c *Config) Normalize() {
    if c.Version == 0 {
        c.Version = 1
    }
    for c.Version < CurrentConfigVersion {
        switch c.Version {
        case 1:
            c.normalizeV1ToV2()
        case 2:
            c.normalizeV2ToV3()
        case 3:
            c.normalizeV3ToV4()
        default:
            c.Version = CurrentConfigVersion
        }
    }
}
```

### Idempotency

Calling `Normalize()` twice on the same config produces the same result.
Each step checks whether its transformation has already been applied before
modifying fields.

### Backward compatibility of `Validate()`

To keep tests simple, `Validate()` calls `Normalize()` internally as a safety
net. This means calling `Validate()` on a v1 JSON file will silently upgrade it
to v2 and then validate the v2 shape.

---

## 3. How to add a new migration step

This is the recipe for when you need to add a field, rename a field, or change
an enum — and existing configs on disk must keep working.

### Step A — Update the struct

Add your new field to the relevant struct in `types.go`:

```go
type Entity struct {
    // ... existing fields ...

    // NewField — added in v3. Describes XYZ.
    // Empty/nil means "not set" — old configs without it keep working.
    NewField string `json:"new_field,omitempty"`
}
```

Use `omitempty` for backward compatibility: old JSON won't have the field,
and that's fine.

### Step B — Write the migration function

In `migration.go`, add a new private method:

```go
func (c *Config) normalizeV2ToV3() {
    // 1. Guard: skip if already applied.
    for ei := range c.Entities {
        if c.Entities[ei].NewField != "" {
            return // already migrated
        }
    }

    // 2. Transform: backfill safe defaults for old configs.
    for ei := range c.Entities {
        // e.g., c.Entities[ei].NewField = defaultFrom(c.Entities[ei])
    }

    // 3. Bump version.
    c.Version = 3
}
```

### Step C — Wire it into the chain

```go
const CurrentConfigVersion = 3

func (c *Config) Normalize() {
    if c.Version == 0 {
        c.Version = 1
    }
    for c.Version < CurrentConfigVersion {
        switch c.Version {
        case 1:
            c.normalizeV1ToV2()
        case 2:
            c.normalizeV2ToV3()   // ← new
        default:
            c.Version = CurrentConfigVersion
        }
    }
}
```

### Step D — Update Validate()

If your new field has constraints (required, enum validation, cross-reference),
add them to `Config.Validate()`:

```go
// inside the entities loop
if e.NewField != "" && !isValidValue(e.NewField) {
    errs = append(errs, fmt.Sprintf("entities[%d].new_field: invalid", i))
}
```

### Step E — Update configgen (if applicable)

If the field should be auto-generated during `POST /admin/config/rewrite`,
update `services/data-service/internal/configgen/configgen.go`:

```go
result := &config.Config{
    Version:    config.CurrentConfigVersion,
    // ... populate new field from introspected schema ...
}
```

### Step F — Add tests

See [§5 — Testing migrations](#5-testing-migrations).

---

## 4. Worked example: v1 → v2

This is the migration we implemented live. It illustrates every piece of
the pipeline.

### What changed

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Added `Meta` block | Track when/which version generated the config |
| 2 | ~~`ApprovedTools` `[]string` → `[]ApprovedTool`~~ | Удалено в 2026-08-02 — write-tool approval выпилен из кода/UI/доков |
| 3 | `Relation.JunctionTable` | Required for `many_to_many` (the old struct had no way to specify the junction table) |
| 4 | `EndpointParam.ArrayOf`, `EndpointParam.EnumValues` | Better JSON Schema for MCP tool parameters |
| 5 | `Version` check in `Validate()`: `== 1` → `== CurrentConfigVersion` | No more hardcoded `== 1` |
| 6 | `Validate()` no longer mutates `Version` | Side-effect-free validation; normalization is `Normalize()`'s job |

### Migration function

```go
// normalizeV1ToV2 upgrades v1 → v2 configs.
func (c *Config) normalizeV1ToV2() {
    // 1. Backfill Meta
    if c.Meta == nil {
        c.Meta = &ConfigMeta{ConfigVersion: 2}
    }
    c.Meta.ConfigVersion = 2

    // 2. Bump version
    c.Version = 2
}
```

That's it for the migration itself. The heavy lifting is done by:

- **`omitempty`** on every new field — old configs without them parse fine.

### Configgen changes

`services/data-service/internal/configgen/configgen.go` — `Generate()` now produces:

```go
result := &config.Config{
    Version:    config.CurrentConfigVersion,
    DataSource: cfg.DataSource,
    Meta: &config.ConfigMeta{
        ConfigVersion:    config.CurrentConfigVersion,
        GeneratedAt:      time.Now().UTC().Format(time.RFC3339),
        GeneratorVersion: "", // filled by build system
    },
}
```

### Post-migration config shape (v2 — исторический пример; текущая версия схемы — 4)

```json
{
  "version": 2,
  "meta": {
    "config_version": 2,
    "generated_at": "2026-07-11T12:00:00Z",
    "generator_version": "1.2.0"
  },
  "data_source": {
    "driver": "sqlite",
    "dsn": "university.db"
  },
  "entities": [
    {
      "name": "student",
      "table": "students",
      "id_column": "id",
      "fields": [
        { "name": "id", "column": "id", "type": "string", "nullable": false, "primary_key": true }
      ],
      "relations": [
        {
          "field": "group",
          "kind": "many_to_one",
          "table": "groups",
          "local_fk": "group_id"
        }
      ]
    }
  ]
}
```

### How the migration was verified

```
# 1. Fresh configs (version=2) load fine:
go test ./config/... -run TestValidate_V2Config

# 2. Old configs (version=1) get auto-upgraded:
go test ./config/... -run TestNormalize_V1toV2

# 3. Configs with no version field (version=0) also work:
go test ./config/... -run TestNormalize_VersionFromZero

# 4. All existing tests still pass:
go test ./config/...
go test ./data-service/...
go test ./mcp-gateway/...
go build ./admin-dashboard/...
```

---

## 4a. New field: `endpoints[].strategy` (version 2, no migration needed)

The `strategy` field was added to `Endpoint` without bumping the config version.
No migration step was required because:

### What changed

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Added `Strategy string \`json:\"strategy,omitempty\"\`` to `Endpoint` | `omitempty` — old configs without it parse fine |
| 2 | `Validate()` adjusted: `ep.Op == OpFind && ep.SearchField == ""` → `&& ep.Strategy == ""` (исторически; сейчас `OpFind` удалён из v4 — `types.go` whitelist) | Strategy-based endpoints don't need `search_field` |

### How it works

```go
type Endpoint struct {
    // ... existing fields ...

    // Strategy — имя search strategy ("grep", "filter", "schema").
    // Если пусто — используется Op-based routing (legacy).
    Strategy string `json:"strategy,omitempty"`
}
```

### Accepted strategy values (v4)

| Value | Handler | Description |
|-------|---------|-------------|
| `"grep"` | `search.NewGrepStrategy()` | Multi-token AND, multi-field OR, regex, ignore_case, invert |
| `"filter"` | `search.NewFilterStrategy()` | Field-based c компараторами `field__gt`, `field__like`, `field__in` |
| `"schema"` | `search.NewSchemaStrategy()` | Discovery: мета-информация о сущности (distinct, min/max, count) |

**v4 changes:** `search` и `simple` стратегии удалены. **Фаза 2/2.5 (LLM-поверхность):** консолидированные `db_*` (`db_map`/`db_describe`/`db_search`/`db_get`/`db_related` через `/q/*`) + пер-энтити `filter_{entity}`. Текстовый поиск — `db_search`, точная фильтрация — `filter_{entity}` (поля в схеме тула). `grep_{entity}`/`schema_{entity}` как MCP-тулы не эмитятся.

### Routing logic (endpoint_builder.go)

```
if ep.Strategy != "" {
    // Strategy-based routing (takes precedence over Op)
    entityConfig := entityMap[ep.Entity]
    strategy := strategyFromConfig(ep.Strategy, entityConfig)
    handler = NewStrategyHandler(ctx, strategy, ep.Entity, entityConfig)
} else {
    // Legacy Op-based routing (find, list, get_by_id, count...)
    switch ep.Op { ... }
}
```

### MCP tool generation (mcp.go)

- Strategy endpoints получают MCP-тулы через `strategyToMCPTool()`.
- Сама стратегия генерирует `ToolName()`, `ToolDescription()`, `ToolParams()`.
- Для entity со strategy: `find_*`, `list_*`, и relationship custom queries (`products_by_category`) **скипаются** — их заменяют `grep_*`, `filter_*`, `get_*`, `count_*`, `distinct_*`, `schema_*`.

### Backward compatibility

- Старые конфиги без `strategy` работают как legacy (Op-based routing).
- Поле опциональное, не участвует в `Normalize()`.
- `ConfigVersion` остаётся `2`.

## 5. Testing migrations

### Required test cases

Every migration step needs at least these tests:

| Test | What it checks |
|------|---------------|
| `TestNormalize_V{from}ToV{to}` | A config at version `from` reaches version `to` with correct fields |
| `TestNormalize_VersionFromZero` | A config with no version field (Go zero-value) survives the chain |
| `TestNormalize_NormalizeTwiceIsIdempotent` | Calling `Normalize()` twice gives the same result |
| `TestValidate_V{to}Config` | A valid config at the new version passes `Validate()` |
| `TestValidate_V{to}Config_Invalid{Field}` | Invalid values in new fields are rejected |

### How to write them

Use the `writeTempConfig()` helper in `mcp_loader_test.go`:

```go
func TestNormalize_V2ToV3(t *testing.T) {
    path := writeTempConfig(t, `{
        "version": 2,
        "data_source": { "driver": "sqlite", "dsn": ":memory:" },
        ... your config data ...
    }`)

    cfg, err := config.Load(path)
    if err != nil {
        t.Fatalf("Load() returned error: %v", err)
    }

    if cfg.Version != config.CurrentConfigVersion {
        t.Errorf("Version = %d, want %d", cfg.Version, config.CurrentConfigVersion)
    }

    // Assert new fields are populated correctly
    if cfg.Entities[0].NewField != "expected" {
        t.Errorf("NewField = %q, want %q", cfg.Entities[0].NewField, "expected")
    }
}
```

All migration tests live in `services/helperium-go/config/migration_test.go`.

### Running migration tests

```bash
# All config tests (includes migration tests):
cd helperium && go test ./helperium-go/config/... -v -count=1

# Just migration tests:
go test ./helperium-go/config/... -run 'TestNormalize|TestValidate_V2' -v
# прим.: теста TestApproved больше нет — write-tool approval удалён (коммит b17b910)
```

---

## 6. Rules & pitfalls

### DO

- **Add fields with `omitempty` tag** — old configs without them must parse.
- **Make `Normalize()` idempotent** — check if the migration already ran.
- **Write a test for every new version step** — regeneration test + validation test.
- **Update `configgen.go`** — new versions of the generator should produce the
  latest schema version.
- **Update `specs/config.example.json`** — it should reflect the current schema.

### DON'T

- **Don't remove fields** — old configs may still have them. `Unmarshal` ignores
  unknown JSON keys in Go; JSON marshal can use `omitempty`. If you must remove,
  migrate the field to a new location and leave the old one readable for one
  cycle.
- **Don't change existing field types** — `string` → `[]string` breaks
  `Unmarshal` silently. Use a custom `UnmarshalJSON` when needed.
- **Don't mutate `Version` inside `Validate()`** — normalization belongs in
  `Normalize()`. `Validate()` should only check the final shape.

### How to avoid breaking production

```bash
# 1. Full test suite before deploy
go test ./helperium-go/config/... ./data-service/... ./mcp-gateway/...
go build ./admin-dashboard/...

# 2. Smoke-test with an old config on staging
go run ./data-service/cmd/server/ --config specs/config.example.json

# 3. Check that existing .data/tenants/*.json files load
# (run data-service with an old tenants dir)
```

---

## 7. Reference: config version map

| Version | Key changes | Added fields | File |
|---------|-------------|-------------|------|
| 0 | Pre-history — no version field | — | — |
| 1 | First explicit version | `version: 1` | `types.go` (original) |
| 2 | **Current** (until strategy addition). Meta block, JunctionTable, ArrayOf, EnumValues | `meta`, `junction_table`, `array_of`, `enum_values` | `migration.go` |
| 2 (post-strategy → v4) | Same version (`2`), no migration needed. `endpoints[].strategy` added with `omitempty`. v4: only `"grep"`, `"filter"`, `"schema"` are valid; `"search"` and `"simple"` removed. | `endpoints[].strategy` ("grep", "filter", "schema") | `types.go` |

### Where each version is produced

| Source | Version |
|--------|---------|
| `configgen.Generate()` | **4** (`config.CurrentConfigVersion` = 4) — produces `grep`, `filter`, `schema` endpoints with `strategy` |
| Hand-written `specs/config.example.json` | **2** (устарел — должен быть обновлён до 4; `Normalize()` молча апгрейдит, но для примера лучше держать актуальный) |
| Old `.data/tenants/*.json` on disk | 0–3 — auto-upgraded by `Normalize()` |

### Key files

| File | Purpose |
|------|---------|
| `services/helperium-go/config/migration.go` | `Normalize()`, version chain, `ConfigMeta` |
| `services/helperium-go/config/types.go` | All config types, `Validate()`, `String()` |
| `services/helperium-go/config/loader.go` | `Load()` — the Normalize → Validate pipeline |
| `services/helperium-go/config/validate.go` | `Validate(rawJSON)` — convenience for admin API |
| `services/helperium-go/config/migration_test.go` | Migration tests |
| `services/data-service/internal/configgen/configgen.go` | Config generator (produces latest version) — now emits `grep`, `filter`, `schema` endpoints with `strategy` |
| `services/data-service/internal/configgen/mcp.go` | `GenerateMCPTools()` — generates MCP tools from strategy endpoints using `Strategy.ToolParams()` |
| `services/data-service/internal/server/endpoint_builder.go` | Strategy-based HTTP routing — uses `ep.Strategy` to construct search handlers |
| `specs/config.example.json` | Example config (kept at latest version) |
| `specs/config.schema.md` | Human-readable format reference |
| `doc/agents/tenant-lifecycle.md` | How configs are created and persisted |
| `doc/agents/search-strategies.md` | Detailed description of each strategy (grep, filter, schema) |
---
**Last verified:** 2026-08-09 (HEAD `be9a991`) — миграция конфигов и стратегии сверены с кодом
