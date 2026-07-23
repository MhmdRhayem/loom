from __future__ import annotations

import functools
import hashlib
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    Identity,
    Integer,
    MetaData,
    Text,
    create_engine,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend.core.config import Settings

SCHEMA_NAME = "shop"


def _sync_dsn(url: str) -> str:
    """Force the sync psycopg driver on a plain postgresql:// DSN (needs the +psycopg suffix)."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


# create_engine / sessionmaker are lazy — they build no connection until first use,
# so importing this module is free of database I/O.
engine = create_engine(_sync_dsn(Settings.from_env().database_url), pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


# --- schema (typed ORM models, all in the `shop` schema) ---------------------


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA_NAME)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    rating: Mapped[float] = mapped_column(Float)
    in_stock: Mapped[bool] = mapped_column(Boolean)
    category: Mapped[str] = mapped_column(Text)
    shop: Mapped[str] = mapped_column(Text)  # which of the company's shops carries it
    description: Mapped[str] = mapped_column(Text)
    deal: Mapped[str | None] = mapped_column(Text)  # active promo blurb, nullable
    image_url: Mapped[str | None] = mapped_column(Text)  # served by /shop/images/{id}


class Coupon(Base):
    __tablename__ = "coupons"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    discount_pct: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[str | None] = mapped_column(Text)  # NULL = applies to any product
    min_subtotal: Mapped[float] = mapped_column(Float, default=0.0)


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # processing | shipped | delivered | cancelled
    carrier: Mapped[str | None] = mapped_column(Text)
    tracking_number: Mapped[str | None] = mapped_column(Text)
    estimated_delivery: Mapped[str | None] = mapped_column(Text)
    placed_on: Mapped[str] = mapped_column(Text)
    total: Mapped[float] = mapped_column(Float)


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Float)  # price at the time of purchase


class ProductChange(Base):
    """A product change proposed by the AI shop_manager agent, awaiting merchant approval."""

    __tablename__ = "product_changes"

    change_id: Mapped[str] = mapped_column(Text, primary_key=True)
    shop: Mapped[str] = mapped_column(Text)  # scope of the proposal
    action: Mapped[str] = mapped_column(Text)  # create | update | delete
    product_id: Mapped[str | None] = mapped_column(Text)  # NULL for create
    payload: Mapped[str] = mapped_column(Text)  # JSON dict of product fields
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | approved | rejected
    created_on: Mapped[str] = mapped_column(Text)


class Return(Base):
    __tablename__ = "returns"

    rma_number: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(Text)
    eligible: Mapped[bool] = mapped_column(Boolean)
    window_days_left: Mapped[int] = mapped_column(Integer)
    refund_method: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text)


class Account(Base):
    __tablename__ = "accounts"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)  # pbkdf2 string from auth.py
    role: Mapped[str] = mapped_column(Text, default="client")  # client | merchant | admin
    shop: Mapped[str | None] = mapped_column(Text)  # the shop a merchant owns; NULL otherwise


class CartItem(Base):
    __tablename__ = "cart_items"

    cart_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)


class Payment(Base):
    __tablename__ = "payments"

    cart_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payment_status: Mapped[str] = mapped_column(Text)  # approved | declined | pending
    reason: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)


class Faq(Base):
    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)  # the account that opened it
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    queue: Mapped[str] = mapped_column(Text)
    created_on: Mapped[str] = mapped_column(Text)


# --- schema lifecycle (called by seed.py, never at import) -------------------


def create_all() -> None:
    """Create the shop schema and all tables if missing. Idempotent."""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}"))
        Base.metadata.create_all(conn)
        # create_all never ALTERs an existing table, so columns added after the first
        # release are patched in here — keeps an already-seeded DB working sans --reset.
        conn.execute(
            text(
                f"ALTER TABLE {SCHEMA_NAME}.accounts "
                "ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'client'"
            )
        )
        conn.execute(text(f"ALTER TABLE {SCHEMA_NAME}.accounts ADD COLUMN IF NOT EXISTS shop TEXT"))
        conn.execute(text(f"ALTER TABLE {SCHEMA_NAME}.accounts DROP COLUMN IF EXISTS is_admin"))
        # Embeddings moved from a shop table into Qdrant (retrieval.py); drop the leftover.
        conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA_NAME}.embeddings"))


def reset() -> None:
    """Drop the whole shop schema and its data. Used by seed.py --reset."""
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))


# --- access functions (one per tool) -----------------------------------------


def _tokens(query: str) -> list[str]:
    """Split a natural-language query into lowercased search terms (3+ chars)."""
    return [w for w in query.lower().replace("?", " ").replace(",", " ").split() if len(w) >= 3]


def _search_blob(p: Product) -> str:
    """The lowercased text a product is matched against (one definition for all searches)."""
    return f"{p.name} {p.category} {p.shop} {p.description}".lower()


def _next_id(session: Session, model: type[Base], prefix: str, base: int) -> str:
    """Generate the next sequential id like RMA-55873 from the current row count.

    Only safe for tables whose rows are never deleted (returns, tickets, orders,
    product_changes) — a delete would shrink the count and re-issue a taken id.
    Two concurrent writers can still read the same count; their collision is a
    primary-key IntegrityError, which _retry_on_id_collision absorbs by rerunning.
    """
    count = session.scalar(select(func.count()).select_from(model)) or 0
    return f"{prefix}{base + count}"


def _retry_on_id_collision(fn):
    """Rerun a writer once when two concurrent requests raced _next_id to the same id.

    The failed transaction has rolled back, so the rerun recounts and gets a fresh id;
    a second collision in a row is beyond demo-level concurrency and propagates.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except IntegrityError:
            return fn(*args, **kwargs)

    return wrapper


