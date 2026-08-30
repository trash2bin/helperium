# Roadmap fix: post-P0 queue after direct chat hardening

**Status:** active planning artifact — fixed queue with explicit boundaries.

**Date:** 2026-08-30.
**Baseline:** `bfa16d3` (`fix(api): keep tool schemas on continuation for relayed deepseek`).

## Already closed — do not touch now

| Commit | Scope |
|---|---|
| `01d8458` | `?tenant=` query parameter removed as tenant authority (fail-closed 404). |
| `54b1c2f` | Direct chat quality pinning via `DIRECT_CHAT_AGENT` (Agent Store profile, quality-only). |
| `bfa16d3` | Tool schemas kept on continuation for deepseek models served through OpenAI-compatible relays (litellm `supports_function_calling` false-negative caused DSML markup in final text; broke every multi-hop tool cycle). |
| `97f716c` | Unit suite isolated from live runtime artifacts (`AGENT_DB_PATH`/`SPENDING_PERSISTENCE_PATH` fixture). |
| `1031402` | Native E2E launcher secure-profile contract repair. |

Also closed and not re-opened here: admin flows. Deferred to deployment phase: TLS/DSN hardening. The 20–30 question acceptance suite will be run by the agent's own benchmark harness.

## Fixed queue

| # | Task | Boundary notes |
|---:|---|---|
| 1 | Stale tenant recovery and manifest retry UX in demo web UI | Scope below; minimal frontend fix only. |
| 2 | Port ownership check in `dev.sh start` | Distinguish foreign process vs stale own PID before bind. Note: `dev.sh` was just modified by `1031402`. |
| 3 | Seed/reseed lifecycle | Remove wipe/reseed from public startup, make it an explicit command. Touches `demo/autoparts-store` — needs explicit owner permission for that directory. |
| 4 | Canonical demo docs and local dev defaults | Three READMEs diverge on DSN/TLS defaults; documentation-only plus `make ci-docs`. |
| 5 | ADR: composite spending | Blocks #6. Existing analysis in [deferred-decisions.md](deferred-decisions.md) §P1 (reserve/commit, named agent/account principal, not tenant ID). |
| 6 | Async spending persistence | Depends on #5. Sync fs write must leave the async completion path; concurrency tests required. |
| 7 | Circuit breaker | LLM/provider-level (an MCP circuit breaker already exists in api-service); build on FallbackProvider boundaries. |

Items #5–#7 are **study candidates** worth a closer look (per owner: "Async spending persistence" and "Circuit breaker" are the interesting ones), but no automatic transition between queue items: each starts only after the previous is merged and reviewed.

## Task 1 detailed scope (bounded "go")

Goal: one small frontend fix.

```text
stale tenant → понятная ошибка
404 manifest → без бессмысленных retry
5xx/network → bounded retry + Retry button
смена tenant → старые retry не могут победить новый state
```

### Requirements

- Only the demo web UI file(s) and isolated tests. Do not touch data-service, API contract, Docker, PostgreSQL, `.data`, `agents.sqlite`, autoparts-store, `dev.sh`, seed lifecycle, spending, circuit breaker, TLS.
- Keep the existing saved-tenant check via `/api/tenants`.
- No silent fallback to `default` when the tenant list itself is unavailable — show backend unavailable instead.
- Distinguish tenant-not-found (manifest 404: no retry) from temporary backend failure (5xx/network: bounded retry with exponential backoff, small attempt count).
- On a disappeared selected tenant: clear stale localStorage only when the tenant is genuinely absent from the fresh tenant list, select the first available tenant, reload manifest once.
- Exhausted retries → clear state with a Retry button (not "Refresh the page").
- Close the manifest retry race on tenant switch: generation/request token or another simple cancellation guard so an in-flight old retry loop can never render a manifest for the new tenant state (existing global `manifestRetries` counter is reset by `reloadForNewTenant()` while the old loop still sleeps in `setTimeout`, producing concurrent fetch cycles and cross-tenant render).
- Retry button resets the guard and restarts loading for the current tenant.
- Do NOT wire `checkHealth()` into this task (extra coupling, separate logic).
- No new architecture, no test framework. If a helper is needed for tests: a minimal pure module only, no UMD wrapper. If the logic can be tested without extraction, prefer no extraction.

