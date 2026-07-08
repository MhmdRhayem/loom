"""Demo auth primitives: password hashing and JWT verification."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from demo.shopping_assistant import auth


def test_password_roundtrip():
    stored = auth.hash_password("mohammad123")
    assert auth.verify_password("mohammad123", stored)
    assert not auth.verify_password("wrong", stored)


def test_hashes_are_salted():
    assert auth.hash_password("same") != auth.hash_password("same")


def test_malformed_hash_fails_closed():
    assert not auth.verify_password("anything", "not-a-real-hash")
    assert not auth.verify_password("anything", "")


def _request(header: str | None):
    headers = {"Authorization": header} if header else {}
    return SimpleNamespace(headers=headers)


def test_token_roundtrip():
    token = auth._issue_token("alice@example.com")
    assert auth.current_email(_request(f"Bearer {token}")) == "alice@example.com"


def test_missing_header_is_401():
    with pytest.raises(HTTPException) as err:
        auth.current_email(_request(None))
    assert err.value.status_code == 401


def test_garbage_token_is_401():
    with pytest.raises(HTTPException) as err:
        auth.current_email(_request("Bearer not.a.jwt"))
    assert err.value.status_code == 401


def test_expired_token_is_401():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": "alice@example.com", "exp": past}, auth._SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as err:
        auth.current_email(_request(f"Bearer {token}"))
    assert err.value.status_code == 401


def test_dev_secret_is_long_enough_for_hs256():
    assert len(auth._SECRET.encode()) >= 32
