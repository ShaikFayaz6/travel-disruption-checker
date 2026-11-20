"""Server-side GA4 events through the Measurement Protocol (used for `purchase`, from Stripe's webhook).

Sent from the server because the webhook is the only place that knows a payment really went
through: a browser can close the tab, block the tag, or open the success page without paying.
Off unless GA4_MEASUREMENT_ID and GA4_API_SECRET are set. Never raises.
"""
from __future__ import annotations

import asyncio
import random
import time

import httpx

from .config import GA4_API_SECRET, GA4_MEASUREMENT_ID

URL = "https://www.google-analytics.com/mp/collect"
_tasks: set[asyncio.Task] = set()  # keeps fire-and-forget sends alive until they finish


def enabled() -> bool:
    return bool(GA4_MEASUREMENT_ID and GA4_API_SECRET)


def random_client_id() -> str:
    """The shape GA4 uses ("<random>.<seconds>"), for a purchase without the browser's _ga cookie."""
    return f"{random.randint(10**8, 10**9 - 1)}.{int(time.time())}"


async def _send(client_id: str, event: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.post(URL, params={"measurement_id": GA4_MEASUREMENT_ID, "api_secret": GA4_API_SECRET},
                             json={"client_id": client_id, "events": [event]})
        if r.status_code >= 300:
            print(f"  GA4: {event['name']} got HTTP {r.status_code}", flush=True)
    except Exception as e:
        print(f"  GA4: couldn't send {event['name']} ({e.__class__.__name__}: {e})", flush=True)


def send_purchase(*, client_id: str | None, transaction_id: str, value: float, currency: str,
                  item_id: str = "tripcheck_pro_monthly", item_name: str = "Trip Check Pro") -> None:
    """Queue a GA4 `purchase` event and return straight away (fire and forget)."""
    if not enabled():
        return
    event = {"name": "purchase", "params": {
        "transaction_id": transaction_id, "value": value, "currency": currency.upper(),
        "items": [{"item_id": item_id, "item_name": item_name, "price": value, "quantity": 1}],
    }}
    task = asyncio.create_task(_send(client_id or random_client_id(), event))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
