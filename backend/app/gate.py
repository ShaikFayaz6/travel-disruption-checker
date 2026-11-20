"""The paywall: one free air check per visitor, then an account and Trip Check Pro.

Car checks are always free. The rule is enforced here, on the server, so it can't be skipped by
changing the page. It is off (everything allowed) unless DATABASE_URL and SUPABASE_URL are set.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request

from . import auth, db
from .config import PAYWALL_ENABLED

PAID_STATUSES = ("active", "trialing")

MESSAGES = {
    "free_limit_reached": "You've used your free air check. Create a free account to continue.",
    "subscription_required": "You've used your free air check. Upgrade to Trip Check Pro for unlimited air checks.",
}


@dataclass
class Decision:
    allowed: bool
    uses_free: bool = False  # this check is the visitor's one free air check
    code: str | None = None  # why it was blocked: free_limit_reached | subscription_required


@dataclass
class Who:
    """Who is asking: a signed-in user (or None), the browser's anonymous ID, and the hashed IP."""
    user: dict | None
    anon_id: uuid.UUID | None
    ip_hash: str | None


class PaywallBlocked(Exception):
    def __init__(self, decision: Decision, signed_in: bool):
        super().__init__(decision.code)
        self.decision = decision
        self.signed_in = signed_in

    @property
    def detail(self) -> dict:
        """The body of the HTTP 402 response."""
        return {"code": self.decision.code, "message": MESSAGES[self.decision.code], "signed_in": self.signed_in}


def is_paid(profile: dict | None, now: dt.datetime | None = None) -> bool:
    """An active (or trialing) Pro subscription whose paid period hasn't ended."""
    if not profile or profile.get("subscription_status") not in PAID_STATUSES:
        return False
    end = profile.get("current_period_end")
    return end is not None and end > (now or dt.datetime.now(dt.timezone.utc))


async def decide(user: dict | None, anon_id: uuid.UUID | None, ip_hash: str | None, mode: str) -> Decision:
    """May this check run? Only air checks ("fly") are limited."""
    if not PAYWALL_ENABLED or mode != "fly":
        return Decision(allowed=True)
    try:
        if user and is_paid(await db.get_profile(user["id"])):
            return Decision(allowed=True)
        # Anonymous: neither this browser nor this network has used the free check.
        # Signed in: the same, and the account hasn't either (signing up doesn't give a second one).
        used = await db.fly_check_used(user_id=user["id"] if user else None, anon_id=anon_id, ip_hash=ip_hash)
    except Exception as e:  # database down: let the check run rather than block paying customers
        print(f"  paywall: database error, allowing the check ({e.__class__.__name__}: {e})", flush=True)
        return Decision(allowed=True)
    if not used:
        return Decision(allowed=True, uses_free=True)
    return Decision(allowed=False, code="subscription_required" if user else "free_limit_reached")


async def record(user: dict | None, anon_id: uuid.UUID | None, ip_hash: str | None, mode: str, req) -> None:
    """Store a check that produced an answer (car checks too, for usage numbers). Never raises."""
    if not PAYWALL_ENABLED:
        return
    try:
        await db.insert_check(user_id=user["id"] if user else None, anon_id=anon_id, ip_hash=ip_hash, mode=mode,
                              origin=req.origin, destination=req.destination, trip_date=req.date)
    except Exception as e:
        print(f"  paywall: couldn't record the check ({e.__class__.__name__}: {e})", flush=True)


# ---- small helpers for main.py ----

async def identify(request: Request) -> Who:
    if not PAYWALL_ENABLED:  # don't even read the headers
        return Who(None, None, None)
    return Who(await auth.current_user(request), auth.anon_id(request), auth.ip_hash(request))


def checker(who: Who) -> Callable[[str], Awaitable[None]]:
    """The on_mode callback for assess(): raises PaywallBlocked once the travel mode is known."""
    async def on_mode(mode: str) -> None:
        decision = await decide(who.user, who.anon_id, who.ip_hash, mode)
        if not decision.allowed:
            raise PaywallBlocked(decision, signed_in=who.user is not None)
    return on_mode


async def record_for(who: Who, mode: str, req) -> None:
    await record(who.user, who.anon_id, who.ip_hash, mode, req)
