"""Reading who is asking: the Supabase token, the anonymous ID and the hashed IP."""
import asyncio
import time
import uuid

import jwt
import pytest
from starlette.requests import Request

from app import auth

SECRET = "test-secret-at-least-32-bytes-long-for-hs256!"


def request(headers: dict[str, str], client=("203.0.113.7", 1234)) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "client": client,
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


@pytest.fixture(autouse=True)
def supabase(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", SECRET)


def token(**claims):
    base = {"sub": "11111111-1111-1111-1111-111111111111", "email": "a@example.com",
            "aud": "authenticated", "exp": int(time.time()) + 600}
    return jwt.encode(base | claims, SECRET, algorithm="HS256")


def user(headers):
    return asyncio.run(auth.current_user(request(headers)))


def test_no_token_is_anonymous():
    assert user({}) is None


def test_garbage_token_is_anonymous():
    assert user({"Authorization": "Bearer not-a-jwt"}) is None
    assert user({"Authorization": "Basic abc"}) is None


def test_valid_token_is_the_user():
    assert user({"Authorization": f"Bearer {token()}"}) == {
        "id": "11111111-1111-1111-1111-111111111111", "email": "a@example.com"}


def test_wrong_secret_expired_or_wrong_audience_is_anonymous():
    forged = jwt.encode({"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 600},
                        "another-secret-that-is-also-32-bytes-long", algorithm="HS256")
    assert user({"Authorization": f"Bearer {forged}"}) is None
    assert user({"Authorization": f"Bearer {token(exp=int(time.time()) - 10)}"}) is None
    assert user({"Authorization": f"Bearer {token(aud='anon')}"}) is None


def test_accounts_off_means_anonymous(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "")
    assert user({"Authorization": f"Bearer {token()}"}) is None


def test_anon_id_must_be_a_uuid():
    good = uuid.uuid4()
    assert auth.anon_id(request({"X-Anon-Id": str(good)})) == good
    assert auth.anon_id(request({"X-Anon-Id": "nope"})) is None
    assert auth.anon_id(request({})) is None


def test_ip_is_hashed_and_uses_the_first_forwarded_address():
    direct = auth.ip_hash(request({}))
    assert "203.0.113.7" not in direct and len(direct) == 64
    forwarded = auth.ip_hash(request({"X-Forwarded-For": "198.51.100.1, 10.0.0.1"}))
    assert forwarded == auth.ip_hash(request({"X-Forwarded-For": "198.51.100.1"}, client=("10.9.9.9", 1)))
    assert forwarded != direct
