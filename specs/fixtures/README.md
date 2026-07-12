# Test fixtures для data-service

Эта директория содержит технические артефакты, которые **используются runtime'ом**,
но **не хранятся в git** (регенерируются детерминированно).

## seed.json

`fixtures.json` со схемой university-данных (студенты, группы, преподаватели,
дисциплины, расписание, оценки), который читает Go-сервис `data-service --seed`.

### Почему здесь

- Логически **технический артефакт** — рядом с OpenAPI-спецификациями в `specs/`.
- Не часть продового контракта API (как OpenAPI), а артефакт для dev-режима.
- Содержит ровно те же данные, что и `university.db` после сидинга — это
  snapshot формата, который data-service умеет читать.

### Не в git

В `.gitignore`: `specs/fixtures/seed.json`. Регенерируется одной командой
(детерминирован при `--seed 42`):

```bash
uv run agent-seedgen                    # дефолт: 40 студентов, 60 оценок
uv run agent-seedgen --students 80 --grades 200 --seed 42
```

Затем заливается в БД:

```bash
DB_PATH=./university.db \
  go run ./data-service/cmd/server --seed --seed-path ./specs/fixtures/seed.json
```

## Почему не в `fixtures/` (на верхнем уровне)

Раньше этот JSON лежал в `fixtures/seed.json`. После миграции CLI в `rag/fixtures/`,
директория `fixtures/` перестала быть workspace member'ом и осталась только
как папка с артефактами. Но `specs/fixtures/` лучше потому что:

- `fixtures/` рядом с `helperium-sdk/`, `rag/` — соседствует с кодом.
- `specs/fixtures/` — соседствует с OpenAPI-схемами, которые тоже являются
  декларативными артефактами между кодом и внешним миром.
- Один gitignore-rule (`specs/fixtures/*`) вместо ad-hoc папки.

## Полный pipeline сидинга

```
agent-seedgen                    specs/fixtures/seed.json
(Python + faker)                 (плоские UUID-id, storage shape)
       │                                  │
       │  ───── uv run agent-seedgen ──▶ │
       │                                  │
       │           data-service --seed    │
       │           (Go, читает JSON)      │
       │                                  ▼
       └──▶ university.db / PostgreSQL   ◀──
```
