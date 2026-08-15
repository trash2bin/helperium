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

## 2026-08-15 — NVIDIA NIM, полный прогон 49 кейсов

Полный Core Benchmark выполнен через `nvidia-nim-bench` с моделью
`nvidia/nemotron-3.5-lightning-30b-a3b` после исправления передачи `api_key`
в ветке `provider_priority` API Service. Перед запуском подтверждён успешный
SSE smoke (`token: OK` → `final: OK` → `done`). Исходный отчёт текущей рабочей
ветки: [`benchmark_report.json`](../../../benchmark_report.json).

| Показатель | Результат |
|---|---:|
| Кейсов | 49 |
| CORRECT | 36 (73,5%) |
| PARTIAL | 8 (16,3%) |
| WRONG | 3 (6,1%) |
| ERROR | 2 (4,1%) |
| Success rate в отчёте | 93,9% |
| Wall-clock длительность | 453,396 с (7,556 мин) |

`filter` прошёл 10/10, а `search` — 1/1. Основная зона риска — `aggregation`
(9 CORRECT, 6 PARTIAL, 1 WRONG), а также сценарии отсутствия сущностей. Два
`ERROR` были вызваны не моделью: `db_search` получил HTTP 500 от data-service
`/q/search`, хотя финальные ответы на оба вопроса были корректными.

Этот прогон является **интеграционным baseline NVIDIA NIM**, но пока не годится
для сравнения latency, token usage и cost: в bench-log отсутствуют
`duration_ms`, токены и стоимость, поэтому соответствующие p50/p95 в отчёте
нулевые. В 38 из 49 результатов также продублирован текст, поскольку runner
склеил SSE-события `token` и `final`. До повторного замера нужно исправить
`/q/search`, дедупликацию финального текста и сбор метрик; отдельно проверить
строгие `LOST_TOTAL`-правила evaluator.

## 2026-08-15 — NVIDIA NIM, повторный полный прогон после исправлений

Повторно измерен тот же набор из 49 кейсов через `nvidia-nim-bench`; итоговый
отчёт переоценён без новых model-вызовов после уточнения классификации tool
errors. Артефакт: [`2026-08-15-nvidia-nim-post-fixes-report.json`](2026-08-15-nvidia-nim-post-fixes-report.json).

| Показатель | Результат |
|---|---:|
| CORRECT | 39 (79,6%) |
| PARTIAL | 5 (10,2%) |
| WRONG | 5 (10,2%) |
| ERROR | 0 (0,0%) |
| Success rate | 89,8% (44/49) |
| P50 / P95 duration | 5,256 с / 15,549 с |
| Средняя задержка | 6,708 с |
| P50 / P95 total tokens | 25 041 / 82 910 |
| Среднее число tool calls / LLM calls | 2,6 / 3,6 |
| Стоимость | $0,00 (NIM не вернул тарифицируемую стоимость) |

Измерение устранило все две прежние `ERROR` поиска: `/q/search` больше не
применяет `ILIKE` к числовому `price`. Runner теперь сохраняет канонический
`final` вместо склейки `token` + `final`, а CLI читает живой
`services/api-service/backlog`, поэтому duration, tokens и LLM calls в этом
отчёте фактические. Четыре одноцифровых count-ответа больше не получают ложный
`LOST_TOTAL`; согласованная мягкая policy absence даёт корректному сообщению
«в каталоге нет» успех независимо от поясняющих деталей.

Этот прогон **не является строгим A/B-сравнением качества NIM**: модель
стохастична, а evaluator между прогонами был исправлен. Поэтому снижение
`success rate` с 93,9% baseline до 89,8% нельзя трактовать как регрессию
модели. Структурно улучшились проверяемость и классификация: `ERROR` снизились
с 2 до 0, а `CORRECT` выросли с 36 до 39. Оставшиеся проблемы поведения агента
— ответы Bosch/Denso без tool calls, неверная семантика «скидки» (49 вместо
ожидаемых 72) и invalid вызов фильтра без параметров — выделены в post-fix
отчёте как agent-side, а не инфраструктурные дефекты.

---
**Last verified:** 2026-08-15 (HEAD 3e2d83d) — добавлена сводка полного NVIDIA NIM-прогона и известные ограничения измерений.
