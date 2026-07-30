# Audit: api-service Architecture

> **Date:** 2026-07-28
> **Scope:** `api-service/src/api_service/` — agent pipeline, server layer, security, MCP lifecycle, provider resolution, async patterns
> **Files examined:** 35 source files across `agent/`, `server/`, and root modules

---

## 1. Pipeline Architecture — Stage/Protocol Design, Stop Conditions, Error Handling

### What's correct

- **Clean Stage protocol** (`pipeline.py:Stage`) — each stage is a standalone `AsyncIterator[AgentEvent]` producer. Stages are composed in `Pipeline` without knowing about each other.
- **Two-phase execution** — Phase 1 (main loop with LLMStage↔ToolExecutionStage alternation), Phase 2 (finalization: Fallback → GuardOutput → SaveHistory). This prevents final content from being produced mid-loop without guard checks.
- **`_done_flags`** for one-shot stages (GuardInput, ToolDiscovery, SaveHistory) — correct gating pattern that prevents re-execution across iterations.
- **`TurnContext`** consolidates all mutable turn state (messages, iteration, pending_calls, tool_results, final_content). Eliminates the old parameter-soup orchestrator.
- **Middleware chain** (`SpendingMiddleware`, `TokenBudgetMiddleware`) — each event passes through the middleware pipeline before being yielded. Blocking returns `None`.

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| **HIGH** | **No history save on exception path.** The orchestrator's `except` clause yields an error event and runs backlog telemetry in `finally`, but never calls `SaveHistoryStage.force_save()`. On any exception during pipeline execution, the entire turn's conversation history is lost. | `orchestrator.py:132-148` |
| MEDIUM | **`TokenBudgetMiddleware` fires only on `tool_result`/`final`/`error` events.** It never checks mid-stream token events, so a very long assistant response can exceed the budget before the middleware fires. By the time it fires, `ctx.should_stop = True` only prevents the *next* LLM call, not the ongoing one. | `middlewares.py:48-49` |
| LOW | **`MAX_TOOL_CALLS_PER_TURN = 10` is a loose limit for a multi-tenant SaaS.** Combined with the `had_tool_calls_this_iteration` logic (iteration not incremented on tool rounds), a conversation could theoretically run 10 tool calls in 1 "iteration." The `max_iterations` guard only limits non-tool rounds. | `pipeline.py:26` |
| LOW | **Stage docstrings claim "no gating" for LLMStage but pipeline still gates via should_stop.** Minor documentation decay — `LLMStage.run()` checks `if ctx.should_stop: return`, contradicting its own comment "LLMStage повторяется на каждой итерации — никакого gating." | `stages/llm.py:63-64` |

---

## 2. Error Handling — Coverage, classify_error Correctness, ErrorContext Usage

### What's correct

- **`classify_error()`** unwraps `ExceptionGroup` automatically and covers 7 categories: rate_limit, auth, context_length, connection, timeout, provider, mcp, internal.
- **Broad `except Exception`** in orchestrator catches any pipeline failure, classifies it, and returns a user-facing error event. The subsequent `finally` block always records telemetry.
- **`ErrorContext`** dataclass carries session_id, correlation_id, stage, iteration — structured metadata for logging beyond plain string messages.

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| **MEDIUM** | **`classify_error` substring matching can misclassify.** e.g., `"auth" in exc_str` matches `"database_auth_error"` (likely an MCP/data error) as an auth error. Also `"mcp"` in `exc_str` catches MCP-related OpenAI errors like `"model_context_protocol"` in error descriptions. Substring matching is fragile. | `error_messages.py:61-108` |
| MEDIUM | **`ErrorContext` adoption is incomplete.** Only `LLMStage._call_llm()` and `ToolExecutionStage` use `ctx.error_context.with_stage()`. `GuardInputStage`, `FallbackStage`, `SaveHistoryStage` do not use it. The `Extra` dict on log records has `"stage"` inconsistently populated. | `stages/llm.py:100`, `stages/tool_execution.py:83` |
| LOW | **`classify_error` has no tenant-specific messaging.** All errors return a generic message — "попробуйте позже" doesn't tell the tenant whether the problem is their DB connection (MCP), their API key (auth), or a global timeout. | `error_messages.py:40-50` |
| LOW | **No distinction between recoverable/unrecoverable errors.** The orchestrator catches everything with one blanket handler. MCP timeout vs. LLM auth failure vs. internal bug all get the same treatment — error event + abort. | `orchestrator.py:132-148` |