def _next_product_id(session: Session) -> str:
    """Max numeric SKU suffix + 1 — delete-safe, unlike the count-based _next_id."""
    ids = session.scalars(select(Product.id).where(Product.id.like("SKU-%"))).all()
    suffixes = (i.split("-", 1)[1] for i in ids)
    top = max((int(s) for s in suffixes if s.isdigit()), default=1000)
    return f"SKU-{top + 1}"


def search_products(query: str) -> dict[str, Any]:
    """Catalog search ranked by how many query terms a product matches.

    Tables are tiny, so we score in Python rather than wrestle SQL: a natural query like
    "cheap summer dress" matches on any term, not just the verbatim phrase. Falls back to
    the top-rated items when nothing matches at all.
    """
    terms = _tokens(query)
    with SessionLocal() as session:
        products = session.scalars(select(Product)).all()

        def score(p: Product) -> int:
            blob = _search_blob(p)
            return sum(term in blob for term in terms)

        ranked = sorted(products, key=lambda p: (score(p), p.rating), reverse=True)
        chosen = [p for p in ranked if score(p) > 0][:8] or ranked[:5]
        results = [
            {
                "id": p.id,
                "name": p.name,
                "price": p.price,
                "rating": p.rating,
                "in_stock": p.in_stock,
                "shop": p.shop,
            }
            for p in chosen
        ]
    return {"query": query, "results": results}


def get_price(product_id: str) -> dict[str, Any]:
    """Current price, active deal, and best applicable coupon for a product id."""
    with SessionLocal() as session:
        product = session.get(Product, product_id)
        if product is None:
            return {"product_id": product_id, "found": False, "message": "No product with that id."}
        coupon = session.scalars(
            select(Coupon)
            .where(
                (Coupon.product_id == product_id) | (Coupon.product_id.is_(None)),
                Coupon.min_subtotal <= product.price,
            )
            .order_by(Coupon.discount_pct.desc())
            .limit(1)
        ).first()
        price, deal = product.price, product.deal
        best_coupon = {"code": coupon.code, "discount_pct": coupon.discount_pct} if coupon else None
        final_price = round(price * (1 - coupon.discount_pct / 100), 2) if coupon else price
    return {
        "product_id": product_id,
        "price": price,
        "deal": deal,
        "best_coupon": best_coupon,
        "final_price": final_price,
    }


def get_account(email: str) -> Account | None:
    """The account row for an email, or None (also None if the schema isn't seeded)."""
    try:
        with SessionLocal() as session:
            return session.get(Account, email)
    except Exception:  # noqa: BLE001 - unseeded schema / unreachable DB -> treat as no account
        return None


def create_account(email: str, name: str, address: str, password_hash: str) -> None:
    """Insert a new customer account (sign-up). Caller checks for duplicates first."""
    with SessionLocal.begin() as session:
        session.add(Account(email=email, name=name, address=address, password_hash=password_hash))


