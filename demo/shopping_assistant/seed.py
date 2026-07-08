from __future__ import annotations

import argparse

from sqlalchemy import func, select

from . import retrieval
from .auth import hash_password
from .db import (
    Account,
    CartItem,
    Coupon,
    Faq,
    Order,
    OrderItem,
    Payment,
    Product,
    Return,
    SessionLocal,
    create_all,
    reset,
)


def _role_accounts() -> list[Account]:
    """The merchant and admin demo logins (the merchant owns the Atelier shop)."""
    return [
        Account(
            email="merchant@example.com",
            name="Maya Atelier",
            address="4 Rue Cambon, 75001 Paris, France",
            password_hash=hash_password("merchant123"),
            role="merchant",
            shop="Atelier",
        ),
        Account(
            email="admin@example.com",
            name="Store Admin",
            address="Loom HQ, Beirut, Lebanon",
            password_hash=hash_password("admin123"),
            role="admin",
        ),
    ]


def _seed_order_items() -> list[OrderItem]:
    """Line items matching the seeded order totals. ORD-1002 mixes two shops on
    purpose: the Atelier merchant must see only its SKU-1002 line, never the jeans."""
    return [
        OrderItem(order_id="ORD-1001", product_id="SKU-1001", qty=1, unit_price=49.0),
        OrderItem(order_id="ORD-1002", product_id="SKU-1004", qty=1, unit_price=58.0),
        OrderItem(order_id="ORD-1002", product_id="SKU-1002", qty=1, unit_price=62.0),
        OrderItem(order_id="ORD-1003", product_id="SKU-1005", qty=1, unit_price=89.0),
        OrderItem(order_id="ORD-1004", product_id="SKU-1003", qty=1, unit_price=18.0),
        OrderItem(order_id="ORD-1005", product_id="SKU-1006", qty=1, unit_price=145.0),
        OrderItem(order_id="ORD-1006", product_id="SKU-1002", qty=1, unit_price=62.0),
    ]


def _product(
    pid: str,
    name: str,
    price: float,
    rating: float,
    in_stock: bool,
    category: str,
    shop: str,
    description: str,
    deal: str | None = None,
) -> Product:
    """One catalog row; the image is served by the demo's /shop/images endpoint."""
    return Product(
        id=pid,
        name=name,
        price=price,
        rating=rating,
        in_stock=in_stock,
        category=category,
        shop=shop,
        description=description,
        deal=deal,
        image_url=f"/shop/images/{pid}",
    )


