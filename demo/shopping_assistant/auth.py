from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import db

# Signing secret for the demo's JWTs. Override in .env for anything beyond local dev.
_SECRET = os.environ.get("AUTH_SECRET", "loom-demo-dev-secret")
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


async def resolve_identity(request: Request) -> str:
    """The identity resolver handed to create_app: Bearer token -> owner id (email)."""
    return current_email(request)


# --- routes -------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class UserInfo(BaseModel):
    email: str
    name: str


class LoginResponse(BaseModel):
    token: str
    user: UserInfo


@auth_router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    """Check the credentials against shop.accounts and issue a signed token."""
    account = db.get_account(payload.email.strip().lower())
    if (
        account is None
        or not account.password_hash
        or not verify_password(payload.password, account.password_hash)
    ):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return LoginResponse(
        token=_issue_token(account.email), user=UserInfo(email=account.email, name=account.name)
    )


@auth_router.get("/me", response_model=UserInfo)
def me(request: Request) -> UserInfo:
    """The signed-in user's profile; lets the frontend validate a stored token."""
    email = current_email(request)
    account = db.get_account(email)
    if account is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return UserInfo(email=account.email, name=account.name)
