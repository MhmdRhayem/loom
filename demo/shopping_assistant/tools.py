from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from . import db, retrieval

# --- catalog_advisor ---------------------------------------------------------


def product_db(query: str) -> dict[str, Any]:
    """Search the catalog across all of the company's shops (Atelier, Everyday, Steps & Co).
    Understands descriptive queries ("something elegant for a garden party"), not just
    keywords; returns id, name, price, rating, stock, and shop per product."""
    # Semantic retrieval when the catalog is indexed; keyword ranking otherwise.
    return retrieval.search_products(query) or db.search_products(query)


def price_api(product_id: str) -> dict[str, Any]:
    """Look up the current price, active deal, and best coupon for a product id. The
    coupon code is real and redeemable: the shopper enters it in the cart drawer at
    checkout, so quote the code itself and not just the discounted total."""
    return db.get_price(product_id)


# --- fit_stylist -------------------------------------------------------------


def style_engine(query: str) -> dict[str, Any]:
    """Return the store's general sizing and styling guidance. This is house-wide advice,
    not a per-item measurement lookup: it does not read the catalog, so do not present it
    as specific to a named product without checking that product with product_db."""
    # A fixed house guideline, not a model or a lookup. The store has no per-item size
    # chart to serve, and the docstring says so rather than letting the agent imply one.
    return {
        "query": query,
        "recommended_size": "M",
        "fit_notes": "Runs slightly large; size down if you are between sizes.",
        "style_tips": ["Pair with ankle boots", "Add a thin belt to define the waist"],
    }


# --- support_concierge -------------------------------------------------------


def faq_kb(query: str) -> dict[str, Any]:
    """Search the policy/FAQ knowledge base by meaning and return the best-matching
    answer with its policy source (plus related entries when they exist)."""
    # Semantic retrieval when the corpus is indexed; keyword matching otherwise.
    return retrieval.search_faq(query) or db.search_faq(query)


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

    def account_api(action: str, new_address: str = "") -> dict[str, Any]:
        """Account help for the signed-in customer (action: profile | reset_password |
        update_address). For update_address, pass the full new address in new_address."""
        if not email:
            return {"error": "No customer is signed in, so there is no account to act on."}
        return db.account_action(action, email, new_address)

    def cart_api(action: str = "view", item_id: str = "") -> dict[str, Any]:
        """Inspect or modify the customer's own cart and check stock (action: view | add |
        decrement | remove — add/decrement change the quantity by one, remove drops the line)."""
        if not email:
            return {"error": "No customer is signed in, so there is no cart to act on."}
        return db.cart_ops(action, item_id, email)

    def payment_api() -> dict[str, Any]:
        """Check payment/billing status for the customer's own cart and diagnose failures."""
        if not email:
            return {"error": "No customer is signed in, so there is no payment to check."}
        return db.get_payment_status(email)

    def ticket_api(summary: str) -> dict[str, Any]:
        """Open a support ticket for the signed-in customer and escalate to a human agent."""
        if not email:
            return {"error": "No customer is signed in, so a ticket cannot be opened."}
        return db.open_ticket(summary, email)

    return {
        "list_my_orders": list_my_orders,
        "order_api": order_api,
        "returns_api": returns_api,
        "account_api": account_api,
        "cart_api": cart_api,
        "payment_api": payment_api,
        "ticket_api": ticket_api,
    }


# --- merchant tools (shop_manager) --------------------------------------------
#
# Catalog management for the signed-in merchant. The owned shop is resolved fresh
# from the DB on every call (never taken from the model), and nothing here writes
# the catalog directly: every propose_* tool queues a pending product_changes row
# that the merchant must approve or reject on the Products page.


def _merchant_tools(email: str | None) -> dict[str, Callable[..., Any]]:
    denied = {"error": "Only a signed-in merchant can manage a shop."}

    def _shop() -> str | None:
        """The signed-in merchant's shop, resolved fresh from the DB (never from the model).

        Called from inside the tool bodies rather than at bind time: binding runs inline
        on the event loop (run_agent awaits nothing around it), so a synchronous query
        here would block every other request, while a tool body already runs in
        LangChain's executor. Reading per call also means a revoked merchant role takes
        effect immediately instead of at the next turn.
        """
        account = db.get_account(email) if email else None
        return account.shop if account is not None and account.role == "merchant" else None

    def list_shop_products() -> dict[str, Any]:
        """List every product in the merchant's own shop: id, name, price, stock, rating, deal."""
        shop = _shop()
        if not shop:
            return denied
        return {"shop": shop, "products": db.list_products(shop=shop)}

    def propose_product_create(
        name: str, price: float, category: str, description: str = "", deal: str = ""
    ) -> dict[str, Any]:
        """Propose adding a new product to the merchant's shop. The change is queued as
        PENDING and applied only after the merchant approves it on the Products page —
        always tell the merchant that."""
        shop = _shop()
        if not shop:
            return denied
        payload = {
            "name": name,
            "price": price,
            "category": category,
            "description": description,
            "deal": deal or None,
            "in_stock": True,
        }
        return db.create_product_change(shop, "create", None, payload)

    def propose_product_update(
        product_id: str,
        name: str | None = None,
        price: float | None = None,
        category: str | None = None,
        description: str | None = None,
        in_stock: bool | None = None,
        deal: str | None = None,
    ) -> dict[str, Any]:
        """Propose changing one of the merchant's own products; pass only the fields to
        change. Queued as PENDING until the merchant approves it on the Products page."""
        shop = _shop()
        if not shop:
            return denied
        fields = {
            "name": name,
            "price": price,
            "category": category,
            "description": description,
            "in_stock": in_stock,
            "deal": deal,
        }
        payload = {k: v for k, v in fields.items() if v is not None}
        if not payload:
            return {"error": "Pass at least one field to change."}
        return db.create_product_change(shop, "update", product_id, payload)

    def propose_product_delete(product_id: str) -> dict[str, Any]:
        """Propose removing one of the merchant's own products from the catalog.
        Queued as PENDING until the merchant approves it on the Products page."""
        shop = _shop()
        if not shop:
            return denied
        return db.create_product_change(shop, "delete", product_id, {})

    def list_pending_changes() -> dict[str, Any]:
        """The merchant's queued product changes still awaiting approval or rejection."""
        shop = _shop()
        if not shop:
            return denied
        return {"shop": shop, "pending": db.list_product_changes(shop, "pending")}

    return {
        "list_shop_products": list_shop_products,
        "propose_product_create": propose_product_create,
        "propose_product_update": propose_product_update,
        "propose_product_delete": propose_product_delete,
        "list_pending_changes": list_pending_changes,
    }


_MERCHANT_TOOL_NAMES = {
    "list_shop_products",
    "propose_product_create",
    "propose_product_update",
    "propose_product_delete",
    "list_pending_changes",
}


# --- registry ----------------------------------------------------------------

TOOLS: dict[str, Callable[..., Any]] = {
    "product_db": product_db,
    "price_api": price_api,
    "style_engine": style_engine,
    "faq_kb": faq_kb,
}


def get_tools(names: Sequence[str], owner_id: str | None = None) -> list[Callable[..., Any]]:
    """Map an agent's declared tool names to callables, binding the signed-in customer
    (owner_id = account email) into the tools that touch customer records."""
    tools = {**TOOLS, **_user_tools(owner_id)}
    # Only the merchant agent declares these, so don't build the five closures otherwise.
    if _MERCHANT_TOOL_NAMES & set(names):
        tools.update(_merchant_tools(owner_id))
    return [tools[name] for name in names if name in tools]
