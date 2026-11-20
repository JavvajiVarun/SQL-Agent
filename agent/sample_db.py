"""Create a small, realistic e-commerce SQLite database (deterministic, so answers are checkable).

    python -m agent.sample_db      → data/shop.db
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "shop.db"

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 24.99), ("Mechanical Keyboard", "Electronics", 89.00),
    ("USB-C Hub", "Electronics", 39.50), ("27-inch Monitor", "Electronics", 249.00),
    ("Noise-Cancelling Headphones", "Electronics", 179.00), ("Laptop Stand", "Accessories", 34.00),
    ("Desk Lamp", "Home Office", 29.99), ("Ergonomic Chair", "Home Office", 319.00),
    ("Standing Desk", "Home Office", 459.00), ("Notebook Pack", "Stationery", 12.50),
    ("Gel Pens (12)", "Stationery", 9.99), ("Water Bottle", "Lifestyle", 19.00),
    ("Backpack", "Lifestyle", 69.00), ("Coffee Mug", "Lifestyle", 14.00), ("Webcam 1080p", "Electronics", 59.00),
]
CITIES = [("Phoenix", "AZ"), ("Tempe", "AZ"), ("Austin", "TX"), ("Dallas", "TX"), ("Seattle", "WA"),
          ("San Jose", "CA"), ("Los Angeles", "CA"), ("Chicago", "IL"), ("New York", "NY"), ("Boston", "MA")]
FIRST = ["Ava", "Liam", "Noah", "Emma", "Mia", "Ethan", "Priya", "Arjun", "Sofia", "Lucas", "Zara", "Omar"]
LAST = ["Patel", "Smith", "Garcia", "Chen", "Kim", "Reddy", "Johnson", "Lopez", "Nguyen", "Brown"]

SCHEMA = """
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, city TEXT, state TEXT, signup_date TEXT);
CREATE TABLE products  (product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders    (order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id),
                        order_date TEXT, status TEXT);
CREATE TABLE order_items (order_id INTEGER REFERENCES orders(order_id), product_id INTEGER REFERENCES products(product_id),
                          quantity INTEGER, unit_price REAL);
"""


def build(path: Path = DB_PATH, seed: int = 7, n_customers: int = 300, n_orders: int = 2000) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    start = date(2026, 1, 1)
    con.executemany("INSERT INTO products VALUES (?,?,?,?)", [(i + 1, *p) for i, p in enumerate(PRODUCTS)])
    customers = []
    for cid in range(1, n_customers + 1):
        city, state = rng.choice(CITIES)
        customers.append((cid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", city, state,
                          (start + timedelta(days=rng.randint(0, 200))).isoformat()))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
    weights = [8, 3, 5, 2, 3, 4, 4, 1, 1, 7, 6, 5, 3, 6, 3]  # cheap items sell more often
    for oid in range(1, n_orders + 1):
        cid = rng.randint(1, n_customers)
        d = start + timedelta(days=rng.randint(0, 319))  # Jan 1 – Nov 16, 2026
        status = rng.choices(["completed", "cancelled", "refunded"], [0.88, 0.08, 0.04])[0]
        con.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, cid, d.isoformat(), status))
        for pid in set(rng.choices(range(1, len(PRODUCTS) + 1), weights, k=rng.randint(1, 3))):
            con.execute("INSERT INTO order_items VALUES (?,?,?,?)",
                        (oid, pid, rng.randint(1, 3), PRODUCTS[pid - 1][2]))
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    print("Created", build())
