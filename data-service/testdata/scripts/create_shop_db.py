"""
Create a foreign (non-university) SQLite database — an online shop.

This simulates "somebody else's database" that data-service connects to
via --discover, auto-generates config, and serves through REST API.

By default creates shop.db next to this script (data-service/testdata/scripts/shop.db).
Override via SHOP_DB env var to write to a custom path — e.g. the materialize
target scenarios/shop/data.db.
"""

import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get("SHOP_DB", Path(__file__).resolve().parent / "shop.db"))
DB.unlink(missing_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(str(DB))
db.executescript("""
    -- Products catalog
    CREATE TABLE categories (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT
    );

    CREATE TABLE products (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        category_id INTEGER,
        stock INTEGER DEFAULT 0,
        sku TEXT UNIQUE,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (category_id) REFERENCES categories(id)
    );

    -- Customers
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        phone TEXT,
        city TEXT,
        registered_at TEXT DEFAULT (datetime('now'))
    );

    -- Orders
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL,
        status TEXT DEFAULT 'new',
        total REAL NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    CREATE TABLE order_items (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    -- Reviews
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        rating INTEGER CHECK(rating >= 1 AND rating <= 5),
        comment TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    -- Insert some sample data so --discover has something to work with
    INSERT INTO categories VALUES (1, 'Электроника', 'Телефоны, ноутбуки, планшеты');
    INSERT INTO categories VALUES (2, 'Одежда', 'Футболки, джинсы, куртки');
    INSERT INTO categories VALUES (3, 'Книги', 'Художественная и техническая литература');

    INSERT INTO products VALUES (1, 'iPhone 15', 'Смартфон Apple', 999.99, 1, 50, 'IP15-001', '2025-09-01');
    INSERT INTO products VALUES (2, 'MacBook Pro', 'Ноутбук Apple M3', 2499.99, 1, 20, 'MBP-001', '2025-09-01');
    INSERT INTO products VALUES (3, 'Футболка Nike', 'Спортивная футболка', 29.99, 2, 200, 'NK-001', '2025-08-15');
    INSERT INTO products VALUES (4, 'Clean Code', 'Robert C. Martin', 49.99, 3, 100, 'BK-001', '2025-07-01');

    INSERT INTO customers VALUES (1, 'Иван Петров', 'ivan@example.com', '+79001234567', 'Москва', '2025-10-01');
    INSERT INTO customers VALUES (2, 'Анна Сидорова', 'anna@example.com', '+79007654321', 'СПб', '2025-10-15');

    INSERT INTO orders VALUES (1, 1, 'delivered', 1029.98, '2025-11-01');
    INSERT INTO orders VALUES (2, 2, 'processing', 2499.99, '2025-11-15');

    INSERT INTO order_items VALUES (1, 1, 1, 1, 999.99);
    INSERT INTO order_items VALUES (2, 1, 3, 1, 29.99);
    INSERT INTO order_items VALUES (3, 2, 2, 1, 2499.99);

    INSERT INTO reviews VALUES (1, 1, 1, 5, 'Отличный телефон!', '2025-11-10');
    INSERT INTO reviews VALUES (2, 3, 2, 4, 'Хорошая футболка, но маломерит', '2025-11-12');
""")

db.commit()
print(f"Created shop database: {DB} ({DB.stat().st_size} bytes)")
print("Tables: categories, products, customers, orders, order_items, reviews")

# Print schema for reference
cur = db.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")
for row in cur:
    print(f"  {row[0]}")
