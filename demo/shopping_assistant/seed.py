from __future__ import annotations

import argparse

from sqlalchemy import func, select

from .auth import hash_password
from .db import (
    Account,
    CartItem,
    Coupon,
    Faq,
    Order,
    Payment,
    Product,
    Return,
    SessionLocal,
    create_all,
    reset,
)


def _rows() -> list:
    """Build fresh ORM instances for every seed row (fresh per call so they can be re-added)."""
    products = [
        Product(
            id="SKU-1001",
            name="Aurora Midi Dress",
            price=49.0,
            rating=4.5,
            in_stock=True,
            category="dresses",
            description="Flowy midi dress in a breathable viscose blend, great for warm days.",
            deal=None,
        ),
        Product(
            id="SKU-1002",
            name="Linen Wrap Dress",
            price=62.0,
            rating=4.2,
            in_stock=True,
            category="dresses",
            description="Adjustable wrap dress in pure linen with a flattering V-neck.",
            deal=None,
        ),
        Product(
            id="SKU-1003",
            name="Everyday Cotton Tee",
            price=18.0,
            rating=4.7,
            in_stock=False,
            category="tops",
            description="Soft organic-cotton crew tee, a wardrobe staple.",
            deal=None,
        ),
        Product(
            id="SKU-1004",
            name="High-Rise Slim Jeans",
            price=58.0,
            rating=4.4,
            in_stock=True,
            category="bottoms",
            description="Stretch high-rise jeans with a slim leg and no-gap waist.",
            deal=None,
        ),
        Product(
            id="SKU-1005",
            name="Merino Wool Sweater",
            price=89.0,
            rating=4.6,
            in_stock=True,
            category="knitwear",
            description="Fine-gauge merino crewneck, warm but lightweight.",
            deal="15% off this week",
        ),
        Product(
            id="SKU-1006",
            name="Classic Trench Coat",
            price=145.0,
            rating=4.8,
            in_stock=True,
            category="outerwear",
            description="Water-resistant cotton trench with a removable belt.",
            deal=None,
        ),
        Product(
            id="SKU-1007",
            name="Leather Ankle Boots",
            price=120.0,
            rating=4.3,
            in_stock=True,
            category="shoes",
            description="Full-grain leather boots with a stacked block heel.",
            deal=None,
        ),
        Product(
            id="SKU-1008",
            name="Silk Scarf",
            price=35.0,
            rating=4.1,
            in_stock=True,
            category="accessories",
            description="Hand-rolled mulberry-silk scarf in a painterly print.",
            deal=None,
        ),
        Product(
            id="SKU-1009",
            name="Canvas Tote Bag",
            price=28.0,
            rating=4.5,
            in_stock=False,
            category="accessories",
            description="Heavy-duty cotton-canvas tote with an inner zip pocket.",
            deal=None,
        ),
        Product(
            id="SKU-1010",
            name="Lounge Jogger Pants",
            price=42.0,
            rating=4.0,
            in_stock=True,
            category="bottoms",
            description="Brushed-fleece joggers with a tapered fit and deep pockets.",
            deal="10% off this week",
        ),
    ]
    coupons = [
        Coupon(code="SAVE10", discount_pct=10, product_id=None, min_subtotal=0.0),
        Coupon(code="WELCOME20", discount_pct=20, product_id=None, min_subtotal=75.0),
        Coupon(code="DRESS15", discount_pct=15, product_id="SKU-1001", min_subtotal=0.0),
        Coupon(code="SHOES25", discount_pct=25, product_id="SKU-1007", min_subtotal=0.0),
    ]
    orders = [
        Order(
            order_id="ORD-1001",
            email="alice@example.com",
            status="shipped",
            carrier="UPS",
            tracking_number="1Z999AA10123456784",
            estimated_delivery="2026-06-29",
            placed_on="2026-06-20",
            total=49.0,
        ),
        Order(
            order_id="ORD-1002",
            email="bob@example.com",
            status="delivered",
            carrier="FedEx",
            tracking_number="612382734512",
            estimated_delivery="2026-06-18",
            placed_on="2026-06-12",
            total=120.0,
        ),
        Order(
            order_id="ORD-1003",
            email="carol@example.com",
            status="processing",
            carrier=None,
            tracking_number=None,
            estimated_delivery="2026-07-02",
            placed_on="2026-06-26",
            total=89.0,
        ),
        Order(
            order_id="ORD-1004",
            email="alice@example.com",
            status="cancelled",
            carrier=None,
            tracking_number=None,
            estimated_delivery=None,
            placed_on="2026-06-15",
            total=18.0,
        ),
        Order(
            order_id="ORD-1005",
            email="mohammad@example.com",
            status="shipped",
            carrier="DHL",
            tracking_number="JD014600003RS034",
            estimated_delivery="2026-07-05",
            placed_on="2026-06-28",
            total=145.0,
        ),
        Order(
            order_id="ORD-1006",
            email="mohammad@example.com",
            status="processing",
            carrier=None,
            tracking_number=None,
            estimated_delivery="2026-07-08",
            placed_on="2026-07-01",
            total=62.0,
        ),
    ]
    returns = [
        Return(
            rma_number="RMA-55872",
            order_id="ORD-1002",
            eligible=True,
            window_days_left=12,
            refund_method="original payment",
            status="approved",
            next_step="Pack the item and drop it at any FedEx location using the emailed label.",
        ),
    ]
    # Login credentials for the demo: mohammad@example.com / mohammad123 (the test user),
    # everyone else password123 — handy for verifying cross-account isolation.
    accounts = [
        Account(
            email="alice@example.com",
            name="Alice Martin",
            address="12 Rue de Lyon, 75012 Paris, France",
            password_hash=hash_password("password123"),
        ),
        Account(
            email="bob@example.com",
            name="Bob Chen",
            address="88 Market St, San Francisco, CA 94103, USA",
            password_hash=hash_password("password123"),
        ),
        Account(
            email="carol@example.com",
            name="Carol Diaz",
            address="5 King St, London EC2V 8EA, UK",
            password_hash=hash_password("password123"),
        ),
        Account(
            email="mohammad@example.com",
            name="Mohammad Rhayem",
            address="Hamra St, Beirut, Lebanon",
            password_hash=hash_password("mohammad123"),
        ),
    ]
    # The in-flight cart the checkout agent inspects, plus a seeded checkout failure to diagnose.
    cart_items = [CartItem(cart_id="current", product_id="SKU-1001", qty=1)]
    payments = [
        Payment(
            cart_id="current",
            payment_status="declined",
            reason="card_expired",
            suggestion="Ask the customer to update the card expiry date or use another payment method.",
        )
    ]
    faqs = [
        Faq(
            question="How much does shipping cost and how long does it take?",
            answer="Standard shipping is free on orders over $50 and takes 3-5 business days. Express shipping is $9.99 and takes 1-2 business days.",
            source="policy/shipping-and-returns",
            keywords="shipping delivery cost free express how long time",
        ),
        Faq(
            question="What is the return policy?",
            answer="Returns are accepted within 30 days of delivery for a full refund to the original payment method. Items must be unworn with tags attached.",
            source="policy/returns",
            keywords="return refund exchange policy 30 days window",
        ),
        Faq(
            question="Can I use more than one coupon?",
            answer="Only one coupon code can be applied per order, but coupons do stack on top of any active site-wide deal.",
            source="policy/promotions",
            keywords="coupon promo code discount stack one per order",
        ),
        Faq(
            question="Which payment methods do you accept?",
            answer="We accept Visa, Mastercard, American Express, PayPal, and Apple Pay. Cards are charged when the order ships.",
            source="policy/payments",
            keywords="payment card pay methods visa mastercard paypal apple",
        ),
        Faq(
            question="How do I reset my password?",
            answer="Use the 'Forgot password' link on the login page, or ask support to email you a secure reset link.",
            source="help/account",
            keywords="password reset account login email forgot",
        ),
    ]
    return [*products, *coupons, *orders, *returns, *accounts, *cart_items, *payments, *faqs]


def seed() -> None:
    """Create the schema (if missing) and insert the dummy data, unless it is already there."""
    create_all()
    with SessionLocal.begin() as session:
        if session.scalar(select(func.count()).select_from(Product)):
            print("Shop database already seeded — nothing to do.")
            return
        rows = _rows()
        session.add_all(rows)
        print(f"Seeded {len(rows)} rows into the 'shop' schema.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo shop database.")
    parser.add_argument(
        "--reset", action="store_true", help="Drop the shop schema first, then reseed from scratch."
    )
    args = parser.parse_args()
    if args.reset:
        reset()
        print("Dropped the 'shop' schema.")
    seed()


if __name__ == "__main__":
    main()
