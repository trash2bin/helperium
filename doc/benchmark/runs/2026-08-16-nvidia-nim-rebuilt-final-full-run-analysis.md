# NVIDIA NIM benchmark — clean rebuilt final full run

> **Status:** raw observation; no commit. This run is the clean comparison point after rebuilding `data-service`, `mcp-gateway`, and `api`, reseeding the autoparts fixture, regenerating tenant config, and synchronizing the versioned benchmark policy. The NVIDIA API key is not recorded in this artifact.

## Provenance

The run executed all 49 active cases against agent `nvidia-nim-bench` and tenant `autoparts`. Before the run, the deterministic fixture was applied with `seed=42`, the tenant config was regenerated through the supported rewrite endpoint, and the live MCP gateway manifest was verified to contain both the generic required-filter/authoritative-total contract and the `label` semantic description.

| Property | Value |
|---|---|
| Raw artifact | [`2026-08-16-nvidia-nim-rebuilt-final-full-run-raw-report.json`](2026-08-16-nvidia-nim-rebuilt-final-full-run-raw-report.json) |
| Active cases | 49 |
| Agent | `nvidia-nim-bench` |
| Tenant | `autoparts` |
| Runtime preparation | rebuild data-service, MCP gateway, and API; reseed; tenant rewrite; policy sync |
| Model requests | 49 |
| Raw exit code | 1 because the runner exits non-zero when any WRONG or ERROR verdict exists |

## Result

| Verdict | Cases | Share |
|---|---:|---:|
| CORRECT | 45 | 91.8% |
| PARTIAL | 2 | 4.1% |
| WRONG | 1 | 2.0% |
| ERROR | 1 | 2.0% |
| **Verdict pass rate** (`CORRECT + PARTIAL`) | **47 / 49** | **95.9%** |

The previous `final-policy` full run, which was later shown to use a stale data-service image, produced `43 / 2 / 3 / 1`. This rebuilt run therefore improves the raw distribution by two CORRECT cases and removes two WRONG cases. The comparison is directional rather than causal: the model is stochastic and the current working tree also contains uncommitted deterministic fixes.

## What the rebuilt runtime demonstrated

The marketing promotion question now completed correctly with **49** products for `label IN ('sale', 'promo')`. This is the relevant integration proof that the rebuilt manifest exposed the current `label` semantics and that the model used the filter contract successfully.

The Cyrillic order lookup path, typed integer ID validation, valid `db_describe` count path, and corrected `EXT-01401` fixture wording caused no verdict failure in this run. The former `db_get(AP-100006)` HTTP 500 was not reproduced.

The price-discount question also returned **72**, but this should not be overinterpreted. Its final answer describes `old_price__gt=0`, whereas the benchmark truth is `old_price > price`. Those predicates happen to coincide in the current seeded data, but the generic filter contract does not support a field-to-field comparison. This remains a capability gap, not a proof that the price-discount expression is generally representable.

## Non-CORRECT cases

### `order-count-status-002` — WRONG

| Field | Evidence |
|---|---|
| Question | `Сколько заказов со статусом delivered?` |
| Expected | 3 |
| Trace | No tool calls; `token` and `final` both contain a malformed English “Here's a thinking process” response |
| Verdict | WRONG (`RETRIEVAL_MISS`, `ANSWER_MISS`) |
| Classification | Agent/model-generation or thought-channel handling failure; not a data-service failure |
| Why | The live filter endpoint accepts a known order status, but the model never invoked a tool. |

### `product-filter-combined-001` — ERROR

| Field | Evidence |
|---|---|
| Question | `Покажи тормозные колодки бренда Bosch в наличии` |
| Trace | `errors = ["Request failed: timed out"]`; no events, tool calls, or final text |
| Verdict | ERROR (`RETRIEVAL_MISS`, `ANSWER_MISS`) |
| Classification | Runner/API-provider transport failure |
| Follow-up | The report currently records `infra_error_rate = 0.0%` because this timeout is flattened to `outcome=final` with empty text in the high-level case record. This is a deterministic runner telemetry/classification defect. |

### `brand-lookup-002` — PARTIAL

| Field | Evidence |
|---|---|
| Question | `Из какой страны бренд Denso?` |
| Final answer | `Бренд Denso из Японии.` |
| Tool used | `db_describe` |
| Grounding evidence | `db_describe` returned the description `Denso — OEM производитель автозапчастей из Япония...` |
| Verdict cause | Fixture allows `filter_catalog_brand`, `db_search`, and `db_get`, but not `db_describe` |
| Classification | Benchmark fixture allowance defect; no model defect is established. |

### `product-absence-003` — PARTIAL

| Field | Evidence |
|---|---|
| Question | `Покажи товары бренда НЕСУЩЕСТВУЮЩИЙ` |
| Final answer | Correctly reports that the brand and its products do not exist |
| Tool path | `db_map` then `filter_catalog_brand`; brand filter returned `total = 0` |
| Verdict cause | Fixture permits `filter_catalog_product` or `db_search`, but not the valid prerequisite `filter_catalog_brand` |
| Classification | Benchmark fixture allowance defect; no model defect is established. |

## Recommended next steps

| Priority | Action | Rationale |
|---|---|---|
| P0 | Preserve this raw report and the two failure traces. Do not clean historical evidence yet. | The run contains a genuine provider/API timeout. |
| P1 | Fix runner classification so a captured `Request failed: timed out` contributes to `infra_error_rate` and is not flattened into `outcome=final`. Add a deterministic regression from a saved error trace. | The present top-level metric says `0.0%` infra errors although the per-case trace proves an infrastructure timeout. |
| P1 | Add `db_describe` to `brand-lookup-002` and `filter_catalog_brand` to `product-absence-003` accepted tool alternatives; add evaluator regressions. | Both results are grounded by the actual tool traces. |
| P2 | Investigate the NVIDIA thought/content channel and require a non-empty user-facing final after generation. | `order-count-status-002` was not a data lookup failure; it was a malformed think-only answer with no tool call. |
| P2 | Design a generic, safe field-to-field comparison capability before treating `old_price > price` as fully solved. | `old_price__gt=0` is only accidentally equivalent for the current seed. |

No code was changed as part of this analysis artifact, and no commit was created.
