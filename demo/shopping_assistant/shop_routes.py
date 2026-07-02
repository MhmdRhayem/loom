from __future__ import annotations

from fastapi import APIRouter

from . import db

shop_router = APIRouter(prefix="/shop", tags=["shop"])


@shop_router.get("/products")
def products(limit: int = 100) -> dict:
    """The catalog the storefront renders."""
    return {"products": db.list_products(limit)}


@shop_router.get("/orders")
def orders(limit: int = 100) -> dict:
    """Placed orders with status + shipping details."""
    return {"orders": db.list_orders(limit)}


@shop_router.get("/coupons")
def coupons() -> dict:
    """Active coupons and their scope."""
    return {"coupons": db.list_coupons()}
