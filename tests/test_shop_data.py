"""Phase 0 data: the generator is deterministic and the committed shop.json is what the seed produces."""

from collections import defaultdict

from data.generate_shop import OUT, SHOP_TODAY, dumps, generate


def test_same_seed_same_bytes():
    assert dumps(generate()) == dumps(generate())


def test_different_seed_different_data():
    assert dumps(generate(seed=1)) != dumps(generate(seed=2))


def test_committed_shop_json_is_current():
    # A stale file would silently break every label that points at it.
    assert OUT.read_text(encoding="utf-8") == dumps(generate()), "run `python -m data.generate_shop`"


def test_shape_and_sizes():
    shop = generate()
    assert len(shop["customers"]) == 200
    assert len(shop["orders"]) == 500
    assert len({c["email"] for c in shop["customers"]}) == 200
    assert all(c["email"].endswith("@example.com") for c in shop["customers"])


def test_orders_are_internally_consistent():
    shop = generate()
    customer_ids = {c["id"] for c in shop["customers"]}
    today = SHOP_TODAY.isoformat()
    for o in shop["orders"]:
        assert o["customer_id"] in customer_ids
        subtotal = sum(l["qty"] * l["unit_price_cents"] for l in o["items"])
        assert o["subtotal_cents"] == subtotal
        assert o["shipping_cents"] == (0 if subtotal >= 7500 else 599)  # policy SHP-2
        assert o["total_paid_cents"] == subtotal + o["shipping_cents"]
        assert o["placed_at"] <= today
        if o["status"] == "processing":
            assert o["shipped_at"] is None and o["delivered_at"] is None
        elif o["status"] == "shipped":
            assert o["shipped_at"] and o["delivered_at"] is None
        else:
            assert o["placed_at"] < o["shipped_at"] < o["delivered_at"] <= today


def test_refunds_never_exceed_amount_paid():
    shop = generate()
    paid = {o["id"]: o["total_paid_cents"] for o in shop["orders"]}
    refunded: dict[str, int] = defaultdict(int)
    for r in shop["refunds"]:
        assert r["amount_cents"] > 0
        refunded[r["order_id"]] += r["amount_cents"]
    assert all(refunded[o] <= paid[o] for o in refunded)