### Regression coverage and checks

- Regression coverage for bootstrap/retry logic (minimal isolated testable helper only if necessary).
- Run existing demo web tests and `node --check` on the edited JS.
- Show diff and `git diff --check`. **No commit** until owner reviews the diff.

### Rejected expansions (do not do in this task)

- Any backend change to make 404 vs 5xx distinguishable: the existing proxy already passes upstream statuses through (`test_proxy_upstream_5xx_passes_through`, `test_proxy_upstream_404_passes_through`), and data-service answers unknown tenants with 404 fail-closed since `01d8458`.
- `checkHealth()` integration.
- Error-state unification of `#tableTitle`/metrics (cosmetic; noted, out of scope).

## Resolved — demo web UI source of truth

Owner confirmed `demo/web/static/app.js` is the source of truth edited directly (no TS source for the demo UI in this repo). Task 1 proceeds on it as-is. The repo TS UIs are admin-dashboard and embed only; the identically named admin `dist/app.js` is a different file.

### Confirmed defects (line refs at HEAD `bfa16d3`)

1. **Two concurrent manifest retry loops on tenant switch (real race).** `manifestRetries` is a global counter (line 40); `reloadForNewTenant()` resets it to 0 (line 354) while an old `loadManifest()` loop (124–141) still sleeps inside its `setTimeout(3000)` backoff. The sleeping iteration re-checks `while (manifestRetries < 10)` — now true again — so the old loop resumes: two concurrent fetch cycles. Worse, when the stale loop gets `response.ok` it calls `buildTabsFromManifest()` + `loadData()` with `X-Tenant-ID` of the OLD tenant: tabs and table can render the previous tenant's data while the select shows the new one. Also doubles the retry budget (2×10 requests).
2. **404 retried 10×3s = 30 s of guaranteed-futile waiting.** The loop does not inspect `response.status`; a deleted tenant yields a stable 404 (proxy passes statuses through — `test_proxy_upstream_404_passes_through`; data-service answers unknown tenants 404 fail-closed since `01d8458`). Retrying a permanent 404 is pointless; it is not a "backend warming up" case.
3. **Silent `default` fallback when `/api/tenants` itself fails** (lines 248–254): `catch` sets `state.tenants = ["default"]` and the select renders one healthy-looking option. Wrong class of error — must show backend-unavailable with Retry. Side effect: `setTenantId("default")` then persists `default` into `agentTutorTenantId`, silently destroying the user's saved tenant choice (lost even after backend recovery).
4. **Dead end after retries exhausted** (line 143): only text "Failed to load entities. Refresh the page." — but refresh restarts the same failing cycle; no Retry button, no reason shown; `#tableTitle` stays "Loading…" forever (set by `showTabPlaceholder()`, never cleared).
5. **Stale localStorage around the `/api/tenants` → `/api/manifest` race:** existing recovery (first available tenant when saved one absent from a *successfully fetched* list, lines 256–259) is fine, but if the tenant is deleted *between* the two calls the manifest 404 lands in the blind retry loop instead of an immediate re-fetch of the tenant list + stale cleanup + one manifest reload.

### Fix shape (minimal, no new architecture)

- Generation guard: `const gen = ++manifestGeneration` captured by `loadManifest()`; every `await` resumption checks `gen !== manifestGeneration → return`. `reloadForNewTenant()` and the Retry button just increment. Old loops die silently at the next await boundary.
- Error classification: 404 → no retry, tenant-not-found state (re-fetch tenant list, clear stale localStorage only if genuinely absent, select first available, one manifest reload). 5xx/network → bounded exponential backoff, small attempt count (e.g. 3 attempts, 1s→2s→4s, cap 8s). Exhausted → Retry-button state.
- `/api/tenants` failure → distinct backend-unavailable state with Retry; never fabricate `["default"]`.
- Retry button restarts the current phase (tenant list or manifest) under the current generation.
- Checks: `node --check` on edited JS, existing demo web pytest suite, regression tests for the pure helper; diff + `git diff --check`; **no commit** until owner reviews.

## Infrastructure note

Current live stack stays up (native ports 8080–8085, seeded `autoparts-db` container, colima running). Task 1 does not need it. Do not stop, clean or reseed anything as part of queue work.
