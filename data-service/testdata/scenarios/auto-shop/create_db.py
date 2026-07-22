"""
Create an auto parts shop database — realistic Russian auto parts store.

Usage:
    python create_db.py                          # → ./data.db
    SHOP_DB=/path/to/data.db python create_db.py  # → custom path
"""

import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get("SHOP_DB", Path(__file__).resolve().parent / "data.db"))
DB.unlink(missing_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(str(DB))

db.executescript("""
    -- Brands (car manufacturers)
    CREATE TABLE brands (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        country TEXT
    );

    -- Car models
    CREATE TABLE car_models (
        id INTEGER PRIMARY KEY,
        brand_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        year_from INTEGER,
        year_to INTEGER,
        FOREIGN KEY (brand_id) REFERENCES brands(id)
    );

    -- Auto parts catalog
    CREATE TABLE auto_parts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        brand_id INTEGER,
        category TEXT,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        oem_number TEXT UNIQUE,
        description TEXT,
        car_model_id INTEGER,
        FOREIGN KEY (brand_id) REFERENCES brands(id),
        FOREIGN KEY (car_model_id) REFERENCES car_models(id)
    );

    -- Customers
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        car_brand TEXT,
        car_model TEXT,
        city TEXT
    );

    -- Orders
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        customer_name TEXT NOT NULL,
        customer_phone TEXT,
        part_id INTEGER,
        quantity INTEGER NOT NULL DEFAULT 1,
        total REAL NOT NULL,
        status TEXT DEFAULT 'new',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (part_id) REFERENCES auto_parts(id)
    );

    -- ======== DATA ========

    -- Brands
    INSERT INTO brands VALUES (1, 'BMW', 'Германия');
    INSERT INTO brands VALUES (2, 'Mercedes-Benz', 'Германия');
    INSERT INTO brands VALUES (3, 'Toyota', 'Япония');
    INSERT INTO brands VALUES (4, 'VAG (VW/Audi/Skoda)', 'Германия');
    INSERT INTO brands VALUES (5, 'Lada', 'Россия');

    -- Car Models
    INSERT INTO car_models VALUES (1, 1, 'X5 (E70)', 2006, 2013);
    INSERT INTO car_models VALUES (2, 1, '3 Series (E90)', 2005, 2012);
    INSERT INTO car_models VALUES (3, 2, 'C-Class (W204)', 2007, 2014);
    INSERT INTO car_models VALUES (4, 2, 'E-Class (W211)', 2002, 2009);
    INSERT INTO car_models VALUES (5, 3, 'Camry (XV50)', 2011, 2018);
    INSERT INTO car_models VALUES (6, 3, 'RAV4 (XA40)', 2013, 2019);
    INSERT INTO car_models VALUES (7, 4, 'Passat B8', 2014, 2024);
    INSERT INTO car_models VALUES (8, 4, 'A4 (B9)', 2015, 2023);
    INSERT INTO car_models VALUES (9, 5, 'Vesta', 2015, 2024);
    INSERT INTO car_models VALUES (10, 5, 'Granta', 2018, 2024);

    -- Auto Parts - Exhaust system / Глушители
    INSERT INTO auto_parts VALUES (1, 'Глушитель задний BMW X5 E70 оригинал', 1, 'Выхлопная система', 28500, 5, 'BMW-1810X5E70', 'Оригинальный задний глушитель BMW X5 E70 левый', 1);
    INSERT INTO auto_parts VALUES (2, 'Глушитель универсальный 52мм нержавейка', NULL, 'Выхлопная система', 3490, 150, 'UNIV-52MM-SS', 'Универсальный прямоточный глушитель 52мм из нержавеющей стали', NULL);
    INSERT INTO auto_parts VALUES (3, 'Глушитель спортивный прямоточный 63мм', NULL, 'Выхлопная система', 8900, 30, 'SPORT-63-STRAIGHT', 'Спортивный прямоточный глушитель 63мм, звук глубокий бас', NULL);
    INSERT INTO auto_parts VALUES (4, 'Глушитель средний Mercedes W211', 2, 'Выхлопная система', 12500, 8, 'MB-211-MID', 'Средний глушитель (резонатор) Mercedes W211', 4);
    INSERT INTO auto_parts VALUES (5, 'Глушитель задний Lada Vesta', 5, 'Выхлопная система', 5500, 40, 'LADA-VESTA-REAR', 'Задний глушитель Лада Веста с хомутами', 9);
    INSERT INTO auto_parts VALUES (6, 'Глушитель универсальный 45мм', NULL, 'Выхлопная система', 2500, 200, 'UNIV-45MM', 'Универсальный глушитель 45мм, бюджетный вариант', NULL);
    INSERT INTO auto_parts VALUES (7, 'Катализатор универсальный 100mm', NULL, 'Выхлопная система', 18500, 12, 'CAT-100-UNIV', 'Универсальный катализатор 100мм керамический', NULL);
    INSERT INTO auto_parts VALUES (8, 'Приёмная труба BMW E46', 1, 'Выхлопная система', 9500, 10, 'BMW-E46-FRONT', 'Приёмная труба с катализатором BMW E46', 2);
    INSERT INTO auto_parts VALUES (9, 'Лямбда-зонд Bosch универсальный', NULL, 'Выхлопная система', 4200, 100, 'BOSCH-LS-UNIV', 'Лямбда-зонд Bosch 4 провода универсальный', NULL);
    INSERT INTO auto_parts VALUES (10, 'Глушитель Audi A4 B9 спортивный', 4, 'Выхлопная система', 32000, 3, 'AUDI-A4B9-SPORT', 'Спортивный глушитель Audi A4 B9 с электронными заслонками', 8);

    -- Filters
    INSERT INTO auto_parts VALUES (11, 'Фильтр масляный MANN W914/2', NULL, 'Фильтры', 650, 500, 'MANN-W914-2', 'Масляный фильтр MANN-Filter W914/2, качественный аналог оригиналу', NULL);
    INSERT INTO auto_parts VALUES (12, 'Фильтр воздушный K&N спортивный', NULL, 'Фильтры', 4800, 25, 'KN-AIRSPORT', 'Спортивный воздушный фильтр K&N многоразовый, увеличение мощности', NULL);
    INSERT INTO auto_parts VALUES (13, 'Фильтр салонный угольный Bosch', NULL, 'Фильтры', 890, 300, 'BOSCH-CABIN-C', 'Угольный салонный фильтр Bosch с активированным углём', NULL);
    INSERT INTO auto_parts VALUES (14, 'Фильтр топливный тонкой очистки', NULL, 'Фильтры', 750, 200, 'FUEL-FINE-FILT', 'Фильтр тонкой очистки топлива универсальный (12v)', NULL);
    INSERT INTO auto_parts VALUES (15, 'Фильтр масляный Lada Vesta оригинал', 5, 'Фильтры', 550, 300, 'LADA-VESTA-OIL', 'Оригинальный масляный фильтр Лада Веста (артикул 21080-1012005-00)', 9);

    -- Brake system
    INSERT INTO auto_parts VALUES (16, 'Колодки тормозные передние Brembo', NULL, 'Тормозная система', 6500, 60, 'BREMBO-PAD-F', 'Передние тормозные колодки Brembo P680, высокий коэффициент трения', NULL);
    INSERT INTO auto_parts VALUES (17, 'Диски тормозные передние Zimmermann', NULL, 'Тормозная система', 7800, 40, 'ZIMMERMANN-DISC-F', 'Передние тормозные диски Zimmermann перфорированные 320мм', NULL);
    INSERT INTO auto_parts VALUES (18, 'Тормозная жидкость DOT 4 1л', NULL, 'Тормозная система', 450, 500, 'DOT4-1L', 'Тормозная жидкость DOT 4, точка кипения 260°C, 1 литр', NULL);
    INSERT INTO auto_parts VALUES (19, 'Колодки тормозные задние TRW', NULL, 'Тормозная система', 3800, 80, 'TRW-PAD-R', 'Задние тормозные колодки TRW, тихие и эффективные', NULL);
    INSERT INTO auto_parts VALUES (20, 'Комплект тормозных дисков и колодок BMW X5', 1, 'Тормозная система', 45000, 4, 'BMW-X5-BRAKE-KIT', 'Полный комплект тормозных дисков и колодок BMW X5 E70', 1);

    -- Oils and Fluids
    INSERT INTO auto_parts VALUES (21, 'Масло моторное 5W30 синтетика 4л', NULL, 'Масла и жидкости', 3200, 1000, 'OIL-5W30-SYNTH', 'Моторное масло 5W30 полная синтетика, 4л, допуски MB/BMW/VW', NULL);
    INSERT INTO auto_parts VALUES (22, 'Антифриз G12 красный концентрат 1л', NULL, 'Масла и жидкости', 550, 800, 'ANTIFREEZE-G12', 'Антифриз G12 красный концентрат, срок службы 5 лет', NULL);
    INSERT INTO auto_parts VALUES (23, 'Масло трансмиссионное 75W90 1л', NULL, 'Масла и жидкости', 1200, 400, 'TRANS-OIL-75W90', 'Трансмиссионное масло 75W90 GL-4/GL-5, для МКПП и редукторов', NULL);
    INSERT INTO auto_parts VALUES (24, 'Жидкость ГУР 1л', NULL, 'Масла и жидкости', 380, 250, 'POWER-STEERING-FLUID', 'Гидравлическая жидкость для ГУР, универсальная', NULL);
    INSERT INTO auto_parts VALUES (25, 'Моторное масло Toyota 5W40 5л', 3, 'Масла и жидкости', 4500, 200, 'TOYOTA-5W40-5L', 'Оригинальное моторное масло Toyota 5W-40 5 литров', 5);

    -- Other parts
    INSERT INTO auto_parts VALUES (26, 'Свечи зажигания NGK Iridium 4шт', NULL, 'Электрика', 3500, 300, 'NGK-IRIDIUM-4', 'Свечи зажигания NGK Iridium IX, ресурс 60000км, комплект 4шт', NULL);
    INSERT INTO auto_parts VALUES (27, 'Аккумулятор Varta 60Ah', NULL, 'Электрика', 8500, 50, 'VARTA-60AH', 'Аккумуляторная батарея Varta Blue Dynamic 60Ah, пусковой ток 540A', NULL);
    INSERT INTO auto_parts VALUES (28, 'Щётки стеклоочистителя Bosch Aerotwin', NULL, 'Электрика', 1800, 100, 'BOSCH-AEROTWIN', 'Стеклоочистители Bosch Aerotwin комплект 2шт, бескаркасные', NULL);
    INSERT INTO auto_parts VALUES (29, 'Ремень ГРМ комплект Gates', NULL, 'Двигатель', 6500, 60, 'GATES-TIMING-KIT', 'Комплект ремня ГРМ Gates (ремень + ролик + помпа)', NULL);
    INSERT INTO auto_parts VALUES (30, 'Радиатор охлаждения Nissens', NULL, 'Двигатель', 12500, 25, 'NISSENS-RAD', 'Радиатор охлаждения двигателя Nissens, алюминиевый', NULL);
    INSERT INTO auto_parts VALUES (31, 'Помпа водяная HEPU универсальная', NULL, 'Двигатель', 3200, 80, 'HEPU-WATER-PUMP', 'Помпа водяная HEPU, универсальный монтаж', NULL);
    INSERT INTO auto_parts VALUES (32, 'Подшипник ступицы SKF передний', NULL, 'Подвеска', 4500, 100, 'SKF-HUB-F', 'Передний подшипник ступицы SKF, двухрядный', NULL);
    INSERT INTO auto_parts VALUES (33, 'Амортизатор передний KYB', NULL, 'Подвеска', 7200, 45, 'KYB-SHOCK-F', 'Передний амортизатор KYB газомасляный', NULL);
    INSERT INTO auto_parts VALUES (34, 'Сайлентблок переднего рычага Lemförder', NULL, 'Подвеска', 1200, 200, 'LEMFOERDER-SILENT', 'Сайлентблок переднего нижнего рычага Lemförder', NULL);
    INSERT INTO auto_parts VALUES (35, 'Амортизатор задний Sachs', NULL, 'Подвеска', 5800, 35, 'SACHS-SHOCK-R', 'Задний амортизатор Sachs газомасляный', NULL);

    -- Customers
    INSERT INTO customers VALUES (1, 'Сергей Иванович Козлов', '+79161234567', 'BMW', 'X5 (E70)', 'Москва');
    INSERT INTO customers VALUES (2, 'Елена Владимировна Морозова', '+79239876543', 'Toyota', 'Camry (XV50)', 'СПб');
    INSERT INTO customers VALUES (3, 'Дмитрий Александрович Новиков', '+79501234567', 'Lada', 'Vesta', 'Казань');
    INSERT INTO customers VALUES (4, 'Анна Павловна Соколова', '+79651234567', 'Mercedes-Benz', 'E-Class (W211)', 'Екатеринбург');
    INSERT INTO customers VALUES (5, 'Илья Романович Белов', '+79991234567', 'VAG (VW/Audi/Skoda)', 'A4 (B9)', 'Новосибирск');

    -- Orders
    INSERT INTO orders VALUES (1, 'Сергей Иванович Козлов', '+79161234567', 1, 1, 28500, 'delivered', '2025-01-15');
    INSERT INTO orders VALUES (2, 'Елена Владимировна Морозова', '+79239876543', 25, 2, 9000, 'shipped', '2025-02-01');
    INSERT INTO orders VALUES (3, 'Дмитрий Александрович Новиков', '+79501234567', 5, 1, 5500, 'processing', '2025-02-10');
    INSERT INTO orders VALUES (4, 'Анна Павловна Соколова', '+79651234567', 4, 1, 12500, 'delivered', '2025-01-20');
    INSERT INTO orders VALUES (5, 'Илья Романович Белов', '+79991234567', 10, 1, 32000, 'cancelled', '2025-02-05');
    INSERT INTO orders VALUES (6, 'Сергей Иванович Козлов', '+79161234567', 20, 1, 45000, 'delivered', '2025-01-25');
    INSERT INTO orders VALUES (7, 'Сергей Иванович Козлов', '+79161234567', 9, 2, 8400, 'new', '2025-02-12');
    INSERT INTO orders VALUES (8, 'Пётр Геннадьевич Фёдоров', '+79001234567', 3, 1, 8900, 'shipped', '2025-02-08');
    INSERT INTO orders VALUES (9, 'Алексей Викторович Кузнецов', '+79098765432', 13, 3, 2670, 'processing', '2025-02-14');
""")

db.commit()

# Verify
cnt = db.execute("SELECT COUNT(*) FROM auto_parts").fetchone()[0]
print(f"✅ Created auto-shop database: {DB} ({DB.stat().st_size} bytes)")
print(f"   Brands: {db.execute('SELECT COUNT(*) FROM brands').fetchone()[0]}")
print(f"   Car models: {db.execute('SELECT COUNT(*) FROM car_models').fetchone()[0]}")
print(f"   Auto parts: {cnt}")
print(f"   Customers: {db.execute('SELECT COUNT(*) FROM customers').fetchone()[0]}")
print(f"   Orders: {db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]}")

# Print all parts for reference
cur = db.execute("SELECT id, name, category, price FROM auto_parts ORDER BY category, price")
print("\nAll parts:")
for row in cur:
    print(f"  [{row[2]:20s}] {row[1]:45s} {row[3]:>8.2f}₽")
