"""Helperium-owned: детерминированный seed базы autoparts (НЕ из исходника).

Проблема: foreign seed_data.py использует random/Faker без random.seed() —
каждый запуск генерирует ДРУГИЕ данные → exact_value/count ground truth бенча
ломаются при reseed.

Решение: фиксируем random.seed(42) + Faker.seed(42) ДО вызова seed_data,
получая 100% воспроизводимую базу. Это helperium-owned дополнение —
не редактирует foreign-файлы.

Дополнительно (пост-патч после seed_data, см. doc/benchmark/data-service-audit.md §5):
- country_of_origin: выравниваем по стране бренда (36 товаров seed_data.py
  захардкодил неверно: колодки TRW/Brembo «Германия», свечи Bosch «Япония»).
- supplier: заполняем пустые детерминированно (brand + ' Distribution').
- oem_number: генерируем уникальные OEM-номера детерминированно (seeded random).
- assert: предупреждаем о дубликатах имён категорий (коллизия dict в seed_data.py).
- PostgreSQL column comments: фиксируем значения `old_price` и `label` для
  автогенерируемого MCP filter contract.

Правило: НЕ трогаем price/quantity/category/brand/is_available — ground truth
бенча (49 кейсов, seed=42) завязан на них.

Запуск:
    cd demo/autoparts-store
    DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell < ../../demo/autoparts-store/seed_fixture.py
    # или
    DB_HOST=127.0.0.1 DB_PORT=5434 uv run manage.py shell -c "$(cat seed_fixture.py)"
"""
import random
from faker import Faker

# Фиксируем seed ДО генерации (детерминированная база)
SEED = 42
random.seed(SEED)
Faker.seed(SEED)

# Вызываем команду seed_data с зафиксированным seed
from django.core.management import call_command
from django.db import connection

call_command("seed_data", verbosity=1)

# Domain meanings reach MCP tool descriptions through Postgres introspection ->
# configgen -> EntityField.Description. This is idempotent and survives rewrite.
with connection.cursor() as cursor:
    cursor.execute(
        "COMMENT ON COLUMN catalog_product.old_price IS "
        "'Previous product price. A price discount means old_price is higher than the current price.'"
    )
    cursor.execute(
        "COMMENT ON COLUMN catalog_product.label IS "
        "'Marketing label. Values sale and promo mean promotional campaigns; this is independent of price discount.'"
    )

# ── Пост-патч: реализм данных (helperium-owned, не foreign) ─────────────
from catalog.models import Product, Brand, Category, Order

rng = random.Random(SEED + 1000)  # отдельный поток от основного seed

# 1. country_of_origin: выравниваем по стране бренда.
changed = []
mismatched = 0
for p in Product.objects.select_related('brand'):
    orig = p.country_of_origin
    if p.country_of_origin and p.brand.country and p.country_of_origin != p.brand.country:
        p.country_of_origin = p.brand.country
        mismatched += 1
    # Пустые тоже заполняем страной бренда (детерминизм: страна бренда фиксирована).
    if not p.country_of_origin and p.brand.country:
        p.country_of_origin = p.brand.country
    if p.country_of_origin != orig:
        changed.append(p)

# 2. supplier: заполняем пустые детерминированно.
changed_by_pk = {p.pk: p for p in changed}
for p in Product.objects.filter(supplier='').select_related('brand'):
    p.supplier = f"{p.brand.name} Distribution"
    if p.pk in changed_by_pk:
        changed_by_pk[p.pk].supplier = p.supplier
    else:
        changed.append(p)
        changed_by_pk[p.pk] = p

# 3. oem_number: уникальные OEM-номера (детерминированно, seeded).
#    Формат: 3-буквенный код бренда + 6 цифр от rng. Уникальность по статье.
#    ВАЖНО: используем объект из changed (по pk), иначе bulk_update сохранит
#    старый объект без oem (два объекта с одним pk — потеря значения).
changed_by_pk = {p.pk: p for p in changed}
oem_seen = set()
for p in Product.objects.filter(oem_number='').select_related('brand'):
    code = ''.join(ch for ch in p.brand.name if ch.isalnum()).upper()[:3] or 'GEN'
    while True:
        oem = f"{code}{rng.randint(100000, 999999)}"
        if oem not in oem_seen:
            break
    oem_seen.add(oem)
    target = changed_by_pk.get(p.pk)
    if target is not None:
        target.oem_number = oem
    else:
        p.oem_number = oem
        changed.append(p)
        changed_by_pk[p.pk] = p

# 4. assert: дубликаты имён категорий (коллизия dict в seed_data.py) —
#    предупреждение, не ошибка (существующие кейсы не зависят).
dup_names = {}
for c in Category.objects.all():
    dup_names.setdefault(c.name, []).append(c.id)
dups = {k: v for k, v in dup_names.items() if len(v) > 1}
if dups:
    print(f"⚠️ Дубликаты имён категорий (коллизия dict в seed_data.py): {len(dups)}")

# 5. assert: товары на листьях (родители без прямых товаров — норма).
leaf_empty = Category.objects.filter(products__isnull=True).count()
print(f"ℹ️ Категорий без прямых товаров (листья): {leaf_empty}")

if mismatched:
    print(f"✅ Выровнено country_of_origin: {mismatched} товаров")

# Сохраняем только изменённые
if changed:
    Product.objects.bulk_update(
        changed,
        ['country_of_origin', 'supplier', 'oem_number'],
        batch_size=200,
    )
    print(f"✅ Пост-патч: обновлено {len(changed)} товаров (origin/supplier/oem)")

# Отчёт
print(f"✅ Детерминированная база (seed={SEED}): "
      f"brands={Brand.objects.count()}, cats={Category.objects.count()}, "
      f"products={Product.objects.count()}, orders={Order.objects.count()}")