def get_order(order_id: str, email: str | None = None) -> dict[str, Any]:
    """Status, carrier, and delivery estimate for a placed order.

    With an email, only that customer's orders are visible — anyone else's order id
    answers not_found, so an order's existence never leaks across accounts.
    """
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        if order is None or (email is not None and order.email != email):
            return {
                "order_id": order_id,
                "status": "not_found",
                "message": "No order with that id on this account.",
            }
        return {
            "order_id": order.order_id,
            "status": order.status,
            "carrier": order.carrier,
            "tracking_number": order.tracking_number,
            "estimated_delivery": order.estimated_delivery,
        }


@_retry_on_id_collision
def start_return(order_id: str, email: str | None = None) -> dict[str, Any]:
    """Check return eligibility and start a return; persists an RMA and reuses an existing one.

    Scoped like get_order: with an email, another customer's order id answers not_found.
    """
    with SessionLocal.begin() as session:
        order = session.get(Order, order_id)
        if order is None or (email is not None and order.email != email):
            return {
                "order_id": order_id,
                "eligible": False,
                "message": "No order with that id on this account.",
            }

        existing = session.scalars(select(Return).where(Return.order_id == order_id)).first()
        if existing is not None:
            return {
                "order_id": order_id,
                "eligible": existing.eligible,
                "window_days_left": existing.window_days_left,
                "refund_method": existing.refund_method,
                "rma_number": existing.rma_number,
                "next_step": existing.next_step,
            }

        # Only shipped/delivered orders can be returned; cancelled/processing cannot.
        if order.status not in ("shipped", "delivered"):
            return {
                "order_id": order_id,
                "eligible": False,
                "window_days_left": 0,
                "refund_method": "original payment",
                "rma_number": None,
                "next_step": f"This order is '{order.status}' and is not eligible for a return.",
            }

        rma_number = _next_id(session, Return, "RMA-", 55872)
        next_step = "Pack the item and drop it at any carrier location using the emailed label."
        session.add(
            Return(
                rma_number=rma_number,
                order_id=order_id,
                eligible=True,
                window_days_left=30,
                refund_method="original payment",
                status="open",
                next_step=next_step,
            )
        )
        return {
            "order_id": order_id,
            "eligible": True,
            "window_days_left": 30,
            "refund_method": "original payment",
            "rma_number": rma_number,
            "next_step": next_step,
        }


def _cart_snapshot(session: Session, cart_id: str) -> tuple[list[dict[str, Any]], float, bool]:
    """Return the cart's line items (joined with product data), subtotal, and stock status."""
    rows = session.execute(
        select(Product.id, Product.name, CartItem.qty, Product.price, Product.in_stock)
        .join(Product, Product.id == CartItem.product_id)
        .where(CartItem.cart_id == cart_id)
        .order_by(Product.name)
    ).all()
    cart = [{"id": r.id, "name": r.name, "qty": r.qty, "price": r.price} for r in rows]
    subtotal = round(sum(r.qty * r.price for r in rows), 2)
    stock_ok = all(r.in_stock for r in rows)
    return cart, subtotal, stock_ok


def cart_ops(action: str, item_id: str, cart_id: str) -> dict[str, Any]:
    """Inspect or modify one customer's cart (view | add | decrement | remove) and report stock.

    add/decrement change the line's quantity by one (a decrement to zero deletes the
    line); remove deletes the whole line. cart_id is the account email — every
    customer has their own cart, never a shared one.
    """
    with SessionLocal.begin() as session:
        if action == "add" and item_id:
            if session.get(Product, item_id) is None:
                return {
                    "action": action,
                    "item_id": item_id,
                    "error": "No product with that id.",
                    "cart": [],
                    "subtotal": 0.0,
                    "stock_ok": False,
                }
            line = session.get(CartItem, {"cart_id": cart_id, "product_id": item_id})
            if line is None:
                session.add(CartItem(cart_id=cart_id, product_id=item_id, qty=1))
            else:
                line.qty += 1
        elif action == "decrement" and item_id:
            line = session.get(CartItem, {"cart_id": cart_id, "product_id": item_id})
            if line is not None:
                if line.qty > 1:
                    line.qty -= 1
                else:
                    session.delete(line)
        elif action == "remove" and item_id:
            line = session.get(CartItem, {"cart_id": cart_id, "product_id": item_id})
            if line is not None:
                session.delete(line)

        session.flush()
        cart, subtotal, stock_ok = _cart_snapshot(session, cart_id)
    return {
        "action": action,
        "item_id": item_id,
        "cart": cart,
        "subtotal": subtotal,
        "stock_ok": stock_ok,
    }


