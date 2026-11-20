"""Trip Check Pro: Stripe Checkout, the Customer Portal, the Stripe webhook, and GET /api/me.

The webhook is the only thing that changes a plan. Coming back to the success page proves nothing
(anyone can type that URL), so the account page just waits for the webhook's update.
Off (HTTP 404 / 503) unless the paywall and the Stripe settings are configured.
"""
from __future__ import annotations

import datetime as dt

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, db, ga4, gate
from .config import BILLING_ENABLED, FRONTEND_URL, PAYWALL_ENABLED, STRIPE_PRICE_ID, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter()

ENDED = ("canceled", "unpaid", "incomplete_expired")  # these statuses put the user back on the free plan


class CheckoutBody(BaseModel):
    ga_client_id: str | None = Field(None, max_length=64)  # from the _ga cookie: joins GA4's purchase to the visit


async def _signed_in(request: Request) -> dict:
    user = await auth.current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in first.")
    return user


def _need_billing() -> None:
    if not BILLING_ENABLED:
        raise HTTPException(status_code=503, detail="Payments aren't set up on this server yet.")


def period_end(sub: dict) -> dt.datetime | None:
    """When the paid period ends. Newer Stripe API versions keep it on the subscription item."""
    ts = sub.get("current_period_end")
    if ts is None:
        items = (sub.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc) if ts else None


def plan_for(status: str | None) -> str:
    return "free" if not status or status in ENDED else "pro"


# ---- the signed-in user's own account ----

@router.get("/api/me")
async def me(request: Request):
    if not PAYWALL_ENABLED:
        raise HTTPException(status_code=404, detail="Accounts aren't switched on.")
    user = await _signed_in(request)
    profile = await db.ensure_profile(user["id"], user["email"]) or {}
    end = profile.get("current_period_end")
    used = await db.fly_check_used(user_id=user["id"], anon_id=auth.anon_id(request), ip_hash=auth.ip_hash(request))
    return {"email": profile.get("email") or user["email"],
            "plan": "pro" if gate.is_paid(profile) else "free",
            "subscription_status": profile.get("subscription_status"),
            "current_period_end": end.isoformat() if end else None,
            "free_air_check_used": used}


@router.post("/api/billing/checkout")
async def checkout(body: CheckoutBody, request: Request):
    """A Stripe Checkout page for Trip Check Pro ($4.99 a month). The browser goes to the returned url."""
    _need_billing()
    user = await _signed_in(request)
    profile = await db.ensure_profile(user["id"], user["email"]) or {}
    if gate.is_paid(profile):
        raise HTTPException(status_code=409, detail="You already have Trip Check Pro.")
    customer = profile.get("stripe_customer_id")
    if not customer:  # one Stripe customer per user, so the portal shows all their invoices
        created = await stripe.Customer.create_async(email=user["email"], metadata={"user_id": user["id"]})
        customer = created.id
        await db.set_customer_id(user["id"], customer)
    metadata = {"user_id": user["id"]}
    if body.ga_client_id:
        metadata["ga_client_id"] = body.ga_client_id
    session = await stripe.checkout.Session.create_async(
        mode="subscription",
        customer=customer,
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        client_reference_id=user["id"],
        metadata=metadata,
        subscription_data={"metadata": {"user_id": user["id"]}},
        allow_promotion_codes=True,
        success_url=f"{FRONTEND_URL}/account?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_URL}/?checkout=cancelled",
    )
    return {"url": session.url}


@router.post("/api/billing/portal")
async def portal(request: Request):
    """Stripe's Customer Portal: change card, see invoices, cancel."""
    _need_billing()
    user = await _signed_in(request)
    profile = await db.get_profile(user["id"]) or {}
    if not profile.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="There's no billing account yet. Upgrade first.")
    session = await stripe.billing_portal.Session.create_async(customer=profile["stripe_customer_id"],
                                                               return_url=f"{FRONTEND_URL}/account")
    return {"url": session.url}


# ---- the webhook: Stripe tells us what happened ----

@router.post("/api/stripe/webhook")
async def webhook(request: Request):
    if not BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="Payments aren't set up on this server.")
    payload = await request.body()  # the raw bytes: the signature is over exactly these
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature"), STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.") from e
    event = event.to_dict()
    if not await db.record_stripe_event(event["id"], event["type"]):
        return {"received": True, "duplicate": True}  # Stripe sent it again: already handled
    try:
        await handle(event)
    except Exception:
        await db.forget_stripe_event(event["id"])  # so Stripe's retry is processed
        raise
    return {"received": True}


async def handle(event: dict) -> None:
    kind, obj = event["type"], event["data"]["object"]
    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        sub = (await stripe.Subscription.retrieve_async(obj["subscription"])).to_dict()
        user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        await db.update_subscription(user_id=user_id, customer_id=obj.get("customer"), plan=plan_for(sub["status"]),
                                     status=sub["status"], period_end=period_end(sub))
        ga4.send_purchase(client_id=(obj.get("metadata") or {}).get("ga_client_id"),
                          transaction_id=obj.get("invoice") or obj["id"],
                          value=(obj.get("amount_total") or 0) / 100, currency=obj.get("currency") or "usd")
    elif kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        status = obj["status"]
        await db.update_subscription(user_id=(obj.get("metadata") or {}).get("user_id"),
                                     customer_id=obj.get("customer"), plan=plan_for(status), status=status,
                                     period_end=period_end(obj))
    elif kind == "invoice.payment_failed":
        await db.update_subscription(customer_id=obj.get("customer"), status="past_due")