---

## 3. MCP Lifecycle — Reconnect, Timeout, Lock Strategy

### What's correct

- **Per-tenant SSE sessions** with composite mode support (comma-joined tenant IDs in `X-Tenant-ID` header).
- **Dual locks** (`call_lock` and `list_lock`) per connection — prevents `list_tools` from blocking `call_tool` and vice versa.
- **Two-phase timeout** — `LOCK_ACQUIRE_TIMEOUT` (10s) prevents deadlock on the per-connection lock; `TOOL_EXECUTION_TIMEOUT` (15s) limits actual tool execution.
- **Proactive reconnect at 240s idle** — sessions idle > 4 minutes are reconnected before use, preventing stale SSE connections from lingering.
- **Reconnect-on-error** — if `call_tool` or `list_tools` fails, a single reconnect+retry is attempted.
- **`list_lock` isolation** — `list_tools` uses its own lock, so a slow tool execution can't block tool listing.

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| **MEDIUM** | **No circuit breaker for persistent failures.** If mcp-gateway is down, every tool call triggers a full reconnect cycle (close old → open new → handshake → fail → close again). No backoff, no max-reconnect-per-minute tracking. A 5-minute gateway outage could generate hundreds of reconnect attempts. | `mcp_client.py:115-135` |
| MEDIUM | **No TTL-based session garbage collection.** `_connections` dict grows unboundedly. If 10,000 tenant sessions are opened and never reused, they hold SSE connections (file descriptors, HTTP connections) until `close()` or idle reconnect (240s). No background cleaner. | `mcp_client.py:68-70` |
| LOW | **Race: `_get_connection()` releases `_registry_lock` before caller uses the connection.** Between `_get_connection()` returning and the caller acquiring the call_lock, another coroutine could call `_reconnect()` and close the returned connection. Extremely unlikely in practice (narrow window) but lacks a formal guard. | `mcp_client.py:108-120` |
| LOW | **`sse_read_timeout=1800s`** (30 minutes) — appropriate for SSE, but there's no heartbeat/ping to detect dead connections earlier. If the gateway half-closes the connection, it could take 30 minutes to detect. | `mcp_client.py:86` |

---

## 4. Provider Resolution — 5-Level System, Race Conditions, Scripted Provider

### What's correct

- **Clean 5-level resolution** in `resolve_llm()`: Scripted → `llm_client` → `llm_config` → `provider_priority` → Pool/env fallback. Each level is tested and documented.
- **ScriptedLLMProvider** supports deterministic replay from JSONL files, recording mode, and latency simulation. Properly gated by `USE_SCRIPTED_LLM` env var.
- **ProviderPool** with `with_fallback` iterates through alive workers, skipping dead ones.
- **`_prefix_model()`** correctly handles LiteLLM provider/model prefixes.
- **Health checks** run at 30s intervals with 3-consecutive-failure threshold and 90s staleness limit.
- **API keys passed directly to LiteLLMProvider**, not injected into `os.environ` — avoids process-global race conditions.

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| **MEDIUM** | **TOCTOU race in `ProviderPool.pick()`.** `alive_workers()` acquires/releases `_lock`, then `pick()` re-acquires it. Between the two calls, the worker list can change. The dead/alive snapshot from `alive_workers()` is captured once and used for index calculation, so `_rr_index` may point past the actual list if a worker was removed. Mitigation: modulo against captured length prevents OOB. | `provider_pool.py:130-150` |
| LOW | **`resolve_llm()` creates a NEW provider every request** in the pool/env fallback path (`await _resolve_pool_or_env()`). This calls `_pool.get_any_worker()` which returns a reference to an existing worker. This is correct — no new HTTP clients created — but the docstring over-promises ("FRESH provider every request") vs reality (reuses pool workers). | `factory.py:112` |
| LOW | **`_create_env_provider()` first-found-wins for env vars.** If both `OPENAI_API_KEY` and `MISTRAL_API_KEY` are set, only the first one iterated (dict order) is used. No priority or user choice. | `factory.py:36-42` |
| LOW | **ProviderPool health check logs at DEBUG.** A worker going unhealthy is a significant event that should be visible at INFO level at least once. | `provider_pool.py:194` |

