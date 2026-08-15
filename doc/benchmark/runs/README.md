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

## 2026-08-15 — NVIDIA NIM: два полных прогона и детерминированная переоценка

Ниже зафиксированы **три разных артефакта**. Их нельзя смешивать при сравнении
verdict: первый и второй — самостоятельные live NIM runs с разными ответами
стохастичной модели; третий — только повторная оценка сохранённых логов второго
run, без NIM-вызовов.

| Артефакт | Тип | Commit / время | CORRECT | PARTIAL | WRONG | ERROR | Назначение |
|---|---|---|---:|---:|---:|---:|---|
| [`benchmark_report.json`](../../../benchmark_report.json) | Первый полный live NIM run | `3e2d83d`, 2026-08-15 00:35:17 | 36 | 8 | 3 | 2 | Интеграционный baseline NIM до runtime/benchmark fixes |
| [`2026-08-15-nvidia-nim-post-fix-full-run-raw-report.json`](2026-08-15-nvidia-nim-post-fix-full-run-raw-report.json) | Второй полный live NIM run | `c7c22a7`, 2026-08-15 01:18:46 | 39 | 5 | 4 | 1 | Raw outcome после исправления `/q/search` и runner telemetry |
| [`2026-08-15-nvidia-nim-post-fixes-report.json`](2026-08-15-nvidia-nim-post-fixes-report.json) | Deterministic re-evaluation второго run | без новых model-вызовов | 44 | 1 | 4 | 0 | Текущая оценка тех же saved logs финальным evaluator и fixtures |

### Первый NIM run — integration baseline

Первый full run выполнил 49 кейсов через `nvidia-nim-bench` с моделью
`nvidia/nemotron-3.5-lightning-30b-a3b` после исправления передачи `api_key` в
ветке `provider_priority` API Service. SSE smoke прошёл (`token: OK` →
`final: OK` → `done`). Он выявил HTTP 500 в `/q/search`, нулевые backlog metrics
и склейку `token` + `final`; поэтому этот артефакт — baseline интеграции, а не
валидная точка для latency/token comparison.

### Второй NIM run — raw post-fix outcome

Второй **отдельный** 49-case NIM run выполнен после исправления `/q/search`,
SSE dedupe и чтения live backlog. Его raw distribution: 39 CORRECT, 5 PARTIAL,
4 WRONG, 1 ERROR. Он содержит реальные новые ответы модели; например,
`brand-lookup-002` отвечает о Denso по внешнему знанию без tool call. Следовательно,
изменения verdict между первым и вторым run нельзя объяснять одной лишь
переоценкой.

### Deterministic re-evaluation второго run — current scoring

Текущий отчёт переоценивает **сохранённые логи второго run** без model-вызовов.
Он применяет исправления классификации tool 400/422, count fixtures,
одноцифровых totals, Unicode-разделителей чисел и false-uncertainty matching.
Итог: **44 CORRECT, 1 PARTIAL, 4 WRONG, 0 ERROR**, `verdict_pass_rate` **91,8%**.
Фактические P50/P95 duration — 5,256 с / 15,549 с; P50/P95 tokens — 25 041 /
82 910. Стоимость остаётся $0,00, поскольку NIM не вернул тарифицируемую
стоимость.

> Baseline 2026-08-12 в `services/agent-db/agent_db/bench/README.md` — это
> отдельный 49-case run **polza/deepseek-v4-flash** (`39/8/2/0`), не NVIDIA NIM;
> он не участвует в сравнении NIM run 2026-08-15.

Оставшиеся agent-side проблемы текущего scoring: Bosch и Denso без tool calls;
старый ambiguous discount count; старый ambiguous discount filter. Последние два
будут deprecated/replaced_by в одном изменении с явными price-discount и
marketing-label cases.

---
**Last verified:** 2026-08-15 — reconciled raw NIM runs and deterministic re-evaluation provenance.
