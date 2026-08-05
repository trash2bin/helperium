"""Helperium-owned: детерминированный seed базы autoparts (НЕ из исходника).

Проблема: foreign seed_data.py использует random/Faker без random.seed() —
каждый запуск генерирует ДРУГИЕ данные → exact_value/count ground truth бенча
ломаются при reseed.

Решение: фиксируем random.seed(42) + Faker.seed(42) ДО вызова seed_data,
получая 100% воспроизводимую базу. Это helperium-owned дополнение —
не редактирует foreign-файлы.

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

call_command("seed_data", verbosity=1)

# Отчёт
from catalog.models import Product, Brand, Category, Order

print(f"✅ Детерминированная база (seed={SEED}): "
      f"brands={Brand.objects.count()}, cats={Category.objects.count()}, "
      f"products={Product.objects.count()}, orders={Order.objects.count()}")