---

## 5. Security — Anti-Abuse, Guardrails, Spending Limits, Tenant Isolation

### What's correct

- **Two-layer guard** (input + output) with regex patterns for prompt injection detection and system prompt leak prevention. Configurable via env var and admin API.
- **TokenBucket** rate limiter per (session, IP, User-Agent) composite key — prevents IP-based bypass by hashing User-Agent into the key.
- **AntiAbuseChecker** with 5 checks: User-Agent, message length, inter-message interval, session budget, repeated text.
- **SpendingChecker** with per-tenant in-memory tracking, configurable budget, periodic (monthly) reset.
- **GuardOutputStage** mutates `final_content` to `[Ответ заблокирован системой безопасности]` on match — prevents credential/system prompt leaks to the user.
- **Session ID prefixing** (`direct:`, `agent:`) for tenant namespace isolation.

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| **HIGH** | **`AntiAbuseChecker` protocol (in protocols.py) does not match the concrete implementation.** The protocol defines `async def check(self, request, session_id, message, agent_config=None)`, but `anti_abuse.py::AntiAbuseChecker.check()` has a different signature: `def check(self, session_id, ip, user_agent, message, n_msg, last_msg_time_since)`. The protocol is never used (`isinstance` would fail), and `@runtime_checkable` is misleading. Dead or wrong contract. | `protocols.py:100-105` vs `anti_abuse.py:175-185` |
| MEDIUM | **Sync I/O on event loop in security checks.** `check_abuse()` in `security.py` calls `AntiAbuseChecker.check()` (sync, uses `threading.Lock`) and `TokenBucket.allow()` (sync, uses `threading.Lock`) without `asyncio.to_thread`. These are fast operations but still block the event loop. | `security.py:23-50` |
| MEDIUM | **Spending limits are in-memory only.** If the api-service restarts, all spending records are lost. A tenant could make $2000 in LLM calls before the budget catches up. No persistence for spending records. | `spending.py:37-43` |
| MEDIUM | **`SpendingMiddleware.record()` is called per-tenant in a loop** — each tenant in a composite multi-tenant request gets recorded separately. But `check_limits()` also runs per-tenant, so if tenant-A has exceeded budget, the entire request is blocked even if tenant-B is fine. A crafty tenant-B could DOS tenant-A's queries by including tenant-A in the composite header. | `middlewares.py:29-37` |
| LOW | **Guard patterns are case-insensitive but don't handle Unicode normalization.** An attacker could use Unicode homoglyphs (e.g., `fоrget` with Cyrillic 'о') to bypass `(?i)(?:ignore|forget|disregard...)` patterns. | `guardrails.py:20-60` |

---

## 6. Async Patterns — Sync Singletons Under Async, threading.Lock vs asyncio.Lock

### What's correct

- **`asyncio.Lock`** used correctly in all async-native components: `MCPClient._registry_lock`, `ConversationManager._LRULockCache`, `ProviderPool._lock`, `ProviderStore._lock`.
- **`threading.Lock`** used correctly for sync singletons: `deps._agent_lock` (one-time init), `SpendingChecker._lock` (fast dict ops, no awaitables).
- **`threading.RLock()`** in `SessionStore` — correct for SQLite access serialization.
- **_AsyncSpendingTracker and _AsyncBacklogWriter** adapters wrap sync singletons without `to_thread`, justified by fast operations (dict lookups, local file writes).

### Issues found

