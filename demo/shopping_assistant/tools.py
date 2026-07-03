from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable

from . import db

# --- catalog_advisor ---------------------------------------------------------


def product_db(query: str) -> dict[str, Any]:
    """Search the product catalog. Returns matching products with id, name, price, rating, stock."""
    return db.search_products(query)


def price_api(product_id: str) -> dict[str, Any]:
    """Look up the current price, active deal, and best coupon for a product id."""
    return db.get_price(product_id)


# --- checkout_payments -------------------------------------------------------


def cart_api(action: str = "view", item_id: str = "") -> dict[str, Any]:
    """Inspect or modify the in-flight cart (action: view | add | remove) and check stock."""
    return db.cart_ops(action, item_id)


def payment_api(cart_id: str = "current") -> dict[str, Any]:
    """Check payment/billing status for a cart and diagnose a checkout failure."""
    return db.get_payment_status(cart_id)


# --- fit_stylist -------------------------------------------------------------


def style_engine(query: str) -> dict[str, Any]:
    """Give size/fit and style recommendations for a shopper request."""
    # Pure advice, not stored records — kept computed rather than backed by the store.
    return {
        "query": query,
        "recommended_size": "M",
        "fit_notes": "Runs slightly large; size down if you are between sizes.",
        "style_tips": ["Pair with ankle boots", "Add a thin belt to define the waist"],
    }


# --- support_concierge -------------------------------------------------------


def faq_kb(query: str) -> dict[str, Any]:
    """Search the policy/FAQ knowledge base and return the best-matching answer."""
    return db.search_faq(query)


def ticket_api(summary: str) -> dict[str, Any]:
    """Open a support ticket and escalate to a human agent with context."""
    return db.open_ticket(summary)


# --- identity-bound tools (order_tracking, returns_refunds, account_assistant) --
#
# These read or write customer records, so the signed-in email is baked into the
# closure per request rather than taken as a model-controlled argument — the agent
# cannot look at anyone else's data no matter what the user asks it to do.


def _user_tools(email: str | None) -> dict[str, Callable[..., Any]]:
    def list_my_orders() -> dict[str, Any]:
        """List all of the signed-in customer's orders: id, status, tracking, dates, and totals."""
        if not email:
            return {"error": "No customer is signed in, so orders cannot be listed."}
        return {"orders": db.list_orders(email)}

    def order_api(order_id: str) -> dict[str, Any]:
        """Look up status, carrier, and delivery estimate for one of the customer's own orders."""
        if not email:
            return {"error": "No customer is signed in, so orders cannot be looked up."}
        return db.get_order(order_id, email)

    def returns_api(order_id: str) -> dict[str, Any]:
        """Check return eligibility and start a return/refund for one of the customer's own orders."""
        if not email:
            return {"error": "No customer is signed in, so returns cannot be started."}
        return db.start_return(order_id, email)

    def account_api(action: str) -> dict[str, Any]:
        """Account help for the signed-in customer (action: profile | reset_password | update_address)."""
        if not email:
            return {"error": "No customer is signed in, so there is no account to act on."}
        return db.account_action(action, email)

    return {
        "list_my_orders": list_my_orders,
        "order_api": order_api,
        "returns_api": returns_api,
        "account_api": account_api,
    }


# --- registry ----------------------------------------------------------------

TOOLS: dict[str, Callable[..., Any]] = {
    "product_db": product_db,
    "price_api": price_api,
    "cart_api": cart_api,
    "payment_api": payment_api,
    "style_engine": style_engine,
    "faq_kb": faq_kb,
    "ticket_api": ticket_api,
}


def get_tools(names: Sequence[str], owner_id: str | None = None) -> list[Callable[..., Any]]:
    """Map an agent's declared tool names to callables, binding the signed-in customer
    (owner_id = account email) into the tools that touch customer records."""
    tools = {**TOOLS, **_user_tools(owner_id)}
    return [tools[name] for name in names if name in tools]
