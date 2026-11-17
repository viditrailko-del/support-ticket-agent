"""Seeded, deterministic fake shop: customers, products, orders, deliveries, past refunds.

    python -m data.generate_shop            # (re)writes data/shop.json
    python -m data.generate_shop --check    # exit 1 if data/shop.json is not what the seed produces

Same seed -> same bytes. The labeled tickets in eval/tickets/ point at orders in this file, so any change
here (seed, catalog, probabilities, the order of rng calls) means re-checking every label.

Design choices:
- Money is integer cents everywhere (no float arithmetic on money).
- Dates are ISO strings relative to a fixed SHOP_TODAY, so "inside the 30-day window" never depends on
  the day the tests run.
- Stdlib only: runs anywhere, no DB, no network. Phase 1 seeds Postgres from this JSON.
- No real people: names come from a small invented list, emails are @example.com.
"""

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 20261115
SHOP_TODAY = date(2026, 11, 15)  # "now" for the shop and for every labeled ticket
N_CUSTOMERS = 200
N_ORDERS = 500
VIP_SHARE = 0.10

SHIPPING_FEE_CENTS = 599  # policy SHP-2
FREE_SHIPPING_FROM_CENTS = 7500  # policy SHP-2

OUT = Path(__file__).with_name("shop.json")

FIRST_NAMES = [
    "Ava", "Ben", "Chloe", "Dev", "Elena", "Farid", "Grace", "Hugo", "Ines", "Jonas",
    "Kira", "Liam", "Maya", "Nico", "Olga", "Priya", "Quentin", "Rosa", "Sami", "Tara",
    "Umar", "Vera", "Wes", "Ximena", "Yusuf", "Zoe", "Arjun", "Bea", "Cyril", "Dana",
]
LAST_NAMES = [
    "Abbott", "Brandt", "Castillo", "Dorsey", "Eklund", "Fontaine", "Gallo", "Hartley",
    "Ivers", "Jansen", "Kowal", "Lindqvist", "Moreau", "Nakamura", "Okafor", "Pereira",
    "Quigley", "Rasmussen", "Sorensen", "Thorne", "Underwood", "Varga", "Whitlock", "Yilmaz",
]

# sku, name, category, price_cents, final_sale
PRODUCTS = [
    ("EL-100", "Wireless Earbuds", "electronics", 4999, False),
    ("EL-101", "Bluetooth Speaker", "electronics", 3499, False),
    ("EL-102", "USB-C Charger 65W", "electronics", 2999, False),
    ("EL-103", "Smart Watch", "electronics", 12999, False),
    ("EL-104", "Noise-Cancelling Headphones", "electronics", 18999, False),
    ("EL-105", "E-Reader", "electronics", 9999, False),
    ("EL-106", "Power Bank 20000mAh", "electronics", 3999, False),
    ("HM-200", "Ceramic Mug Set", "home", 2499, False),
    ("HM-201", "Cotton Throw Blanket", "home", 3999, False),
    ("HM-202", "Desk Lamp", "home", 4499, False),
    ("HM-203", "Chef's Knife", "home", 5999, False),
    ("HM-204", "Bamboo Cutting Board", "home", 1999, False),
    ("HM-205", "French Press", "home", 2799, False),
    ("HM-206", "Scented Candle", "home", 1499, False),
    ("AP-300", "Merino Sweater", "apparel", 7999, False),
    ("AP-301", "Rain Jacket", "apparel", 9499, False),
    ("AP-302", "Running Shoes", "apparel", 8999, False),
    ("AP-303", "Wool Socks (3-pack)", "apparel", 1899, False),
    ("AP-304", "Canvas Tote Bag", "apparel", 2299, False),
    ("AP-305", "Leather Belt", "apparel", 3499, False),
    ("OU-400", "Insulated Water Bottle", "outdoor", 2999, False),
    ("OU-401", "Camping Lantern", "outdoor", 3799, False),
    ("OU-402", "Trail Backpack 30L", "outdoor", 10999, False),
    ("OU-403", "Yoga Mat", "outdoor", 3299, False),
    ("CL-500", "Clearance Sunglasses", "clearance", 1599, True),
    ("CL-501", "Clearance Phone Case", "clearance", 999, True),
    ("CL-502", "Clearance Beanie", "clearance", 1299, True),
]