| Severity | Issue | File:Line |
|---|---|---|
| LOW | **`_AsyncSpendingTracker` does not use `asyncio.to_thread`.** Calling `get_spending_checker().record_spending()` on the event loop is fine for fast operations, but `get_spending_checker()` itself acquires a `threading.Lock`. Under high concurrency, this could block the event loop for multiple milliseconds. | `adapters.py:13-15` |
| LOW | **`GuardChecker.check_input()` is sync but called from async pipeline.** `GuardInputStage.run()` calls `ctx.guard_checker.check_input()` without `to_thread`. Regex search on short strings is fast, but over thousands of RPS this adds up on the event loop. | `stages/guard_input.py:32` |
| INFO | **`FastAPI lifespan` correctly closes MCP client and stops health checks** on shutdown. No known resource leaks. | `app.py:75-91` |

---

## 7. Race Conditions and Concurrency Problems

| Severity | Issue | File:Line |
|---|---|---|
| **HIGH** | **Backlog file writes are not atomic.** Multiple concurrent requests for the same session append to the same `.jsonl` file. `write()` + `SEPARATOR` is two syscalls — two concurrent writes can interleave, producing corrupted records. | `backlog.py:80-89` |
| **MEDIUM** | **ProviderPool `alive_workers()` → `pick()` TOCTOU.** Between the two lock acquisitions, the worker list changes. If the last alive worker dies between the calls, `pick()` re-fetches `alive_workers()` and raises `RuntimeError`. Caller sees "No alive providers" even though a worker was available when `alive_workers()` was first called. | `provider_pool.py:130-155` |
| MEDIUM | **Settings mutation (`apply_runtime_settings()`) races with concurrent requests.** If an admin changes abuse config while 10 requests are in flight, some read old settings, some read new. Inconsistent pipeline behavior within the same incident. No request-scoped settings snapshot. | `abuse_live.py:130-140` |
| LOW | **MCP `_get_connection()` TOCTOU.** Connection reference returned after releasing `_registry_lock` — another coroutine's `_reconnect()` could close it before the caller acquires `call_lock`. Narrow window, benign failure (reconnect retry handles it). | `mcp_client.py:108-120` |

---

## 8. Concrete Recommendations — Priority Order

### MUST FIX (Production Impact)

1. **[HIGH] `SaveHistoryStage.force_save()` on exception path.** In `orchestrator.py`, wrap the MCP session + pipeline execution in a try/finally that also calls `SaveHistoryStage` force-save:
   ```python
   try:
       async with ...:
           pipeline.run(ctx)
   except Exception:
       yield error_event
   finally:
       if ctx and ctx.turn.turn_messages:
           await SaveHistoryStage().force_save(pipeline_ctx)
       backlog.turn_end(...)
   ```

