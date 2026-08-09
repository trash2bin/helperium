# demo/autoparts-store — Foreign Demo Project (AutoParts24)

> ⚠️ **FOREIGN PROJECT.** Этот каталог — неизменённая копия внешнего проекта
> `auto-parts-store` (Django 5 / PostgreSQL 16, интернет-магазин автозапчастей).
> Он встроен в `demo/` **как есть**, чтобы:
> 1. дать helperium **реалистичную, ветвистую БД** для бенчей и демо,
> 2. показать интеграцию «в реальный проект»,
> 3. не тащить внешний репозиторий как зависимость.

---

## 🔗 Источник

- **Оригинал:** отдельный git-репозиторий клиента
- **Копия здесь:** `demo/autoparts-store/` (только git-tracked файлы, без `.git`, БД, `__pycache__`)

## 🚫 Правила (важно)

1. **НЕ редактируй файлы этого каталога** для фич helperium. Это чужой проект.
2. **Обновление** — перекопировать из исходника (см. ниже), не патчить вручную.
3. **Изоляция:** код helperium **никогда не импортирует** из `demo/autoparts-store/`.
   Это автономное приложение, подключённое к helperium только через БД (PostgreSQL).
4. Единственные helperium-owned файлы здесь: `db/schema.sql`, `README.foreign.md`, `.foreign`.

## 📦 Что внутри

| Файл | Назначение |
|---|---|
| `catalog/` | Django-приложение: модели, views, templates, admin, seed-команды |
| `config/` | Django settings/urls/wsgi |
| `manage.py`, `requirements.txt`, `Dockerfile`, `docker-compose.yml` | запуск |
| `db/schema.sql` | **фиксированный DDL** (из миграций) — для чистой PG-базы без Django |

## 🚀 Запуск

### 1. Через docker-compose (Django + PG, как оригинал)

```bash
cd demo/autoparts-store
docker-compose up -d        # PG на :5434, web на :8000
# seed (если нужно перезаполнить):
docker-compose exec -T web python manage.py seed_data
docker-compose exec -T web python manage.py seed_massive --products 100000 --orders 20000
```

### 2. Чистая PG-база без Django (для data-service / бенча)

```bash
# поднять только PG (из docker-compose), затем:
psql -h 127.0.0.1 -p 5434 -U autoparts -d autoparts -f demo/autoparts-store/db/schema.sql
# seed — через Django (только Django умеет наполнять связи/JSONB)
```

### 3. Локально через uv (без Docker, опционально)

```bash
cd demo/autoparts-store
uv sync                        # ставит зависимости в .venv каталога (изолированно)
DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py runserver 0.0.0.0:8000
# (PG поднять отдельно: docker compose up -d db, он на :5434)
```

> `pyproject.toml` в этом каталоге — helperium-owned дополнение (не из исходника),
> изолированный uv-каталог, НЕ workspace helperium, не влияет на его uv.lock.

### 4. Детерминированная база (для бенча) — helperium-owned

Foreign `seed_data.py` использует `random`/`Faker` **без фиксации seed** — каждый
запуск даёт разные данные → ground truth бенча ломается. Решение — helperium-owned
скрипт (не из исходника), фиксирующий seed:

```bash
cd demo/autoparts-store
DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell < seed_fixture.py
# → детерминированная база (seed=42): 30 брендов, 117 категорий, 407 товаров, 6 заказов
```

> `seed_fixture.py` — helperium-owned (как `db/schema.sql`), не редактируется при re-copy.

## 🧪 Данные (после seed_massive)

- 61 бренд (Bosch, TRW, NGK, Brembo, KYB…)
- 163 категории (дерево: «Тормозная система» → «Колодки тормозные»…)
- **1 705 000+ товаров** (article unique, price, quantity, `characteristics` JSONB, `car_applicability` JSONB, label…)
- **478 000+ заказов** (status/delivery/payment enum, `items` JSONB)
- 51 500 корзин

## 🔄 Как обновить копию из исходника

```bash
# из корня helperium
rm -rf demo/autoparts-store
mkdir -p demo/autoparts-store
cd /path/to/auto-parts-store && git archive --format=tar HEAD \
  | tar -x -C /path/to/helperium/demo/autoparts-store
# затем восстановить helperium-owned файлы (schema.sql, README.foreign.md, .foreign)
```

---

**См. также:** `doc/benchmark/` — как эта база используется для бенча (ground truth, детерминированные проверки).