def get_payment_status(cart_id: str) -> dict[str, Any]:
    """Payment/billing status for one customer's cart (cart_id = account email)."""
    with SessionLocal() as session:
        payment = session.get(Payment, cart_id)
        if payment is None:
            return {
                "cart_id": cart_id,
                "payment_status": "approved",
                "reason": None,
                "suggestion": None,
            }
        return {
            "cart_id": cart_id,
            "payment_status": payment.payment_status,
            "reason": payment.reason,
            "suggestion": payment.suggestion,
        }


@_retry_on_id_collision
def place_order(email: str, new_card: bool = False) -> dict[str, Any]:
    """Turn the customer's cart into a placed order (the storefront checkout).

    A declined payment blocks checkout unless new_card is set, which simulates the
    customer switching to a working card (the payment row flips to approved).
    """
    with SessionLocal.begin() as session:
        cart, subtotal, stock_ok = _cart_snapshot(session, email)
        if not cart:
            return {"placed": False, "error": "Your cart is empty."}
        if not stock_ok:
            return {"placed": False, "error": "An item in your cart is out of stock."}

        payment = session.get(Payment, email)
        if payment is not None and payment.payment_status != "approved":
            if not new_card:
                return {
                    "placed": False,
                    "error": f"Payment {payment.payment_status}: {payment.reason}.",
                    "suggestion": payment.suggestion,
                    "retry_with_new_card": True,
                }
            payment.payment_status = "approved"
            payment.reason = None
            payment.suggestion = None

        order_id = _next_id(session, Order, "ORD-", 1001)
        placed_on = date.today().isoformat()
        estimated = (date.today() + timedelta(days=5)).isoformat()
        session.add(
            Order(
                order_id=order_id,
                email=email,
                status="processing",
                carrier=None,
                tracking_number=None,
                estimated_delivery=estimated,
                placed_on=placed_on,
                total=subtotal,
            )
        )
        for line in cart:
            session.add(
                OrderItem(
                    order_id=order_id,
                    product_id=line["id"],
                    qty=line["qty"],
                    unit_price=line["price"],
                )
            )
        session.execute(delete(CartItem).where(CartItem.cart_id == email))
        return {
            "placed": True,
            "order_id": order_id,
            "total": subtotal,
            "estimated_delivery": estimated,
        }


def account_action(action: str, email: str = "", new_address: str = "") -> dict[str, Any]:
    """Account help: profile lookup, password reset, or address update."""
    with SessionLocal.begin() as session:
        account = session.get(Account, email)
        if action == "profile":
            result: Any = (
                {"name": account.name, "address": account.address} if account else "not_found"
            )
        elif action == "reset_password":
            result = "ok" if account else "not_found"
        elif action == "update_address":
            if account is None:
                result = "not_found"
            elif not new_address.strip():
                result = "error: pass the new address in new_address"
            else:
                account.address = new_address.strip()
                result = {"name": account.name, "address": account.address}
        else:
            result = f"error: unknown action '{action}'"

    reset_link = None
    if action == "reset_password" and result == "ok":
        token = hashlib.sha1(email.encode()).hexdigest()[:10]
        reset_link = f"https://shop.example.com/reset/{token}"

    return {"action": action, "email": email, "result": result, "reset_link": reset_link}


def search_faq(query: str) -> dict[str, Any]:
    """Search the policy/FAQ knowledge base, ranking by how many query terms each entry matches."""
    terms = _tokens(query)
    with SessionLocal() as session:
        faqs = session.scalars(select(Faq)).all()

        def score(f: Faq) -> int:
            blob = f"{f.question} {f.keywords} {f.answer}".lower()
            return sum(term in blob for term in terms)

        best = max(faqs, key=score, default=None)
        if best is None or score(best) == 0:
            return {
                "query": query,
                "answer": "I couldn't find that in our help center. I can open a ticket so a human can help.",
                "source": None,
            }
        return {"query": query, "answer": best.answer, "source": best.source}