REFUND_REASONS = ["changed_mind", "damaged", "wrong_item", "goodwill"]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def generate(seed: int = SEED) -> dict:
    rng = random.Random(seed)

    products = [
        {"sku": s, "name": n, "category": c, "price_cents": p, "final_sale": f}
        for s, n, c, p, f in PRODUCTS
    ]

    customers = []
    used_emails: set[str] = set()
    for i in range(1, N_CUSTOMERS + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        base = f"{first}.{last}".lower()
        email, n = f"{base}@example.com", 2
        while email in used_emails:
            email, n = f"{base}{n}@example.com", n + 1
        used_emails.add(email)
        customers.append({
            "id": f"C{i:04d}",
            "name": f"{first} {last}",
            "email": email,
            "tier": "vip" if rng.random() < VIP_SHARE else "standard",
            "created_at": _iso(_rand_date(rng, date(2023, 1, 1), date(2026, 4, 30))),
        })

    # Some customers order a lot, most order once or twice.
    weights = [rng.choice([1, 1, 1, 2, 3, 6]) for _ in customers]

    orders = []
    order_dates = sorted(
        _rand_date(rng, date(2026, 5, 1), SHOP_TODAY) for _ in range(N_ORDERS)
    )
    for i, placed in enumerate(order_dates, start=1):
        customer = rng.choices(customers, weights=weights)[0]
        lines = []
        for p in rng.sample(products, k=rng.choice([1, 1, 1, 2, 2, 3])):
            qty = 2 if rng.random() < 0.15 else 1
            lines.append({
                "sku": p["sku"], "name": p["name"], "category": p["category"],
                "qty": qty, "unit_price_cents": p["price_cents"], "final_sale": p["final_sale"],
            })
        subtotal = sum(l["qty"] * l["unit_price_cents"] for l in lines)
        shipping = 0 if subtotal >= FREE_SHIPPING_FROM_CENTS else SHIPPING_FEE_CENTS

        shipped = delivered = None
        if (SHOP_TODAY - placed).days >= 2:
            shipped = placed + timedelta(days=rng.randint(1, 2))
            transit = rng.randint(3, 7)
            lost = rng.random() < 0.03
            if not lost and shipped + timedelta(days=transit) <= SHOP_TODAY:
                delivered = shipped + timedelta(days=transit)
        status = "delivered" if delivered else "shipped" if shipped else "processing"

        orders.append({
            "id": f"ORD-{10000 + i}",
            "customer_id": customer["id"],
            "placed_at": _iso(placed),
            "status": status,
            "shipped_at": _iso(shipped),
            "delivered_at": _iso(delivered),
            "items": lines,
            "subtotal_cents": subtotal,
            "shipping_cents": shipping,
            "total_paid_cents": subtotal + shipping,
        })

    refunds = []
    for o in orders:
        if o["status"] != "delivered" or rng.random() >= 0.08:
            continue
        delivered = date.fromisoformat(o["delivered_at"])
        when = min(delivered + timedelta(days=rng.randint(1, 20)), SHOP_TODAY)
        reason = rng.choice(REFUND_REASONS)
        if reason == "goodwill":
            amount = min(rng.choice([500, 1000, 1500]), o["total_paid_cents"])
        else:
            line = rng.choice(o["items"])
            amount = line["qty"] * line["unit_price_cents"]
        refunds.append({
            "id": f"RF-{len(refunds) + 1:04d}",
            "order_id": o["id"],
            "amount_cents": amount,
            "reason": reason,
            "created_at": _iso(when),
        })

    return {
        "seed": seed,
        "shop_today": _iso(SHOP_TODAY),
        "products": products,
        "customers": customers,
        "orders": orders,
        "refunds": refunds,
    }


def dumps(shop: dict) -> str:
    return json.dumps(shop, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify data/shop.json is up to date")
    args = ap.parse_args()
    text = dumps(generate())
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale: run `python -m data.generate_shop`", file=sys.stderr)
            return 1
        print(f"{OUT} is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    shop = generate()
    print(f"wrote {OUT}: {len(shop['customers'])} customers, {len(shop['orders'])} orders, "
          f"{len(shop['refunds'])} refunds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