2. **[HIGH] Fix backlog file write concurrency.** Add a per-session `asyncio.Lock` (or use `SessionStore`'s existing lock) for backlog writes. Either serialize through the SQLite store or acquire a per-session async lock before writing to the backlog file.

3. **[HIGH] Fix `AntiAbuseChecker` protocol mismatch.** Either:
   - Update `protocols.py:AntiAbuseChecker` to match the real signature, or
   - Remove the protocol definition if it's unused (the real checker is accessed via `LiveAbuseProvider`).

### SHOULD FIX (Medium Term)

4. **[MEDIUM] Add MCP circuit breaker.** Track consecutive reconnect failures per tenant in `_TenantConnection`. After 3 failures in 60 seconds, skip reconnect and return a cached error `ToolResult` instead. Add exponential backoff.

5. **[MEDIUM] Add MCP session TTL garbage collection.** Launch a background `asyncio.Task` that checks `_connections` every 60 seconds and closes sessions idle > 10 minutes (the current 240s proactive reconnect handles idleness but doesn't close).

6. **[MEDIUM] Persist spending records.** Write spending to a file or SQLite in the `_AsyncSpendingTracker.record()` adapter. Load on startup. Without this, budget enforcement is reset on every restart.

### NICE TO FIX (Quality of Life)

7. **[LOW] `classify_error` → structured error codes.** Instead of substring matching, wrap exceptions at the source with an error code enum. Keep substring fallback for unclassified errors.

8. **[LOW] `TokenBucket.allow()` → `asyncio.Lock`.** Currently uses `threading.Lock` but is called from async context. Replace with `asyncio.Lock` to avoid even micro-blocking the event loop.

9. **[LOW] Unified `ErrorContext` adoption.** Have all stages call `ctx.error_context.with_stage()` so structured error metadata is consistently available in logs.

---

## Summary

| Area | Verdict | Critical Issues |
|---|---|---|
| Pipeline Architecture | Good — clean Stage/Protocol design, two-phase finalization | No exception-safe history save |
| Error Handling | Adequate — broad coverage, good categorization | Substring matching fragility, incomplete ErrorContext adoption |
| MCP Lifecycle | Solid — dual locks, two-phase timeout, reconnect | No circuit breaker, no TTL garbage collection |
| Provider Resolution | Clean — 5-level resolution, good isolation | Minor TOCTOU race in pool pick |
| Security | Good — layered guards, rate limiting, budget | Protocol contract mismatch, in-memory-only spending |
| Async Patterns | Good — correct lock types per context | None critical |
| Concurrency | Adequate — most races are benign or narrow | Backlog file write corruption |
| **Overall** | **Solid architecture with specific production gaps** | **3 HIGH issues to fix first** |

## Acceptance Report

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "All 35 source files in api-service/src/api_service/ were read and analyzed. Findings with file paths and severity are documented for all 8 audit categories. Concrete recommendations with priority ordering are provided."
    }
  ],
  "changedFiles": [
    ".pi/audit-api-service.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "read + grep (35 files across agent/, server/, root modules)",
      "result": "passed",
      "summary": "All target files read successfully; no errors"
    }
  ],
  "validationOutput": [
    "No validation tests run (read-only architectural audit)"
  ],
  "residualRisks": [
    "Backlog file write corruption under concurrent session writes (HIGH severity, unaddressed in code)",
    "No exception-safe history save (HIGH severity, unaddressed in code)",
    "AntiAbuseChecker protocol contract broken (HIGH severity, unaddressed in code)",
    "MCP circuit breaker missing (survivable but wasteful under sustained gateway outage)",
    "Spending limits reset on restart (medium-term risk for paid tenants)"
  ],
  "noStagedFiles": true,
  "diffSummary": "No code changes made. Single output file .pi/audit-api-service.md written.",
  "reviewFindings": [
    "blocker: orchestrator.py:132-148 — exception path does not save conversation history (SaveHistoryStage.force_save never called)",
    "blocker: backlog.py:80-89 — concurrent writes to same backlog file can interleave and corrupt records",
    "blocker: protocols.py:100-105 vs anti_abuse.py:175-185 — AntiAbuseChecker protocol signature does not match concrete implementation",
    "critical: spending.py:37-43 — spending records are in-memory only; lost on restart",
    "major: mcp_client.py:170-195 — no circuit breaker for persistent MCP gateway failures",
    "major: mcp_client.py:68-70 — _connections dict grows unboundedly with no background TTL cleanup",
    "moderate: middlewares.py:29-37 — multi-tenant composite requests: one tenant's budget exhaustion blocks the entire request",
    "moderate: error_messages.py:61-108 — substring-based classification fragile (e.g. 'auth' matches 'database_auth_error')",
    "moderate: security.py:23-50 — sync AntiAbuseChecker + TokenBucket called on event loop without to_thread",
    "minor: guardrails.py:20-60 — Unicode homoglyph bypass possible for injection patterns",
    "minor: stales/llm.py:63-64 — docstring says 'no gating' but code checks should_stop",
    "no-blockers: pipeline architecture, MCP lock strategy, provider resolution design, async patterns — all structurally sound"
  ],
  "manualNotes": "Plan.md and progress.md not found at project root. Progress file at .pi-subagents/artifacts/progress/57ae2087/progress.md was read (was empty, updated with audit status). Review-only mode: no code was edited. All findings are from static analysis."
}
```
