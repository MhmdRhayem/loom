"""A small SQLAlchemy-backed mock store for the shopping-assistant demo.

This is the demo's *business* backend — the external systems the agents call into
(catalog, orders, carts, accounts, FAQ, support). It runs on the **same Postgres
instance as the framework** (the ``postgres:18`` service in ``docker-compose.yml``,
reached via ``DATABASE_URL``), but keeps its tables in a dedicated ``shop`` schema so
demo business data never collides with the framework's own tables in ``public``.

It uses **SQLAlchemy**, like the framework's storage layer
(``src/multi_agent_framework/storage/models.py``), so the schema is declared with typed
models rather than hand-written DDL. The one difference: the framework's storage is
*async* (it's in the request path), whereas these tools are plain *synchronous*
callables, so this module uses a **sync** engine. LangChain runs sync tools in a worker
thread, so the event loop is never blocked.

This module has **no import-time side effects** — importing it never touches the
database. Creating the schema and inserting dummy data is a deliberate one-time step:
run ``python -m demo.shopping_assistant.seed`` after ``docker compose up``. Because the
store is a real database, the demo is *stateful*: opening a ticket or starting a return
persists across turns.

Layout: the engine/session, the ORM models, and one access function per tool. Seed data
lives in ``seed.py``; ``tools.py`` is a thin layer mapping tool names to these functions.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    Identity,
    Integer,
    MetaData,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from multi_agent_framework.core.config import Settings

SCHEMA_NAME = "shop"


def _sync_dsn(url: str) -> str:
    """Force the sync psycopg driver on a plain ``postgresql://`` DSN (SQLAlchemy needs the +psycopg suffix)."""
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
    description: Mapped[str] = mapped_column(Text)
    deal: Mapped[str | None] = mapped_column(Text)  # active promo blurb, nullable


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
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    queue: Mapped[str] = mapped_column(Text)
    created_on: Mapped[str] = mapped_column(Text)


# --- schema lifecycle (called by seed.py, never at import) -------------------


def create_all() -> None:
    """Create the ``shop`` schema and all tables if missing. Idempotent."""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}"))
        Base.metadata.create_all(conn)


def reset() -> None:
    """Drop the whole ``shop`` schema and its data. Used by ``seed.py --reset``."""
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))


# --- access functions (one per tool) -----------------------------------------


def _tokens(query: str) -> list[str]:
    """Split a natural-language query into lowercased search terms (3+ chars)."""
    return [w for w in query.lower().replace("?", " ").replace(",", " ").split() if len(w) >= 3]


def _next_id(session: Session, model: type[Base], prefix: str, base: int) -> str:
    """Generate the next sequential id like ``RMA-55873`` from the existing row count."""
    count = session.scalar(select(func.count()).select_from(model)) or 0
    return f"{prefix}{base + count}"


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
            blob = f"{p.name} {p.category} {p.description}".lower()
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


def get_order(order_id: str) -> dict[str, Any]:
    """Status, carrier, and delivery estimate for a placed order."""
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        if order is None:
            return {
                "order_id": order_id,
                "status": "not_found",
                "message": "No order with that id.",
            }
        return {
            "order_id": order.order_id,
            "status": order.status,
            "carrier": order.carrier,
            "tracking_number": order.tracking_number,
            "estimated_delivery": order.estimated_delivery,
        }


def start_return(order_id: str) -> dict[str, Any]:
    """Check return eligibility and start a return; persists an RMA and reuses an existing one."""
    with SessionLocal.begin() as session:
        order = session.get(Order, order_id)
        if order is None:
            return {"order_id": order_id, "eligible": False, "message": "No order with that id."}

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


def cart_ops(action: str = "view", item_id: str = "", cart_id: str = "current") -> dict[str, Any]:
    """Inspect or modify the in-flight cart (view | add | remove) and report stock."""
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


def get_payment_status(cart_id: str = "current") -> dict[str, Any]:
    """Payment/billing status for a cart, with a suggested fix on failure."""
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


def account_action(action: str, email: str = "") -> dict[str, Any]:
    """Account help: profile lookup, password reset, or address update."""
    with SessionLocal() as session:
        account = session.get(Account, email)
        if action == "profile":
            result: Any = (
                {"name": account.name, "address": account.address} if account else "not_found"
            )
        else:
            result = "ok" if account or not email else "not_found"

    reset_link = None
    if action == "reset_password" and email:
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


def open_ticket(summary: str) -> dict[str, Any]:
    """Open a support ticket in the human-support queue and persist it."""
    with SessionLocal.begin() as session:
        ticket_id = _next_id(session, Ticket, "TCK-", 40192)
        session.add(
            Ticket(
                ticket_id=ticket_id,
                summary=summary,
                status="open",
                queue="human-support",
                created_on="2026-06-27",
            )
        )
    return {"ticket_id": ticket_id, "summary": summary, "status": "open", "queue": "human-support"}
