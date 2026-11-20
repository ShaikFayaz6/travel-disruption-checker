"""Who is making a request: a signed-in Supabase user, an anonymous browser, and a hashed IP.

The frontend (Vercel) and backend (Render) are on different sites and the stream is called
cross-origin, so cookies aren't used. The browser sends headers instead:
  Authorization: Bearer <Supabase access token>   (signed in)
  X-Anon-Id: <uuid kept in localStorage>          (every visitor)
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

import jwt
from fastapi import Request

from .config import IP_HASH_SALT, SUPABASE_JWT_SECRET, SUPABASE_URL

_jwks: jwt.PyJWKClient | None = None


def _jwks_client() -> jwt.PyJWKClient:
    """Supabase's public signing keys, fetched once and cached (PyJWKClient caches them itself)."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True, timeout=5)
    return _jwks


async def verify(token: str) -> dict | None:
    """The user in a Supabase access token, or None if it isn't valid. Never raises."""
    if not SUPABASE_URL or not token:
        return None
    try:
        if SUPABASE_JWT_SECRET:  # legacy projects: tokens signed with a shared secret
            claims = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
        else:  # current projects: tokens signed with a private key; check with the public one
            key = await asyncio.to_thread(_jwks_client().get_signing_key_from_jwt, token)  # it blocks on first fetch
            claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience="authenticated")
    except Exception:  # expired, forged, garbage, or the key list couldn't be fetched
        return None
    if not claims.get("sub"):
        return None
    return {"id": claims["sub"], "email": claims.get("email")}


async def current_user(request: Request) -> dict | None:
    """The signed-in user from `Authorization: Bearer <jwt>`, or None (anonymous use keeps working)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return await verify(token.strip())


def anon_id(request: Request) -> uuid.UUID | None:
    """The browser's anonymous ID from `X-Anon-Id`, if it is a valid UUID."""
    try:
        return uuid.UUID(request.headers.get("x-anon-id", "").strip())
    except ValueError:
        return None


def ip_hash(request: Request) -> str:
    """The visitor's IP, salted and hashed (raw IPs are never stored).

    Behind Render's and Vercel's proxies the real address is the first one in X-Forwarded-For.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")
    return hashlib.sha256(f"{IP_HASH_SALT}:{ip}".encode()).hexdigest()
