from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from . import db
from .auth import current_account

shop_router = APIRouter(prefix="/shop", tags=["shop"])


class CartItemRequest(BaseModel):
    product_id: str


class CheckoutRequest(BaseModel):
    # Simulates switching to a working card when the seeded payment is declined.
    new_card: bool = False
    # Optional promo code. Empty means no coupon; an invalid one fails the checkout
    # rather than being dropped, so the price charged always matches the price quoted.
    coupon_code: str = ""


@shop_router.get("/products")
def products(
    limit: int = 100,
    shop: str | None = None,
    q: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool = False,
) -> dict:
    """The catalog the storefront renders, with filters and the available facets."""
    rows = db.list_products(
        limit,
        shop=shop,
        search=q,
        category=category,
        min_price=min_price,
        max_price=max_price,
        in_stock_only=in_stock,
    )
    return {"products": rows, **db.catalog_facets()}


# Category -> emoji for the generated product art. Default covers unknown categories.
_CATEGORY_EMOJI = {
    "dresses": "👗",
    "tops": "👕",
    "bottoms": "👖",
    "knitwear": "🧶",
    "outerwear": "🧥",
    "shoes": "👟",
    "accessories": "👜",
    "activewear": "🎽",
}


@shop_router.get("/images/{product_id}")
def product_image(product_id: str) -> Response:
    """A deterministic SVG product image: gradient from the id, emoji from the category.

    Keeps the demo fully offline — no external image CDN — while every product still
    gets distinct, stable art.
    """
    card = db.get_product_card(product_id)
    emoji = _CATEGORY_EMOJI.get((card or {}).get("category", ""), "🛍️")
    hue = int(hashlib.md5(product_id.encode()).hexdigest(), 16) % 360
    hue2 = (hue + 45) % 360
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="hsl({hue}, 42%, 26%)"/>
      <stop offset="100%" stop-color="hsl({hue2}, 50%, 14%)"/>
    </linearGradient>
  </defs>
  <rect width="400" height="300" fill="url(#g)"/>
  <circle cx="330" cy="30" r="130" fill="hsl({hue}, 60%, 55%)" opacity="0.10"/>
  <circle cx="40" cy="280" r="90" fill="hsl({hue2}, 60%, 60%)" opacity="0.08"/>
  <text x="200" y="182" font-size="96" text-anchor="middle">{emoji}</text>
</svg>"""
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Customer routes depend on the account row (not just the token), so a deleted
# account loses access the moment it's gone — same rule as the role guards.


@shop_router.get("/orders")
def orders(account: db.Account = Depends(current_account), limit: int = 100) -> dict:
    """The signed-in customer's orders with status + shipping details."""
    return {"orders": db.list_orders(account.email, limit)}


@shop_router.get("/cart")
def cart(account: db.Account = Depends(current_account)) -> dict:
    """The signed-in customer's cart with line items, subtotal, and stock status."""
    return db.cart_ops("view", "", account.email)


@shop_router.post("/cart/add")
def cart_add(payload: CartItemRequest, account: db.Account = Depends(current_account)) -> dict:
    """Add one unit of a product to the signed-in customer's cart."""
    return db.cart_ops("add", payload.product_id, account.email)


@shop_router.post("/cart/decrement")
def cart_decrement(
    payload: CartItemRequest, account: db.Account = Depends(current_account)
) -> dict:
    """Take one unit of a product out of the cart (the line disappears at zero)."""
    return db.cart_ops("decrement", payload.product_id, account.email)


@shop_router.post("/cart/remove")
def cart_remove(payload: CartItemRequest, account: db.Account = Depends(current_account)) -> dict:
    """Remove a product line from the signed-in customer's cart."""
    return db.cart_ops("remove", payload.product_id, account.email)


@shop_router.post("/checkout")
def checkout(payload: CheckoutRequest, account: db.Account = Depends(current_account)) -> dict:
    """Place an order from the signed-in customer's cart."""
    return db.place_order(account.email, new_card=payload.new_card, coupon_code=payload.coupon_code)
