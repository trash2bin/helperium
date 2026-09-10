# Benchmark run registry

Ним-бенчмарк executed live через настройку `nvidia-nim-bench`
(Nemotron-3.5-lightning-30b, streamable HTTP `/mcp`, tenant `autoparts`).
Канонические прогоны хранятся в `bench-backlog/runs/<run_uuid>/`.
Raw-артефакты отдельных прогонов больше не коммитятся; только сводный
`benchmark_report.json` остаётся в run-directory и доступен локально.

## Прогрессия стабилизации (seed=42, canonical DB)

| Run | Дата | CORRECT | PARTIAL | WRONG | ERROR | Pass | Заметка |
|---|---|---:|---:|---:|---:|---:|---|
| `6d7e3295` | — | 14 | 2 | 4 | 29 | 32.7% | Базовый запуск; `db_filter` ещё не добавлен |
| `973e0d42` | — | — | — | — | — | 65.3% | Добавлен `db_filter`, JSON unwrap, numeric-string validation |
| `711d07ec` | — | 40 | 1 | 2 | 6 | 83.7% | Первый plateau; evaluator hyphen normalisation, case `must_call_any` поправлен |
| `59cd878f` | — | 40 | 1 | 2 | 6 | 83.7% | Второй plateau; подтверждает стабильный ceiling для этой модели |
| `9982c40` | 2026-09-11 | 40 | 0 | 1 | 8 | 81.6% | Актуализация на свежем HEAD; Nemotron 3.5 Lightning через NVIDIA build API. 2 extra ERROR — инфра-сбои API, не модель. 1 WRONG — классический AP↔АП (order-lookup-status-002). CORRECT rate идентичен: 40/49. |

**Последний verified plateau:** 81.6% pass rate (40 CORRECT / 0 PARTIAL / 1 WRONG / 8 ERROR)
на commit `9982c40` (2026-09-11), Nemotron-3.5-lightning-30b-a3b через NVIDIA build API.
Предыдущий plateau 83.7% (40 CORRECT / 1 PARTIAL / 2 WRONG / 6 ERROR) достигнут на
локальном NIM-контейнере. Разница в 2 ERROR — возросшая нестабильность NVIDIA API
(авторизация, 403, пустые ответы), не деградация модели. Основные оставшиеся классы ошибок:

- AP↔АП транслитерация order-number аргументов (стабильный паттерн модели, не recovery)
- `is_promo=true` вместо `label IN ('sale','promo')` в promo-кейсах
- Инфра-сбои NVIDIA API (авторизация, таймауты) — до 16% кейсов

Новые структурные фиксы, поднявшие па Vancouver с 30% до 83.7%,
задокументированы в `../core-benchmark.md` и changelog фиксов.

---
**Last verified:** 2026-09-11 (commit `9982c40`) — benchmark rerun on fresh HEAD with Nemotron 3.5 Lightning via NVIDIA build API; 81.6% pass rate, 40 CORRECT.
