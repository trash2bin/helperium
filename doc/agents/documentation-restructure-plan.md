# Documentation Restructure Plan

**Status:** Proposed
**Target:** `README.md`, `README_RU.md`, `services/*/README.md`, `doc/agents/*.md`
**Goal:** Reduce doc volume ~30–40%, eliminate signal-to-noise ratio problems, and make current state discoverable without wading through implementation history.

---

## P0 — Structural (What / TL;DR / Reference)

### TL;DR + Reference split

Every long README gets a 30–40 line header:

```
## TL;DR
- Service purpose in one sentence
- Port / transport / auth model
- Quick start (one command or one curl)
- Where to go next (3–4 links with anchors)

## Reference
- Links to split files: proposed layout `reference/` (tools, config), etc. — создаётся только при выполнении split.
- "For historical context" links go to `doc/agents/*.md` or `CHANGELOG.md`, not inline
```

Everything that is:
- exhaustive endpoint tables without contract nuance
- per-field strategy limits that change between commits
- deep implementation callouts (`stats.go:36`, `types.go:50`)

…moves to `reference/*.md` or stays in `doc/agents/` if it's narrative. The main README only references it.

**Affected files (candidates):**
- `services/data-service/README.md` — biggest win; extract limits table, FieldRules deep-dive, pagination internals
- `services/api-service/README.md` — extract MCP v2 transport details, abuse config model
- `services/mcp-gateway/README.md` — session management details → вынести в отдельный reference-файл при split
- `README_RU.md` — mirror TL;DR structure, then link to `services/*/README.md` instead of duplicating

---

## P1 — Single Source of Truth (Синхронизация surface)

### Stop describing the MCP tool surface in 6 files

Current authoritative source: **`services/data-service/README.md`** (already the most precise).

All others become pointers:

| File | Change |
|---|---|
| `services/data-service/internal/configgen/README.md` | Replace "5 db_*" list with: `См. services/data-service/README.md (MCP-тулы). Здесь — только FieldRules и генерация.` |
| `services/mcp-gateway/README.md` | Remove duplicate tool table; keep one sentence + link. |
| `doc/agents/search-strategies.md` | Replace tool list with: `Тул-сёрфейс описан centrally в services/data-service/README.md; здесь — только стратегии и почему filter пер-энтити.` |
| `README_RU.md` | Replace duplicated tool enumeration with link + one-line summary. |
| `services/api-service/README.md` | MCP v2 section already correct; just add `См. services/data-service/README.md` for the full tool list. |

**Rule:** If a concept is described in `data-service/README` with anchors, no other file re-defines it. Cross-links use Markdown anchors (`#mcp-тулы`).

---

## P2 — Kill Dead Line-References (code anchors)

Any line number that refers to a utility/helper or a file that moved more than once gets stripped. Only **contract anchors** survive:

| Keep | Drop (examples) |
|---|---|
| `endpoint_builder.go:147` — read-only guard (security contract) | `types.go:326` → `internal/runtime/types.go:50` (FindColumn — implementation detail) |
| `q_dispatch.go:223` — `/q/*` routing | `strategy_common.go:89-98` — offset parsing (internal) |
| `mcp_manifest.go:20` — runtime generation | `response_mapper.go:210` — coercion logic |
| `tenant.go:85` — two-phase drain | `navigation.go:110` — MaxRows=1000 (moves to config reference) |

**Mechanism:** If a line reference isn't the *first place* a newcomer should look to understand the behavior, it doesn't belong in prose.

---

## P3 — Archive Hygiene (evidence vs. current docs)

### Relocate audit/incident snapshots

Files like `doc/archive/*-audit-*.md`, `doc/archive/*-plan-*.md`, and `doc/archive/*incident*.md` are **dated evidence**, not living documentation.

Two options:

**Option A — Soft (low effort):**
- Add YAML front-matter to each: `archive: true`, `as_of: "2026-08-18"`, `superseded_by: doc/archive/...`
- Add a header banner: `> [ARCHIVE] As of 2026-08-18. For current state see services/data-service/README.md`
- Keep them in `doc/archive/` for traceability

**Option B — Hard (recommended):**
- Move all audit/plan/incident snapshots from `doc/agents/` and `doc/benchmark/` into `doc/archive/`, prefixing each with its date (`YYYY-MM-DD-slug`).
- Keep only links in `doc/agents/` (if needed at all)
- Living docs (`README.md`, `search-strategies.md`) cite the archive by slug, never inline

