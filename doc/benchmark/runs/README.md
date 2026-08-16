# Benchmark run artifacts

Этот каталог содержит только **актуальный canonical rebuilt run** и его case-level analysis. Старые raw runs, deterministic re-evaluations, smoke reports и extracted traces больше не являются активными artifacts; они сохранены локально в обратимом архиве `.data/benchmark-archive-20260816/` и остаются доступны через историю Git до отдельной очистки истории.

## 2026-08-16 — clean rebuilt final run

16 августа выполнен отдельный 49-case live NIM run после rebuild `data-service`, `mcp-gateway` и `api`, повторного deterministic seed `autoparts`, tenant rewrite и sync policy `autoparts-benchmark-v2`. До model run был проверен live MCP manifest: generic field-reference contract присутствовал в `filter_catalog_product`, а `old_price__gt_field=price` вернул authoritative `total=72`.

| Artifact | Type | Runtime | CORRECT | PARTIAL | WRONG | ERROR | Purpose |
|---|---|---|---:|---:|---:|---:|---|
| [`2026-08-16-nvidia-nim-rebuilt-final-full-run-raw-report.json`](2026-08-16-nvidia-nim-rebuilt-final-full-run-raw-report.json) | Full live NIM run | Rebuilt + reseeded + rewritten | 45 | 2 | 1 | 1 | Canonical raw integration evidence |
| [`2026-08-16-nvidia-nim-rebuilt-final-full-run-analysis.md`](2026-08-16-nvidia-nim-rebuilt-final-full-run-analysis.md) | Case-level analysis | Same run | — | — | — | — | Separates infra, fixture and agent-side failures |

The run has `verdict_pass_rate = 95.9%` (47/49). The single `ERROR` is a provider/transport timeout and is now represented by the evaluator as `ERROR` with `INFRA_ERROR`; the two `PARTIAL` cases include deterministic fixture allowance findings. The remaining `WRONG` is classified as agent-side behavior rather than evaluator or runtime failure.

The historical 2026-08-12 autoparts baseline remains documented in `services/agent-db/agent_db/bench/README.md` as a separate non-NIM baseline. It is not part of this active run registry.

**Last verified:** 2026-08-16 (рабочая ветка) — canonical rebuilt raw report and case-level analysis checked; live MCP manifest, field-reference total=72, evaluator timeout classification and policy v2 state verified. No newer NIM benchmark was executed after this run.
