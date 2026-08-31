# Spending reserve/commit decision

**Status:** Implemented behind `SPENDING_RESERVATIONS_ENABLED`, **disabled by default in every environment**. The flag must stay off until the estimate-accuracy work in "Remaining before enablement" is done and covered by tests that run with the flag on.

## Problem

Helperium's original `SpendingChecker` records LLM cost *after* a turn completes. Concurrent turns all pass the pre-turn limit check and then record their costs, so a budget can be exceeded before the next request is refused. Composite scopes make tenant ID an insufficient billing identity, because one turn can use several tenants.

## Decision

The admission principal is a stable **account or named agent identity**, not a tenant ID. Tenant IDs remain usage dimensions for audit and reporting; they do not own the budget of a composite request.

Admission is two-phase:

1. `reserve(principal_id, request_id, estimated_cost, tenant_ids)` atomically creates a reservation if committed plus reserved usage stays within the principal budget.
2. The provider call executes while the reservation is held.
3. `commit(request_id, actual_cost)` replaces the estimate with realized cost and frees the unused remainder.
4. `release(request_id)` returns capacity when the provider call fails before cost is known.

`request_id` is `"{turn_id}:model-{model_call_index}"`: unique per provider call and stable under retry of the same call, so a repeated `reserve` is idempotent rather than double-charging.

## Money unit

All ledger arithmetic is **integer micro-USD** (`MICROS_PER_USD = 1_000_000`). This is not cosmetic: a realistic turn costs a small fraction of a cent, so a cents-based ledger rounded every reservation and every commit to zero and silently disabled admission entirely. `usd_to_micros` rounds up so realized cost is never understated.

## Budget semantics

`SpendingChecker` treats `budget <= 0` as *unlimited*. The ledger preserves that: an unlimited budget is SQL `NULL` / Python `None`, never `0`. Representing unlimited as `0` would invert the meaning into "nothing is allowed".

The principal budget is owned by the ledger (`set_budget`, default from `SPENDING_PRINCIPAL_DEFAULT_BUDGET`) and is **not** derived from tenant budgets. Deriving it — for example as the minimum tenant budget — would both contradict the principal decision above and overwrite any explicit operator value on every request.

## Composite requests

A composite request reserves once against its principal and stores the full tenant set as usage dimensions. It is never charged in full to each tenant separately. If tenant-level budgets are required later, the product must specify an allocation rule first; proportional attribution is preferable to duplicating the full amount.

## Reporting

Under admission, per-tenant `record_spending` still runs after each commit so `GET /admin/spending/{tenant_id}` keeps showing usage. Reporting failures are logged and never fail a turn the user already received.

## Failure and expiry policy

- Provider failure before cost is known → `release`, no charge. Bounded upstream retries inside one `complete()` call are covered by one reservation; see the accuracy caveat below.
- Refused reservation → the loop outcome is `limit_reached` with the standard spending message, not an internal error.
- A reservation has a bounded TTL (`SPENDING_RESERVATION_TTL_SECONDS`, default 1800s) so a crashed worker cannot hold budget forever. TTL must exceed the worst-case turn duration.
- An **expired** reservation is still committable. The provider call already happened and the money is really owed; refusing the commit would lose the charge and fail a delivered turn. Expiry only frees held capacity early.

## Persistence and concurrency

`SQLiteSpendingLedger` serializes every mutation with `BEGIN IMMEDIATE` and closes each connection explicitly (the `sqlite3.Connection` context manager commits but does not close). The ledger file location comes from settings and resolves against the project root, never the process CWD, because launchers start api-service from different directories and a relative path would split the ledger per launcher.

This is a **single-instance** design. Multi-instance deployments need a shared transactional store; the domain API is intended to move to PostgreSQL without changing the agent-loop contract.

A ledger file written by the retired cents schema is rejected at startup rather than reinterpreted as micro-USD.

## Remaining before enablement

1. **Estimate accuracy.** `_conservative_input_tokens` uses a fixed 3 characters/token ratio, which is *not* a guaranteed upper bound: Cyrillic text mixed with part numbers and emoji tokenize denser, and the tool schemas sent with each request are not counted at all. Replace with a real tokenizer count over messages **and** tools, plus a safety factor.
2. **Model pricing coverage.** No model currently configured in `.env`/Compose (MiniMax, the DeepSeek routing string, Ollama defaults, `scripted/test`) has an entry in `litellm.model_cost`, and estimation fails closed. Enabling the flag without an operator-supplied price source would refuse every turn. Needs an explicit price override channel.
3. **Retry attribution.** `LiteLLMProvider` performs bounded upstream retries inside one `complete()`; every attempt can cost money while one reservation covers them all.
4. Tests for 1–3 must run with `SPENDING_RESERVATIONS_ENABLED=true`, not only through the legacy branch.

## Verification

Covered by `services/api-service/src/api_service/tests/unit/test_spending_ledger.py` (money units, unlimited budgets, concurrency, idempotency, expiry, connection hygiene, legacy-schema rejection), `tests/unit/agent/test_loop_spending_reservation.py` (loop admission through the flag and the real ledger, refusal mapped to `limit_reached`, release on provider failure, reporting preserved) and `tests/unit/agent/test_spending_pricing.py` (fail-closed estimation, call-time price resolution). The legacy post-hoc contract stays characterized in `tests/unit/test_spending_and_provider_breaker_semantics.py`.