@_retry_on_id_collision
def open_ticket(summary: str, email: str | None = None) -> dict[str, Any]:
    """Open a support ticket in the human-support queue, attached to the customer."""
    with SessionLocal.begin() as session:
        ticket_id = _next_id(session, Ticket, "TCK-", 40192)
        session.add(
            Ticket(
                ticket_id=ticket_id,
                email=email,
                summary=summary,
                status="open",
                queue="human-support",
                created_on=date.today().isoformat(),
            )
        )
    return {"ticket_id": ticket_id, "summary": summary, "status": "open", "queue": "human-support"}


# --- bulk reads (for the storefront API, not the agent tools) -----------------
#
# These back the demo's /shop/* endpoints so the frontend can render the real
# catalog. They return whole tables (tiny, so no pagination beyond a cap) and
# fail soft: before ``seed.py`` runs the ``shop`` schema doesn't exist, so a query
# would raise — we return an empty list instead, matching the framework's
# boot-without-a-backend behavior.


def _safe(query) -> list[dict[str, Any]]:
    """Run a read against the shop store, returning [] if the schema isn't seeded yet."""
    try:
        with SessionLocal() as session:
            return query(session)
    except Exception:  # noqa: BLE001 - unseeded schema / unreachable DB -> empty, never 500
        return []


def _product_dict(p: Product) -> dict[str, Any]:
    """The full product row as the API/tool payload shape."""
    return {
        "id": p.id,
        "name": p.name,
        "price": p.price,
        "rating": p.rating,
        "in_stock": p.in_stock,
        "category": p.category,
        "shop": p.shop,
        "description": p.description,
        "deal": p.deal,
        "image_url": p.image_url,
    }


def list_products(
    limit: int = 100,
    *,
    shop: str | None = None,
    search: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
) -> list[dict[str, Any]]:
    """Catalog products, optionally filtered by shop / text / category / price / stock."""

    def q(session: Session) -> list[dict[str, Any]]:
        stmt = select(Product).order_by(Product.id).limit(limit)
        if shop:
            stmt = stmt.where(Product.shop == shop)
        if category:
            stmt = stmt.where(Product.category == category)
        if min_price is not None:
            stmt = stmt.where(Product.price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)
        if in_stock_only:
            stmt = stmt.where(Product.in_stock.is_(True))
        rows = session.scalars(stmt).all()
        if search:
            terms = _tokens(search)
            rows = [p for p in rows if any(t in _search_blob(p) for t in terms)]
        return [_product_dict(p) for p in rows]

    return _safe(q)


def catalog_facets() -> dict[str, Any]:
    """The filter options the storefront offers: shops, categories, and the price range."""
    try:
        with SessionLocal() as session:
            shops = sorted(session.scalars(select(Product.shop).distinct()).all())
            categories = sorted(session.scalars(select(Product.category).distinct()).all())
            price_min = session.scalar(select(func.min(Product.price)))
            price_max = session.scalar(select(func.max(Product.price)))
    except Exception:  # noqa: BLE001 - unseeded schema -> empty facets, never 500
        return {"shops": [], "categories": [], "price_min": 0.0, "price_max": 0.0}
    return {
        "shops": shops,
        "categories": categories,
        "price_min": float(price_min or 0),
        "price_max": float(price_max or 0),
    }


def get_product_card(product_id: str) -> dict[str, str] | None:
    """Name + category for the image renderer; None when the product doesn't exist."""
    try:
        with SessionLocal() as session:
            p = session.get(Product, product_id)
            return {"name": p.name, "category": p.category} if p else None
    except Exception:  # noqa: BLE001 - unseeded schema -> generic image
        return None


def list_orders(email: str, limit: int = 100) -> list[dict[str, Any]]:
    """One customer's orders with status, shipping details, and line items (never anyone else's)."""

    def q(session: Session) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(Order).where(Order.email == email).order_by(Order.order_id).limit(limit)
        ).all()
        items: dict[str, list[dict[str, Any]]] = {}
        if rows:
            lines = session.execute(
                select(OrderItem, Product.name)
                .join(Product, Product.id == OrderItem.product_id)
                .where(OrderItem.order_id.in_([o.order_id for o in rows]))
                .order_by(Product.name)
            ).all()
            for line, name in lines:
                items.setdefault(line.order_id, []).append(
                    {
                        "product_id": line.product_id,
                        "name": name,
                        "qty": line.qty,
                        "unit_price": line.unit_price,
                    }
                )
        return [
            {
                "order_id": o.order_id,
                "email": o.email,
                "status": o.status,
                "carrier": o.carrier,
                "tracking_number": o.tracking_number,
                "estimated_delivery": o.estimated_delivery,
                "placed_on": o.placed_on,
                "total": o.total,
                "items": items.get(o.order_id, []),
            }
            for o in rows
        ]

    return _safe(q)


