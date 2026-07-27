from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import db

logger = logging.getLogger(__name__)

# Signing secret for the demo's JWTs. Override in .env for anything beyond local dev.
# `or`, not a get() default: an env file carrying `AUTH_SECRET=` hands back "", and an
# empty key makes PyJWT raise InvalidKeyError, which is not an InvalidTokenError and so
# escapes the handler in current_email — turning every login into a 500 instead of a 401.
# The fallback is >=32 bytes so HS256 doesn't run below its recommended key length.
_DEV_SECRET = "loom-demo-dev-secret-do-not-use-in-production"
_SECRET = os.environ.get("AUTH_SECRET") or _DEV_SECRET
if _SECRET == _DEV_SECRET:
    logger.warning("AUTH_SECRET is unset; signing demo JWTs with the public dev default")
_ALGORITHM = "HS256"
_TOKEN_TTL = timedelta(hours=24)
_PBKDF2_ITERATIONS = 600_000  # OWASP's 2024+ recommendation for pbkdf2-sha256

auth_router = APIRouter(prefix="/auth", tags=["auth"])


# --- passwords (stdlib pbkdf2, no extra dependency) ---------------------------


def hash_password(password: str) -> str:
    """Hash a password into a self-describing pbkdf2_sha256$iterations$salt$hash string."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash. Malformed hashes just fail the check."""
    try:
        _scheme, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False


# --- tokens -------------------------------------------------------------------


def _issue_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": email, "iat": now, "exp": now + _TOKEN_TTL}
    return jwt.encode(claims, _SECRET, algorithm=_ALGORITHM)


