# Benchmark run artifacts

Machine-readable reports from Core Benchmark runs (deterministic evaluator,
no LLM judge). Each JSON is the full `report_to_dict()` output: per-case
success/eval/metrics/outcome/final_text — usable for post-hoc analysis.

| File | Date | Scope | Result |
|---|---|---|---|
| `2026-08-05-full-49-baseline-report.json` | 2026-08-05 | Full 49 cases, polza/deepseek-v4-flash, seed=42 (pre-fix baseline) | **81.6%** (40/49) |
| `2026-08-05-full-49-baseline.log` | 2026-08-05 | Console log of the same run | — |
| `2026-08-05-fail-selection-analysis.json` | 2026-08-05 | FAIL-case selection dump used by the failure-analysis swarm | — |
| `2026-08-06-9fail-recheck-report.json` | 2026-08-06 | 9 FAIL cases re-run after code fixes (entity-name, preview, `__in`) | 55.6% → 4 fixed by code |
| `2026-08-06-4fail-recheck-report.json` | 2026-08-06 | 4 remaining FAIL re-run after evaluator fixes (bool/derived) | 75.0% |
| `2026-08-06-1fail-recheck-report.json` | 2026-08-06 | order-lookup-total alone (derived-arithmetic fix) | PASS |
| `2026-08-06-3eval-fixes-report.json` | 2026-08-06 | brand-lookup-001 + 2 absence cases after evaluator fixes (morphology, db_map-not-data) | **100%** |
| `2026-08-06-full-49-after-fixes-report.json` | 2026-08-06 | Full 49 cases after ALL fixes (code + evaluator + case tweak) | **89.8%** (44/49); honest = **93.9%** (46/49, 2 real model hallucinations excluded) |

## What the 89.8% → 93.9% difference is

The full-after-fixes run reported 5 FAILs; 3 of them were evaluator false
positives (db_map counted as data in absence cases, country morphology
«Германии» vs «Германия»), fixed in `evaluator.py` and confirmed by re-runs
(`3eval-fixes-report` = 100%). The 2 remaining FAILs
(`product-filter-price-002`, `product-filter-discount-001`) are genuine model
hallucinations: the compact preview (`{id, name}`) exposes no prices, so the
model fabricates them instead of calling `db_get` — a known limitation
(scout-1 in `../data-service-audit.md`).

Raw per-session traces (SSE events, tool calls, tool results) live in
`bench-backlog/` (gitignored, ~1.7MB for the full run) — keyed by question text.