# --- management (merchant + admin) --------------------------------------------
#
# Product CRUD, shop stats/orders, the AI-proposal queue, and user administration.
# Every mutator takes ``shop``: a string scopes the operation to that shop (merchant),
# ``None`` means any shop (admin). Cross-shop targets answer a not-found error so a
# product's existence never leaks across shops (same idea as get_order).

_EDITABLE_FIELDS = {"name", "price", "category", "description", "in_stock", "deal"}
VALID_ROLES = ("client", "merchant", "admin")


def _create_product_in(session: Session, fields: dict[str, Any], shop: str) -> dict[str, Any]:
    """Insert a product row inside an open transaction; returns the row or an error."""
    name = str(fields.get("name") or "").strip()
    try:
        price = round(float(fields.get("price") or 0), 2)
    except (TypeError, ValueError):
        price = 0.0
    if not name or price <= 0:
        return {"error": "A product needs a name and a price above zero."}
    pid = _next_product_id(session)
    deal = fields.get("deal")
    product = Product(
        id=pid,
        name=name,
        price=price,
        rating=float(fields.get("rating") or 0.0),  # new products start unrated
        in_stock=bool(fields.get("in_stock", True)),
        category=str(fields.get("category") or "").strip().lower() or "misc",
        shop=shop,
        description=str(fields.get("description") or "").strip(),
        deal=(str(deal).strip() or None) if deal else None,
        image_url=f"/shop/images/{pid}",
    )
    session.add(product)
    session.flush()
    return _product_dict(product)


def _update_product_in(
    session: Session, product_id: str, fields: dict[str, Any], shop: str | None
) -> dict[str, Any]:
    """Apply whitelisted field changes inside an open transaction."""
    product = session.get(Product, product_id)
    if product is None or (shop is not None and product.shop != shop):
        return {"error": f"No product {product_id} in your shop."}
    changes = {k: fields[k] for k in _EDITABLE_FIELDS & fields.keys()}
    if "name" in changes:
        changes["name"] = str(changes["name"]).strip()
        if not changes["name"]:
            return {"error": "A product name cannot be empty."}
    if "price" in changes:
        try:
            changes["price"] = round(float(changes["price"]), 2)
        except (TypeError, ValueError):
            return {"error": "Price must be a number."}
        if changes["price"] <= 0:
            return {"error": "Price must be above zero."}
    if "in_stock" in changes:
        changes["in_stock"] = bool(changes["in_stock"])
    if "deal" in changes:
        deal = changes["deal"]
        changes["deal"] = (str(deal).strip() or None) if deal else None
    for key, value in changes.items():
        setattr(product, key, value)
    session.flush()
    return _product_dict(product)


def _delete_product_in(session: Session, product_id: str, shop: str | None) -> dict[str, Any]:
    """Delete a product row inside an open transaction."""
    product = session.get(Product, product_id)
    if product is None or (shop is not None and product.shop != shop):
        return {"error": f"No product {product_id} in your shop."}
    name = product.name
    session.delete(product)
    return {"deleted": True, "product_id": product_id, "name": name}


def create_product(fields: dict[str, Any], shop: str) -> dict[str, Any]:
    """Add a product to one shop (manual form + approved AI proposals)."""
    with SessionLocal.begin() as session:
        return _create_product_in(session, fields, shop)


def update_product(product_id: str, fields: dict[str, Any], shop: str | None) -> dict[str, Any]:
    """Change whitelisted fields of one product; shop=None (admin) may touch any shop."""
    with SessionLocal.begin() as session:
        return _update_product_in(session, product_id, fields, shop)


def delete_product(product_id: str, shop: str | None) -> dict[str, Any]:
    """Remove one product; shop=None (admin) may touch any shop."""
    with SessionLocal.begin() as session:
        return _delete_product_in(session, product_id, shop)