def current_email(request: Request) -> str:
    """The verified account email from the Bearer token; 401 if missing or invalid.

    Plain function so it works both as a FastAPI dependency (shop routes) and as
    the body of the framework's identity resolver.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        claims = jwt.decode(header[len("Bearer ") :], _SECRET, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid or expired token") from None
    return str(claims["sub"])


# Chat is the expensive endpoint (every message costs LLM calls), so it gets its own
# per-account ceiling — generous for a human, tight for a runaway script.
_CHAT_LIMIT_PER_MINUTE = 20


async def _check_chat_rate_limit(request: Request, email: str) -> None:
    """429 when an account exceeds the per-minute chat budget. Fails open without Redis."""
    client = _redis(request)
    if client is None:
        return
    try:
        key = f"chat_rl:{email}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60)
        if count > _CHAT_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=429, detail="too many messages — wait a minute and try again"
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - rate limiting must never block chat on Redis errors
        return


async def resolve_identity(request: Request) -> str:
    """The identity resolver handed to create_app: Bearer token -> owner id (email).

    Checks the account row still exists, so a deleted account's token stops working
    immediately instead of staying valid until it expires. (Sync DB read, so it runs
    in the threadpool — this sits on the async chat path.) Chat requests are also
    rate-limited per account here, since this runs before every turn.
    """
    email = current_email(request)
    if await run_in_threadpool(db.get_account, email) is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    # /dream shares the ceiling: it is the only other endpoint that spends model calls,
    # and a standard-tier consolidation pass is more expensive than a chat turn.
    if request.url.path.startswith(("/chat", "/dream")):
        await _check_chat_rate_limit(request, email)
    return email


# --- role guards ---------------------------------------------------------------
#
# The role is never baked into the token — every guard reads the account row fresh,
# so promoting or demoting an account takes effect on the very next request.


def current_account(request: Request) -> db.Account:
    """The signed-in account row; 401 if the token is missing/invalid or the account is gone."""
    account = db.get_account(current_email(request))
    if account is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return account


def admin_account(request: Request) -> db.Account:
    """Dependency: the signed-in account, which must be an admin (403 otherwise)."""
    account = current_account(request)
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return account


def manager_account(request: Request) -> db.Account:
    """Dependency: a merchant (with a shop) or an admin (403 otherwise)."""
    account = current_account(request)
    if account.role == "admin" or (account.role == "merchant" and account.shop):
        return account
    raise HTTPException(status_code=403, detail="merchant or admin access required")


async def require_admin(request: Request) -> None:
    """The analytics guard handed to create_app: only admins see /analytics/*.

    Threaded because admin_account does a sync DB read and this runs on the
    framework's async routes.
    """
    await run_in_threadpool(admin_account, request)


# Agents that exist only for merchants; everyone else never sees them — not in the
# router menu, not in /agents, and no ask_<peer> delegation tool is generated.
MERCHANT_ONLY_AGENTS = {"shop_manager"}


async def resolve_agent_visibility(request: Request) -> list[str] | None:
    """The agent-visibility hook handed to create_app.

    Merchants get the full roster (None = unrestricted); everyone else — including
    anonymous requests to the public /agents — gets the roster minus the
    merchant-only agents.
    """
    try:
        account = await run_in_threadpool(db.get_account, current_email(request))
    except HTTPException:
        account = None  # anonymous -> the restricted default
    if account is not None and account.role == "merchant" and account.shop:
        return None
    registry = request.app.state.registry
    return [name for name in registry.names() if name not in MERCHANT_ONLY_AGENTS]


# --- routes -------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    address: str = ""


class UserInfo(BaseModel):
    email: str
    name: str
    role: str = "client"  # client | merchant | admin
    shop: str | None = None  # the shop a merchant owns


class LoginResponse(BaseModel):
    token: str
    user: UserInfo


def _user_info(account: db.Account) -> UserInfo:
    return UserInfo(email=account.email, name=account.name, role=account.role, shop=account.shop)


# --- login rate limiting (Redis-backed, fails open when Redis is down) --------

_MAX_LOGIN_FAILURES = 5
_FAILURE_WINDOW_SECONDS = 600


def _redis(request: Request):
    store = getattr(request.app.state, "redis", None)
    return store.client if store is not None else None


async def _too_many_failures(request: Request, email: str) -> bool:
    client = _redis(request)
    if client is None:
        return False
    try:
        count = await client.get(f"login_fail:{email}")
        return int(count or 0) >= _MAX_LOGIN_FAILURES
    except Exception:  # noqa: BLE001 - rate limiting must never block logins on Redis errors
        return False


async def _record_failure(request: Request, email: str) -> None:
    client = _redis(request)
    if client is None:
        return
    try:
        key = f"login_fail:{email}"
        await client.incr(key)
        await client.expire(key, _FAILURE_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001 - a lost failure count must not break the login
        pass


async def _clear_failures(request: Request, email: str) -> None:
    client = _redis(request)
    if client is None:
        return
    try:
        await client.delete(f"login_fail:{email}")
    except Exception:  # noqa: BLE001 - a stale lockout counter expires on its own TTL
        pass


@auth_router.post("/login", response_model=LoginResponse)
async def login(request: Request, payload: LoginRequest) -> LoginResponse:
    """Check the credentials against shop.accounts and issue a signed token.

    Five failures for the same email within ten minutes locks the account out
    until the window passes (backed by Redis; fails open without it).
    """
    email = payload.email.strip().lower()
    if await _too_many_failures(request, email):
        raise HTTPException(
            status_code=429, detail="too many failed attempts — try again in a few minutes"
        )
    # Threaded: the handler must be async (for the Redis awaits), but the DB read is
    # sync and the 600k-iteration PBKDF2 check is ~hundreds of ms of pure CPU — run
    # inline they would stall the event loop and every in-flight request with it.
    account = await run_in_threadpool(db.get_account, email)
    if (
        account is None
        or not account.password_hash
        or not await run_in_threadpool(verify_password, payload.password, account.password_hash)
    ):
        await _record_failure(request, email)
        raise HTTPException(status_code=401, detail="invalid email or password")
    await _clear_failures(request, email)
    return LoginResponse(token=_issue_token(account.email), user=_user_info(account))


@auth_router.post("/register", response_model=LoginResponse)
def register(payload: RegisterRequest) -> LoginResponse:
    """Create a customer account and sign it in immediately."""
    email = payload.email.strip().lower()
    name = payload.name.strip()
    if not name or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="a name and a valid email are required")
    if len(payload.password) < 6:
        raise HTTPException(status_code=422, detail="password must be at least 6 characters")
    if db.get_account(email) is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    db.create_account(
        email=email,
        name=name,
        address=payload.address.strip(),
        password_hash=hash_password(payload.password),
    )
    return LoginResponse(token=_issue_token(email), user=UserInfo(email=email, name=name))


@auth_router.get("/me", response_model=UserInfo)
def me(request: Request) -> UserInfo:
    """The signed-in user's profile; lets the frontend validate a stored token."""
    return _user_info(current_account(request))
