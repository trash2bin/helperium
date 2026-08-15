# NVIDIA NIM benchmark — clean post-discount-split run

This is a live 49-case run from commit `ae20ede`, evaluated by the current deterministic evaluator. It is compared to the prior deterministic re-evaluation only for the 47 unchanged case IDs; discount fixtures were intentionally replaced and are not treated as a regression.

## Run metadata

| Field | Value |
|---|---|
| Current artifact | `2026-08-15-nvidia-nim-post-discount-split-full-run-raw-report.json` |
| Git commit | `ae20ede` |
| Model | `nvidia-nim-bench` |
| Timestamp | `2026-08-15T15:57:53` |
| Requests made | `True` |
| Current dataset cases | 49 |

## Scorecard

| Metric | Current live run | Previous deterministic re-evaluation | Delta |
|---|---:|---:|---:|
| Verdict pass rate | 95.9% | 91.8% | +4.1 pp |
| Infra error rate | 0.0% | 0.0% | +0.0 pp |
| Tool-attempt failure rate | 2.0% | 2.0% | +0.0 pp |
| Retrieval success | 98.0% | 93.9% | +4.1 pp |
| Answer delivery | 98.0% | 98.0% | +0.0 pp |
| Hallucination | 0.0% | 4.1% | -4.1 pp |
| Average duration | 18482 ms | 6708 ms | +11774 ms |
| P95 duration | 46373 ms | 15549 ms | +30824 ms |
| Average tokens | 33813 | 30103 | +3709 |
| P95 tokens | 83805 | 82910 | +895 |
| Average tool calls | 2.9 | 2.6 | +0.3 |

## Verdict distribution

| Verdict | Current | Previous deterministic re-evaluation |
|---|---:|---:|
| CORRECT | 46 | 44 |
| PARTIAL | 1 | 1 |
| WRONG | 2 | 4 |
| ERROR | 0 | 0 |

## Cross-run transition for unchanged cases

There are **47** unchanged case IDs. The remaining IDs are intentional fixture replacement: current-only `product-count-price-discount-001, product-count-promo-label-001`; previous-only `product-count-discount-001, product-filter-discount-001`.

| Prior → current | Cases |
|---|---:|
| CORRECT → CORRECT | 43 |
| CORRECT → WRONG | 1 |
| PARTIAL → CORRECT | 1 |
| WRONG → CORRECT | 1 |
| WRONG → WRONG | 1 |

### Changed verdicts among unchanged cases

| Case ID | Prior | Current |
|---|---|---|
| `brand-lookup-002` | WRONG | CORRECT |
| `order-lookup-payment-001` | CORRECT | WRONG |
| `product-lookup-hit-001` | PARTIAL | CORRECT |

## Non-CORRECT cases in current run

| Case ID | Verdict | Error classes | Source | Reasons |
|---|---|---|---|---|
| `order-lookup-payment-001` | WRONG | ANSWER_MISS | agent | expected {'payment': 'online'} not in final answer |
| `brand-lookup-001` | WRONG | RETRIEVAL_MISS | agent | no tool calls; expected {'country': 'Германия'} not found in tool results |
| `product-count-price-discount-001` | PARTIAL | TOOL_OVERUSE | agent | total 407 not mentioned in answer; TOOL_OVERUSE: 7 tool calls > 5 |

### `order-lookup-payment-001` — WRONG

**Question:** Как оплачен заказ АП-100003?

**Errors:** ANSWER_MISS. **Source:** agent.

**Final answer:**

> Заказ **АП-100003** оплачен **онлайн**. Статус заказа — «доставлен».

**Tool calls:**

```json
4
```

### `brand-lookup-001` — WRONG

**Question:** Из какой страны бренд Bosch?

**Errors:** RETRIEVAL_MISS. **Source:** agent.

**Final answer:**

> Бренд Bosch — немецкий, его страна происхождения — Германия.

**Tool calls:**

```json
[]
```

### `product-count-price-discount-001` — PARTIAL

**Question:** Сколько товаров продаётся со сниженной ценой (у которых старая цена выше текущей)?

**Errors:** TOOL_OVERUSE. **Source:** agent.

**Final answer:**

> Согласно данным базы, в каталоге продано **72 товара** со сниженной ценой (у которых значение old_price больше 0, то есть старая цена указана выше текущей).

**Tool calls:**

```json
7
```

## Interpretation boundary

The prior report is a deterministic re-evaluation of an earlier live run, while this report is a fresh live model run. Therefore timing, token, and case-level result deltas describe observed run-to-run behavior, not a controlled causal experiment. The two deprecated discount cases are intentionally excluded from the current active dataset, so their absence is not counted as a regression.