def shop_stats(shop: str | None) -> dict[str, Any]:
    """Product count, in-stock count, and rating/price averages for one shop (or all)."""
    try:
        with SessionLocal() as session:
            stmt = select(
                func.count(),
                func.count().filter(Product.in_stock.is_(True)),
                func.avg(Product.rating),
                func.avg(Product.price),
            ).select_from(Product)
            if shop is not None:
                stmt = stmt.where(Product.shop == shop)
            total, in_stock, avg_rating, avg_price = session.execute(stmt).one()
    except Exception:  # noqa: BLE001 - unseeded schema -> empty stats, never 500
        return {"products": 0, "in_stock": 0, "avg_rating": None, "avg_price": None}
    return {
        "products": int(total or 0),
        "in_stock": int(in_stock or 0),
        "avg_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        "avg_price": round(float(avg_price), 2) if avg_price is not None else None,
    }


def shop_sales(shop: str | None) -> dict[str, Any]:
    """Sales rollup for one shop (or all shops): totals, revenue by day, top products, status mix.

    Revenue counts only the scope's line items (qty x unit_price at purchase time), so a
    merchant sees their share of mixed-shop orders, never the whole order total.
    """
    empty: dict[str, Any] = {
        "revenue": 0.0,
        "units": 0,
        "orders": 0,
        "avg_order_value": None,
        "by_day": [],
        "top_products": [],
        "status_counts": [],
    }
    try:
        with SessionLocal() as session:
            stmt = (
                select(
                    Order.order_id,
                    Order.placed_on,
                    Order.status,
                    OrderItem.product_id,
                    OrderItem.qty,
                    OrderItem.unit_price,
                    Product.name,
                )
                .join(OrderItem, OrderItem.order_id == Order.order_id)
                .join(Product, Product.id == OrderItem.product_id)
            )
            if shop is not None:
                stmt = stmt.where(Product.shop == shop)
            rows = session.execute(stmt).all()
    except Exception:  # noqa: BLE001 - unseeded schema -> empty dashboard, never 500
        return empty

    revenue, units = 0.0, 0
    days: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    order_status: dict[str, str] = {}
    for r in rows:
        order_status[r.order_id] = r.status
        if r.status == "cancelled":
            continue  # counted in the status mix, but never as revenue/units
        line = r.qty * r.unit_price
        revenue += line
        units += r.qty
        day = days.setdefault(r.placed_on, {"day": r.placed_on, "revenue": 0.0, "units": 0})
        day["revenue"] += line
        day["units"] += r.qty
        product = products.setdefault(
            r.product_id,
            {"product_id": r.product_id, "name": r.name, "units": 0, "revenue": 0.0},
        )
        product["units"] += r.qty
        product["revenue"] += line

    for bucket in (*days.values(), *products.values()):
        bucket["revenue"] = round(bucket["revenue"], 2)
    statuses: dict[str, int] = {}
    for status in order_status.values():
        statuses[status] = statuses.get(status, 0) + 1
    sold = sum(n for s, n in statuses.items() if s != "cancelled")
    return {
        "revenue": round(revenue, 2),
        "units": units,
        "orders": sold,
        "avg_order_value": round(revenue / sold, 2) if sold else None,
        "by_day": sorted(days.values(), key=lambda d: d["day"]),
        "top_products": sorted(products.values(), key=lambda p: p["revenue"], reverse=True)[:10],
        "status_counts": sorted(
            ({"status": s, "orders": n} for s, n in statuses.items()),
            key=lambda s: s["orders"],
            reverse=True,
        ),
    }


