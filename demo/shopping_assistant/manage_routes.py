from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db
from .auth import admin_account, manager_account

# Management API for merchants (their own shop) and admins (any shop + users).
# The scoping rule everywhere: an admin operates unscoped (None = any shop), a
# merchant is pinned to the shop on their account — client-supplied shop values
# are only ever honored for admins.

manage_router = APIRouter(prefix="/manage", tags=["manage"])


def _scope(account: db.Account) -> str | None:
    return None if account.role == "admin" else account.shop


class ProductCreate(BaseModel):
    name: str
    price: float
    category: str = ""
    description: str = ""
    deal: str | None = None
    in_stock: bool = True
    shop: str | None = None  # admin only — merchants are pinned to their own shop


class ProductUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    category: str | None = None
    description: str | None = None
    deal: str | None = None
    in_stock: bool | None = None


class RoleUpdate(BaseModel):
    role: str
    shop: str | None = None


# --- products ------------------------------------------------------------------


@manage_router.get("/products")
def manage_products(
    account: db.Account = Depends(manager_account), shop: str | None = None
) -> dict:
    """The manageable catalog: the merchant's own shop, or all shops (admin, ?shop= filters)."""
    scope = _scope(account)
    effective = scope if scope is not None else shop
    return {"shop": effective, "products": db.list_products(limit=500, shop=effective)}


@manage_router.post("/products")
def create_product(payload: ProductCreate, account: db.Account = Depends(manager_account)) -> dict:
    """Add a product. Merchants add to their own shop; admins must name an existing shop."""
    scope = _scope(account)
    shop = scope if scope is not None else (payload.shop or "").strip()
    if not shop or (scope is None and shop not in db.catalog_facets()["shops"]):
        raise HTTPException(status_code=422, detail="an admin must specify an existing shop")
    result = db.create_product(payload.model_dump(exclude={"shop"}), shop)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@manage_router.put("/products/{product_id}")
def update_product(
    product_id: str, payload: ProductUpdate, account: db.Account = Depends(manager_account)
) -> dict:
    """Change only the fields provided. 404 for products outside the caller's shop."""
    result = db.update_product(product_id, payload.model_dump(exclude_unset=True), _scope(account))
    if "error" in result:
        code = 404 if result["error"].startswith("No product") else 422
        raise HTTPException(status_code=code, detail=result["error"])
    return result


@manage_router.delete("/products/{product_id}")
def delete_product(product_id: str, account: db.Account = Depends(manager_account)) -> dict:
    """Remove a product. 404 for products outside the caller's shop."""
    result = db.delete_product(product_id, _scope(account))
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# --- stats + orders --------------------------------------------------------------


@manage_router.get("/stats")
def stats(account: db.Account = Depends(manager_account), shop: str | None = None) -> dict:
    """The stats strip: product count, in-stock count, avg rating/price for the scope."""
    scope = _scope(account)
    return db.shop_stats(scope if scope is not None else shop)


@manage_router.get("/orders")
def orders(account: db.Account = Depends(manager_account), shop: str | None = None) -> dict:
    """Customer orders containing the scope's products (only that shop's lines for merchants)."""
    scope = _scope(account)
    return {"orders": db.list_shop_orders(scope if scope is not None else shop)}


@manage_router.get("/sales")
def sales(account: db.Account = Depends(manager_account), shop: str | None = None) -> dict:
    """The sales dashboard rollup: totals, revenue by day, top products, status mix."""
    scope = _scope(account)
    return db.shop_sales(scope if scope is not None else shop)


# --- pending AI changes ----------------------------------------------------------


def _change_result(result: dict) -> dict:
    if "error" in result:
        if result["error"].startswith("No change"):
            raise HTTPException(status_code=404, detail=result["error"])
        if "already" in result["error"]:
            raise HTTPException(status_code=409, detail=result["error"])
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@manage_router.get("/changes")
def changes(account: db.Account = Depends(manager_account), status: str = "pending") -> dict:
    """The AI-proposal queue for the caller's scope (?status=pending|approved|rejected)."""
    return {"changes": db.list_product_changes(_scope(account), status or None)}


@manage_router.post("/changes/{change_id}/approve")
def approve_change(change_id: str, account: db.Account = Depends(manager_account)) -> dict:
    """Apply a pending AI proposal to the catalog (404 cross-shop, 409 if not pending)."""
    return _change_result(db.apply_product_change(change_id, _scope(account)))


@manage_router.post("/changes/{change_id}/reject")
def reject_change(change_id: str, account: db.Account = Depends(manager_account)) -> dict:
    """Discard a pending AI proposal (404 cross-shop, 409 if not pending)."""
    return _change_result(db.reject_product_change(change_id, _scope(account)))


# --- users (admin only) -----------------------------------------------------------


@manage_router.get("/users")
def users(account: db.Account = Depends(admin_account)) -> dict:
    """Every account with its role and shop, for the admin's Users page."""
    return {"users": db.list_accounts()}


@manage_router.put("/users/{email}")
def update_user(
    email: str, payload: RoleUpdate, account: db.Account = Depends(admin_account)
) -> dict:
    """Change an account's role (and owned shop for merchants). Not your own — lockout guard."""
    if email == account.email:
        raise HTTPException(status_code=400, detail="you cannot change your own role")
    if payload.role not in db.VALID_ROLES:
        raise HTTPException(status_code=422, detail="role must be client, merchant, or admin")
    shop = (payload.shop or "").strip() or None
    if payload.role == "merchant" and not shop:
        raise HTTPException(status_code=422, detail="a merchant account needs a shop")
    if not db.update_account_role(email, payload.role, shop):
        raise HTTPException(status_code=404, detail="no account with that email")
    return {"updated": True, "email": email, "role": payload.role, "shop": shop}


@manage_router.delete("/users/{email}")
def delete_user(email: str, account: db.Account = Depends(admin_account)) -> dict:
    """Remove an account — any except your own."""
    if email == account.email:
        raise HTTPException(status_code=400, detail="you cannot delete yourself")
    if not db.delete_account(email):
        raise HTTPException(status_code=404, detail="no account with that email")
    return {"deleted": True, "email": email}