**Why this matters:** Archives contain stale claims (`5 db_*`, `readPagination`, `parseOffset без cap`). When someone grep's the repo and finds those files, they copy-paste into living docs. The chain of staleness starts there.

---

## P4 — What / How / Why Restructure (narrative hygiene)

### Pattern to apply to each major section

Current (bad):
```
TenantFilter из auth.RowFilters; /stats (stats.go:36) и count (count.go:37-42) тоже применяют tenant-фильтр / исключают tenant_id.
```

Target (good):
```
### What
`tenantFilter` injects `WHERE tenant_id = :tenant_id` into every query unless auth is disabled or RowFilters are absent.

### Contract
- Applies to: grep, filter, get_by_id, count, stats, related
- Fails: 403 with `missing_row_filter` if header auth is on but RowFilters are empty
- Opt-out: `AuthStrategyNone` (not recommended for multi-tenant)

### Implementation notes
See `internal/runtime/handlers/row_filter.go`. The filter is resolved per-request in `strategy_handler.go` and `stats.go:36`.
```

**Rule:** Prose describes observable behavior and contracts. Implementation details stay in linked code.

### Kill "раньше... теперь..." from READMEs

If a diff is interesting historically, it belongs in:
- `CHANGELOG.md` (one-line per commit)
- `doc/archive/*.md` (full narrative)

README tells you how the system works *today*.

---

## P5 — Language & Register Consistency

### Problematics

- Russian prose with English code identifiers in the same clause: `"tenantFilter из auth.RowFilters; /stats (stats.go:36)"`
- Mixed terminology: `tenant-изоляции` vs `isolation invariant` vs `tenant isolation`
- Em-dashes used as commas (`—`) create walls-of-text in security sections

### Rules

1. **Sentences are Russian, identifiers are English.** Period. No mixing in one clause.
2. **Pick one register per section:**
   - `services/*/README.md` — professional-technical (оператор/админ)
   - `doc/agents/*.md` — architectural (инженер)
   - `README.md` / `README_RU.md` — product overview (anyone)
3. **Security contracts are bullets, not prose.** No em-dash paragraphs for auth/model behavior.

---

## P6 — OpenSpec (deferred decision)

### Current state

- OpenAPI specs exist per service (`/openapi.json`)
- MCP tool surface is documented in Markdown + generated JSON manifest
- `doc/agents/api-contracts.md` captures the intentional boundaries

### Open question

Does the project need a **formal MCP tool specification** (beyond Markdown)?

**Arguments for keeping Markdown:**
- Tool surface is small and stable
- Already tightly coupled to Go runtime generation
- Adding JSON Schema / OpenAPI extra layer creates duplication without consumer benefit

**Arguments for adding spec:**
- If `api-service` and `mcp-gateway` are to be consumed by *third parties* (not just internal), a spec file is a contract
- If tool generation moves to a separate repo or becomes plugin-based

### Recommendation

**Defer until:**
- Multi-repo consumption is real (not hypothetical)
- Or tool generation is extracted from `configgen` into a shared package

If/when that happens, generate Markdown docs *from* the spec (single source), not vice versa.

---

## Execution Order

| Phase | Effort | Impact | Notes |
|---|---|---|---|
| P0 — TL;DR split | 2–3h | High | Immediate readability win; no content risk |
| P1 — Single source of truth | 1–2h | High | Mechanical find/replace + link checks |
| P2 — Dead line-refs | 1h | Medium | Mostly deletions; run `rg "types.go:326"` first to find all |
| P3 — Archive hygiene | 2h | Low-medium | Mostly file moves + front-matter; no content edits |
| P4 — What/How/Why | 3–4h | High | Rewrites; needs 1–2 doc reviewers |
| P5 — Register cleanup | 1–2h | Medium | Find/replace + read-through |
| P6 — OpenSpec | n/a | TBD | Deferred |

**Suggested order:** P0 → P1 → P2 → P5, then P4, then P3. P6 parked.

---

## Success Criteria

- `data-service/README.md` under 250 lines (currently ~400)
- No file describes `db_filter` existence more than once (source of truth)
- Zero `types.go:XXX` references in `services/*/README.md`
- All `*-audit-*.md` either have `[ARCHIVE]` banner or live under `doc/archive/`
- New contributor can answer "what does data-service do?" from TL;DR alone in under 2 minutes

---

## Next Step

Confirm the plan, then:
1. Start with `services/data-service/README.md` (biggest impact, most duplicated content)
2. Or start with the archival files (lowest risk, unblocks P4 later)