def list_shop_orders(shop: str | None, limit: int = 100) -> list[dict[str, Any]]:
    """Orders containing one shop's products, with only that shop's line items.

    The merchant payload never includes who the customer is; with shop=None (admin)
    every order and line is returned, plus the customer email and the order total.
    """

    def q(session: Session) -> list[dict[str, Any]]:
        stmt = (
            select(Order, OrderItem, Product.name, Product.shop)
            .join(OrderItem, OrderItem.order_id == Order.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .order_by(Order.order_id, Product.name)
        )
        if shop is not None:
            stmt = stmt.where(Product.shop == shop)
        orders: dict[str, dict[str, Any]] = {}
        for order, item, product_name, product_shop in session.execute(stmt):
            entry = orders.setdefault(
                order.order_id,
                {
                    "order_id": order.order_id,
                    "placed_on": order.placed_on,
                    "status": order.status,
                    **({"email": order.email, "order_total": order.total} if shop is None else {}),
                    "items": [],
                    "items_total": 0.0,
                },
            )
            entry["items"].append(
                {
                    "product_id": item.product_id,
                    "name": product_name,
                    **({"shop": product_shop} if shop is None else {}),
                    "qty": item.qty,
                    "unit_price": item.unit_price,
                }
            )
            entry["items_total"] = round(entry["items_total"] + item.qty * item.unit_price, 2)
        return list(orders.values())[:limit]

    return _safe(q)


@_retry_on_id_collision
def create_product_change(
    shop: str, action: str, product_id: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """Queue a pending product change (proposed by the AI, applied only on approval)."""
    if action not in ("create", "update", "delete"):
        return {"error": f"Unknown action '{action}'."}
    with SessionLocal.begin() as session:
        if action in ("update", "delete"):
            product = session.get(Product, product_id or "")
            if product is None or product.shop != shop:
                return {"error": f"No product {product_id} in your shop."}
        change_id = _next_id(session, ProductChange, "CHG-", 5001)
        session.add(
            ProductChange(
                change_id=change_id,
                shop=shop,
                action=action,
                product_id=product_id,
                payload=json.dumps(payload),
                status="pending",
                created_on=date.today().isoformat(),
            )
        )
    return {"change_id": change_id, "action": action, "status": "pending"}


def list_product_changes(shop: str | None, status: str | None = "pending") -> list[dict[str, Any]]:
    """The proposal queue for one shop (or all shops), payloads decoded for the UI."""

    def q(session: Session) -> list[dict[str, Any]]:
        stmt = select(ProductChange).order_by(ProductChange.change_id)
        if shop is not None:
            stmt = stmt.where(ProductChange.shop == shop)
        if status:
            stmt = stmt.where(ProductChange.status == status)
        return [
            {
                "change_id": c.change_id,
                "shop": c.shop,
                "action": c.action,
                "product_id": c.product_id,
                "payload": json.loads(c.payload),
                "status": c.status,
                "created_on": c.created_on,
            }
            for c in session.scalars(stmt)
        ]

    return _safe(q)


def apply_product_change(change_id: str, shop: str | None) -> dict[str, Any]:
    """Approve a pending change and apply it to the catalog in one transaction.

    If applying fails (e.g. the target product vanished since the proposal), the
    change stays pending and the error is returned — the merchant can reject it.
    """
    with SessionLocal.begin() as session:
        change = session.get(ProductChange, change_id)
        if change is None or (shop is not None and change.shop != shop):
            return {"error": f"No change {change_id} in your shop."}
        if change.status != "pending":
            return {"error": f"Change {change_id} was already {change.status}."}
        payload = json.loads(change.payload)
        if change.action == "create":
            result = _create_product_in(session, payload, change.shop)
        elif change.action == "update":
            result = _update_product_in(session, change.product_id or "", payload, change.shop)
        else:
            result = _delete_product_in(session, change.product_id or "", change.shop)
        if "error" in result:
            return {"change_id": change_id, "status": "pending", **result}
        change.status = "approved"
        return {"change_id": change_id, "status": "approved", "result": result}


def reject_product_change(change_id: str, shop: str | None) -> dict[str, Any]:
    """Discard a pending change without touching the catalog."""
    with SessionLocal.begin() as session:
        change = session.get(ProductChange, change_id)
        if change is None or (shop is not None and change.shop != shop):
            return {"error": f"No change {change_id} in your shop."}
        if change.status != "pending":
            return {"error": f"Change {change_id} was already {change.status}."}
        change.status = "rejected"
    return {"change_id": change_id, "status": "rejected"}


def list_accounts() -> list[dict[str, Any]]:
    """Every account (email, name, role, shop) for the admin's Users page."""

    def q(session: Session) -> list[dict[str, Any]]:
        rows = session.scalars(select(Account).order_by(Account.email)).all()
        return [{"email": a.email, "name": a.name, "role": a.role, "shop": a.shop} for a in rows]

    return _safe(q)


def update_account_role(email: str, role: str, shop: str | None) -> bool:
    """Set an account's role (and owned shop for merchants). False if no such account."""
    if role not in VALID_ROLES:
        return False
    with SessionLocal.begin() as session:
        account = session.get(Account, email)
        if account is None:
            return False
        account.role = role
        account.shop = shop if role == "merchant" else None
        return True


def delete_account(email: str) -> bool:
    """Remove an account. False if no such account."""
    with SessionLocal.begin() as session:
        account = session.get(Account, email)
        if account is None:
            return False
        session.delete(account)
        return True