def _rows() -> list:
    """Build fresh ORM instances for every seed row (fresh per call so they can be re-added)."""
    # The company runs three shops: Atelier (elevated womenswear), Everyday (casual
    # basics), and Steps & Co (shoes + accessories). fmt: off keeps the table readable.
    # fmt: off
    products = [
        _product("SKU-1001", "Aurora Midi Dress", 49.0, 4.5, True, "dresses", "Atelier",
                 "Flowy midi dress in a breathable viscose blend, great for warm days."),
        _product("SKU-1002", "Linen Wrap Dress", 62.0, 4.2, True, "dresses", "Atelier",
                 "Adjustable wrap dress in pure linen with a flattering V-neck."),
        _product("SKU-1003", "Everyday Cotton Tee", 18.0, 4.7, False, "tops", "Everyday",
                 "Soft organic-cotton crew tee, a wardrobe staple."),
        _product("SKU-1004", "High-Rise Slim Jeans", 58.0, 4.4, True, "bottoms", "Everyday",
                 "Stretch high-rise jeans with a slim leg and no-gap waist."),
        _product("SKU-1005", "Merino Wool Sweater", 89.0, 4.6, True, "knitwear", "Atelier",
                 "Fine-gauge merino crewneck, warm but lightweight.", "15% off this week"),
        _product("SKU-1006", "Classic Trench Coat", 145.0, 4.8, True, "outerwear", "Atelier",
                 "Water-resistant cotton trench with a removable belt."),
        _product("SKU-1007", "Leather Ankle Boots", 120.0, 4.3, True, "shoes", "Steps & Co",
                 "Full-grain leather boots with a stacked block heel."),
        _product("SKU-1008", "Silk Scarf", 35.0, 4.1, True, "accessories", "Steps & Co",
                 "Hand-rolled mulberry-silk scarf in a painterly print."),
        _product("SKU-1009", "Canvas Tote Bag", 28.0, 4.5, False, "accessories", "Steps & Co",
                 "Heavy-duty cotton-canvas tote with an inner zip pocket."),
        _product("SKU-1010", "Lounge Jogger Pants", 42.0, 4.0, True, "bottoms", "Everyday",
                 "Brushed-fleece joggers with a tapered fit and deep pockets.", "10% off this week"),
        _product("SKU-1011", "Satin Slip Dress", 79.0, 4.4, True, "dresses", "Atelier",
                 "Bias-cut satin slip dress with adjustable straps, day-to-night."),
        _product("SKU-1012", "Pleated Maxi Skirt", 54.0, 4.3, True, "bottoms", "Atelier",
                 "Sunray-pleated maxi skirt in a fluid crepe with an elastic waist."),
        _product("SKU-1013", "Cashmere Cardigan", 129.0, 4.7, True, "knitwear", "Atelier",
                 "Pure cashmere button cardigan, feather-light and impossibly soft.", "20% off this week"),
        _product("SKU-1014", "Tailored Blazer", 110.0, 4.5, True, "outerwear", "Atelier",
                 "Single-breasted blazer with a nipped waist and satin lining."),
        _product("SKU-1015", "Graphic Oversize Tee", 24.0, 4.2, True, "tops", "Everyday",
                 "Heavyweight oversize tee with a hand-drawn back print."),
        _product("SKU-1016", "Denim Trucker Jacket", 68.0, 4.6, True, "outerwear", "Everyday",
                 "Rigid indigo denim jacket that breaks in beautifully."),
        _product("SKU-1017", "Ribbed Tank Top", 15.0, 4.1, False, "tops", "Everyday",
                 "Stretch-ribbed tank with a scooped neck, sold in twin packs."),
        _product("SKU-1018", "Cargo Utility Pants", 49.0, 4.0, True, "bottoms", "Everyday",
                 "Relaxed cargo pants with bellow pockets and an adjustable hem."),
        _product("SKU-1019", "Performance Leggings", 38.0, 4.5, True, "activewear", "Everyday",
                 "Squat-proof high-rise leggings with a hidden waistband pocket."),
        _product("SKU-1020", "White Court Sneakers", 85.0, 4.6, True, "shoes", "Steps & Co",
                 "Minimal leather court sneakers with a cushioned insole.", "10% off this week"),
        _product("SKU-1021", "Suede Loafers", 95.0, 4.2, True, "shoes", "Steps & Co",
                 "Soft suede penny loafers with a stacked leather heel."),
        _product("SKU-1022", "Leather Crossbody Bag", 75.0, 4.4, True, "accessories", "Steps & Co",
                 "Compact pebbled-leather crossbody with an adjustable strap."),
        _product("SKU-1023", "Wool Beanie", 18.0, 4.3, False, "accessories", "Steps & Co",
                 "Chunky-knit merino beanie with a fold-up cuff."),
        _product("SKU-1024", "Aviator Sunglasses", 42.0, 4.1, True, "accessories", "Steps & Co",
                 "Classic aviators with polarized lenses and a slim metal frame."),
    ]
    # fmt: on
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
    # Login credentials for the demo: mohammad@example.com / mohammad123 (client),
    # merchant@example.com / merchant123 (merchant, owns Atelier), admin@example.com /
    # admin123 (admin); alice/bob/carol are password123 clients for isolation tests.
    accounts = [
        Account(
            email="alice@example.com",
            name="Alice Martin",
            address="12 Rue de Lyon, 75012 Paris, France",
            password_hash=hash_password("password123"),
            role="client",
        ),
        Account(
            email="bob@example.com",
            name="Bob Chen",
            address="88 Market St, San Francisco, CA 94103, USA",
            password_hash=hash_password("password123"),
            role="client",
        ),
        Account(
            email="carol@example.com",
            name="Carol Diaz",
            address="5 King St, London EC2V 8EA, UK",
            password_hash=hash_password("password123"),
            role="client",
        ),
        Account(
            email="mohammad@example.com",
            name="Mohammad Rhayem",
            address="Hamra St, Beirut, Lebanon",
            password_hash=hash_password("mohammad123"),
            role="client",
        ),
        *_role_accounts(),
    ]
    # Carts are per-account (cart_id = the account email). Mohammad's cart carries the
    # seeded checkout failure so the payment-diagnosis scenario works for the test user.
    cart_items = [CartItem(cart_id="mohammad@example.com", product_id="SKU-1001", qty=1)]
    payments = [
        Payment(
            cart_id="mohammad@example.com",
            payment_status="declined",
            reason="card_expired",
            suggestion="Ask the customer to update the card expiry date or use another payment method.",
        )
    ]
    # The policy handbook the support_concierge retrieves from (semantically when
    # embeddings are indexed, by keyword otherwise). Deliberately broad: ambiguous
    # queries land on the concierge, so this corpus is the fallback's substance.
    faqs = [
        Faq(
            question="How much does shipping cost and how long does it take?",
            answer="Standard shipping is free on orders over $50 and takes 3-5 business days. Express shipping is $9.99 and takes 1-2 business days.",
            source="policy/shipping-and-returns",
            keywords="shipping delivery cost free express how long time",
        ),
        Faq(
            question="Do you ship internationally?",
            answer="Yes — we ship to most countries. International orders take 7-14 business days, cost a flat $19.99, and any customs duties are the customer's responsibility.",
            source="policy/shipping-international",
            keywords="international shipping abroad overseas customs duties countries",
        ),
        Faq(
            question="What is the return policy?",
            answer="Returns are accepted within 30 days of delivery for a full refund to the original payment method. Items must be unworn with tags attached.",
            source="policy/returns",
            keywords="return refund exchange policy 30 days window",
        ),
        Faq(
            question="How long does a refund take to arrive?",
            answer="Once the returned item is received and inspected, the refund is issued within 2 business days; your bank may take another 3-5 business days to post it.",
            source="policy/refund-timeline",
            keywords="refund timeline money back how long bank days",
        ),
        Faq(
            question="Can I exchange an item instead of returning it?",
            answer="Yes — exchanges for a different size or color are free within the same 30-day window, and the replacement ships as soon as the original is scanned by the carrier.",
            source="policy/exchanges",
            keywords="exchange swap size color replacement different",
        ),
        Faq(
            question="What do I do if my item arrives damaged or defective?",
            answer="Contact support within 7 days with a photo; we send a prepaid return label and choose with you between a free replacement and a full refund, including shipping.",
            source="policy/damaged-items",
            keywords="damaged defective broken faulty arrived photo replacement",
        ),
        Faq(
            question="Can I change or cancel my order after placing it?",
            answer="Orders can be changed or cancelled free of charge until they ship — usually within 24 hours of being placed. Once shipped, use the return process instead.",
            source="policy/order-changes",
            keywords="cancel change modify order address after placing shipped",
        ),
        Faq(
            question="Can I use more than one coupon?",
            answer="Only one coupon code can be applied per order, but coupons do stack on top of any active site-wide deal.",
            source="policy/promotions",
            keywords="coupon promo code discount stack one per order",
        ),
        Faq(
            question="Do you offer price adjustments if an item goes on sale?",
            answer="If an item you bought drops in price within 7 days of your purchase, contact support and we refund the difference to your original payment method.",
            source="policy/price-adjustment",
            keywords="price adjustment sale drop difference refund match",
        ),
        Faq(
            question="Which payment methods do you accept?",
            answer="We accept Visa, Mastercard, American Express, PayPal, and Apple Pay. Cards are charged when the order ships.",
            source="policy/payments",
            keywords="payment card pay methods visa mastercard paypal apple",
        ),
        Faq(
            question="Is it safe to save my card on my account?",
            answer="Card details are stored by our payment processor, never on our servers; saved cards can be removed at any time from account settings.",
            source="policy/payment-security",
            keywords="card safe save stored security processor remove",
        ),
        Faq(
            question="How do I find my size?",
            answer="Each product page links a size guide with garment measurements. Between sizes? Our fit advice is one message away — the stylist can recommend based on the specific item's cut.",
            source="help/size-guide",
            keywords="size guide fit measurements chart between sizes",
        ),
        Faq(
            question="How do I reset my password?",
            answer="Use the 'Forgot password' link on the login page, or ask support to email you a secure reset link.",
            source="help/account",
            keywords="password reset account login email forgot",
        ),
        Faq(
            question="How is my personal data used?",
            answer="Your data is used only to fulfill orders and personalize your shopping experience; it is never sold. You can request an export or deletion of your account data from support.",
            source="policy/privacy",
            keywords="privacy data personal gdpr delete export sold",
        ),
    ]
    return [
        *products,
        *coupons,
        *orders,
        *_seed_order_items(),
        *returns,
        *accounts,
        *cart_items,
        *payments,
        *faqs,
    ]


