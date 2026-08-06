# External-DB tests (tests/external/)

Тесты, которым нужна **внешняя инфраструктура** (PostgreSQL, и т.п.) — не входят
в CI и не запускаются в `tests/e2e/`. Здесь живёт только документация + сценарии,
которые требуют внешних сервисов.

## Сценарии с внешними БД

| Сценарий | Требует | Где лежит | Как запустить |
|---|---|---|---|
| `postgres-testseed` | PostgreSQL (DSN `${DATABASE_URL}`) | `data-service/testdata/scenarios/postgres-testseed/` | `DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/db ./scripts/dev.sh db materialize postgres-testseed` |

> Сценарии `auto-shop` / `clinic` / `sqlite-testseed` / `shop` — **локальные SQLite**,
> используются в `tests/e2e/` (CI-ready). Не путать.

## Правила

1. **Не клади сюда тесты**, которые можно прогнать на локальном SQLite — им место в `tests/e2e/`.
2. Если тест требует внешний сервис (Postgres, Redis, ChromaDB на отдельном хосте) —
   клади сюда и помечай `@pytest.mark.external`.
3. В CI такие тесты **пропускаются** (маркер `external` исключён из прогона).
