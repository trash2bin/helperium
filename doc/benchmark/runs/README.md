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

**Последний verified plateau:** 83.7% pass rate (40 CORRECT / 1 PARTIAL / 2 WRONG / 6 ERROR),
достигнут на двух последовательных прогонах. Основные оставшиеся классы ошибок:

- AP↔АП транслитерация order-number аргументов (стабильный паттерн模型, не recovery)
- `is_promo=true` вместо `label IN ('sale','promo')` в promo-кейсах
- Волатильные per-case ошибки (не удаётся устранить без model-specific hardcode)

Новые структурные фиксы, поднявшие па Vancouver с 30% до 83.7%,
задокументированы в `../core-benchmark.md` и changelog фиксов.

---
**Last verified:** 2026-08-24 (working tree following `0add4ea`) — documentation restructure (P0-P5 sweep).
