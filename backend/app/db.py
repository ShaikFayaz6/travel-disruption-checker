"""The Supabase Postgres database: accounts, checks and Stripe events (see supabase/migrations/).

Off when DATABASE_URL is empty: then every function here does nothing and the app runs as before.
The backend connects as the database owner, so Row Level Security doesn't limit it.
"""
from __future__ import annotations

import datetime as dt
import uuid

from .config import DATABASE_URL

try:
    import asyncpg
except ImportError:  # pragma: no cover - only needed when DATABASE_URL is set
    asyncpg = None

_pool = None
KEEP = object()  # update_subscription: leave this column as it is


def enabled() -> bool:
    return bool(DATABASE_URL)


async def init() -> None:
    """Open the connection pool at startup. A failure is logged; the next query tries again."""
    if not enabled():
        return
    try:
        await _get()
    except Exception as e:
        print(f"  database: couldn't connect at startup ({e.__class__.__name__}: {e}); will retry", flush=True)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _get():
    global _pool
    if _pool is None:
        # statement_cache_size=0: Supabase's pooler can hand each query to a different connection,
        # where a statement prepared on another one doesn't exist
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0,
                                          command_timeout=10)
    return _pool


# ---- profiles ----

async def get_profile(user_id: str) -> dict | None:
    if not enabled():
        return None
    row = await (await _get()).fetchrow(
        "select id, email, stripe_customer_id, plan, subscription_status, current_period_end, cancel_at "
        "from profiles where id = $1", uuid.UUID(user_id))
    return dict(row) if row else None


async def ensure_profile(user_id: str, email: str | None) -> dict | None:
    """The user's profile, created if the sign-up trigger didn't (e.g. a user from before the migration)."""
    if not enabled():
        return None
    await (await _get()).execute(
        "insert into profiles (id, email) values ($1, $2) on conflict (id) do nothing", uuid.UUID(user_id), email)
    return await get_profile(user_id)


async def set_customer_id(user_id: str, customer_id: str) -> None:
    if enabled():
        await (await _get()).execute("update profiles set stripe_customer_id = $2 where id = $1",
                                     uuid.UUID(user_id), customer_id)


async def update_subscription(*, user_id: str | None = None, customer_id: str | None = None, plan: str | None = None,
                              status: str | None = None, period_end: dt.datetime | None = None,
                              cancel_at=KEEP) -> None:
    """Set a profile's plan / Stripe status / period end, found by user ID or else by Stripe customer ID.

    Only the values given are changed. cancel_at: when a cancelled plan stops; None clears it (renews again).
    """
    if not enabled() or not (user_id or customer_id):
        return
    sets, args = [], []
    for col, val in (("plan", plan), ("subscription_status", status), ("current_period_end", period_end)):
        if val is not None:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    if cancel_at is not KEEP:
        args.append(cancel_at)
        sets.append(f"cancel_at = ${len(args)}")
    if not sets:
        return
    if user_id:
        args.append(uuid.UUID(user_id))
        where = f"id = ${len(args)}"
        if customer_id:  # remember the customer, for later events that only carry it
            args.append(customer_id)
            sets.append(f"stripe_customer_id = ${len(args)}")
    else:
        args.append(customer_id)
        where = f"stripe_customer_id = ${len(args)}"
    await (await _get()).execute(f"update profiles set {', '.join(sets)} where {where}", *args)


# ---- checks (the free air-check limit) ----

async def fly_check_used(*, user_id: str | None, anon_id: uuid.UUID | None, ip_hash: str | None) -> bool:
    """True if this user, browser (anon ID) or network (IP hash) has already run an air check."""
    if not enabled():
        return False
    conds, args = [], []
    for col, val in (("user_id", uuid.UUID(user_id) if user_id else None), ("anon_id", anon_id), ("ip_hash", ip_hash)):
        if val is not None:
            args.append(val)
            conds.append(f"{col} = ${len(args)}")
    if not conds:
        return False
    sql = f"select exists(select 1 from checks where mode = 'fly' and ({' or '.join(conds)}))"
    return bool(await (await _get()).fetchval(sql, *args))


async def insert_check(*, user_id: str | None, anon_id: uuid.UUID | None, ip_hash: str | None, mode: str,
                       origin: str, destination: str, trip_date: dt.date) -> None:
    if enabled():
        await (await _get()).execute(
            "insert into checks (user_id, anon_id, ip_hash, mode, origin, destination, trip_date) "
            "values ($1, $2, $3, $4, $5, $6, $7)",
            uuid.UUID(user_id) if user_id else None, anon_id, ip_hash, mode, origin, destination, trip_date)


# ---- Stripe events ----

async def record_stripe_event(event_id: str, event_type: str) -> bool:
    """Store a webhook event's ID. True if it is new; False if it was already processed."""
    if not enabled():
        return True
    done = await (await _get()).fetchval(
        "insert into stripe_events (id, type) values ($1, $2) on conflict (id) do nothing returning id",
        event_id, event_type)
    return done is not None


async def forget_stripe_event(event_id: str) -> None:
    """Undo record_stripe_event when handling failed, so Stripe's retry is processed."""
    if enabled():
        await (await _get()).execute("delete from stripe_events where id = $1", event_id)