def ensure_updates() -> None:
    """Top up a database seeded by an older version: role accounts, order line
    items, and policy-handbook entries added since the first seed.

    Runs on every invocation and only inserts (or fixes) what is missing, so an
    existing DB migrates with a plain seed run — no --reset required.
    """
    added: list[str] = []
    with SessionLocal.begin() as session:
        for account in _role_accounts():
            existing = session.get(Account, account.email)
            if existing is None:
                session.add(account)
                added.append(account.email)
            elif (existing.role, existing.shop) != (account.role, account.shop):
                existing.role, existing.shop = account.role, account.shop
                added.append(f"{account.email} (role fixed)")
        items_by_order: dict[str, list[OrderItem]] = {}
        for item in _seed_order_items():
            items_by_order.setdefault(item.order_id, []).append(item)
        for order_id, items in items_by_order.items():
            # Backfill only orders that exist and have no lines yet, so a partially
            # migrated DB heals without ever duplicating rows.
            if session.get(Order, order_id) is None:
                continue
            lines = session.scalar(
                select(func.count()).select_from(OrderItem).where(OrderItem.order_id == order_id)
            )
            if not lines:
                session.add_all(items)
                added.append(f"{order_id} line items")
        known_questions = set(session.scalars(select(Faq.question)).all())
        new_faqs = [f for f in _rows() if isinstance(f, Faq) and f.question not in known_questions]
        if new_faqs:
            session.add_all(new_faqs)
            added.append(f"{len(new_faqs)} policy entries")
    if added:
        print(f"Topped up: {', '.join(added)}.")


def ensure_embeddings() -> None:
    """Index the FAQ and catalog corpora in Qdrant for semantic retrieval (idempotent)."""
    try:
        embedded = retrieval.ensure_embeddings()
    except Exception as exc:  # noqa: BLE001 - retrieval is optional; keyword search still works
        print(
            f"Semantic index skipped ({exc}) — needs the qdrant container "
            "(docker compose up -d) and OPENAI_API_KEY (or EMBEDDING_MODEL)."
        )
        return
    if embedded:
        print(f"Semantic index (Qdrant): embedded {embedded} documents.")
    else:
        print("Semantic index (Qdrant) up to date.")


def seed() -> None:
    """Create the schema (if missing) and insert the dummy data, unless it is already there."""
    create_all()
    with SessionLocal.begin() as session:
        if session.scalar(select(func.count()).select_from(Product)):
            print("Shop database already seeded.")
        else:
            rows = _rows()
            session.add_all(rows)
            print(f"Seeded {len(rows)} rows into the 'shop' schema.")
    ensure_updates()
    ensure_embeddings()


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
