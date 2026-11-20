"""The Stripe webhook: signature check, profile updates and each event handled once (no network)."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from stripe import StripeObject

from app import billing, db, ga4
from app.main import app

SECRET = "whsec_test_secret"


def signed(payload: bytes, secret: str = SECRET) -> str:
    """The Stripe-Signature header, built the way stripe.WebhookSignature checks it."""
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def event(kind: str, obj: dict, event_id: str = "evt_1") -> bytes:
    return json.dumps({"id": event_id, "object": "event", "type": kind, "data": {"object": obj}}).encode()


@pytest.fixture
def calls(monkeypatch):
    """Fakes the database: returns the list of profile updates made."""
    seen: set[str] = set()
    updates: list[dict] = []

    async def record(event_id, _type):
        if event_id in seen:
            return False
        seen.add(event_id)
        return True

    async def update(**kw):
        updates.append(kw)

    async def forget(event_id):
        seen.discard(event_id)

    monkeypatch.setattr(billing, "BILLING_ENABLED", True)
    monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(db, "record_stripe_event", record)
    monkeypatch.setattr(db, "forget_stripe_event", forget)
    monkeypatch.setattr(db, "update_subscription", update)
    return updates


client = TestClient(app)  # no `with`: the startup tasks (city lists, database) don't run


def post(payload: bytes, signature: str):
    return client.post("/api/stripe/webhook", content=payload,
                       headers={"Stripe-Signature": signature, "Content-Type": "application/json"})


SUB = {"id": "sub_1", "object": "subscription", "status": "active", "customer": "cus_1",
       "metadata": {"user_id": "11111111-1111-1111-1111-111111111111"},
       "items": {"object": "list", "data": [{"id": "si_1", "object": "subscription_item",
                                             "current_period_end": 1900000000}]}}


def test_bad_signature_is_rejected(calls):
    payload = event("customer.subscription.updated", SUB)
    assert post(payload, signed(payload, "whsec_wrong")).status_code == 400
    assert post(payload, "garbage").status_code == 400
    assert calls == []


def test_subscription_update_changes_the_profile(calls):
    payload = event("customer.subscription.updated", SUB)
    r = post(payload, signed(payload))
    assert r.status_code == 200
    assert len(calls) == 1
    up = calls[0]
    assert up["user_id"] == SUB["metadata"]["user_id"] and up["plan"] == "pro" and up["status"] == "active"
    assert up["period_end"].timestamp() == 1900000000  # read from the subscription item (new API versions)


def test_same_event_twice_is_processed_once(calls):
    payload = event("customer.subscription.updated", SUB, event_id="evt_dup")
    assert post(payload, signed(payload)).status_code == 200
    assert post(payload, signed(payload)).json()["duplicate"] is True
    assert len(calls) == 1


def test_canceled_subscription_goes_back_to_free(calls):
    payload = event("customer.subscription.deleted", SUB | {"status": "canceled"}, event_id="evt_del")
    assert post(payload, signed(payload)).status_code == 200
    assert calls[0]["plan"] == "free" and calls[0]["status"] == "canceled"


def test_payment_failed_sets_past_due(calls):
    payload = event("invoice.payment_failed", {"id": "in_1", "object": "invoice", "customer": "cus_1"}, "evt_f")
    assert post(payload, signed(payload)).status_code == 200
    assert calls == [{"customer_id": "cus_1", "status": "past_due"}]


def test_checkout_completed_activates_pro_and_sends_purchase(calls, monkeypatch):
    class FakeSubscription:
        @staticmethod
        async def retrieve_async(sub_id):
            return StripeObject.construct_from(SUB | {"id": sub_id, "current_period_end": 1800000000}, "sk_test")

    purchases = []
    monkeypatch.setattr(billing.stripe, "Subscription", FakeSubscription)
    monkeypatch.setattr(ga4, "send_purchase", lambda **kw: purchases.append(kw))
    session = {"id": "cs_1", "object": "checkout.session", "mode": "subscription", "subscription": "sub_1",
               "customer": "cus_1", "client_reference_id": SUB["metadata"]["user_id"], "invoice": "in_1",
               "amount_total": 499, "currency": "usd", "metadata": {"ga_client_id": "123.456"}}
    payload = event("checkout.session.completed", session, "evt_cs")
    assert post(payload, signed(payload)).status_code == 200
    assert calls[0]["plan"] == "pro" and calls[0]["customer_id"] == "cus_1"
    assert calls[0]["period_end"].timestamp() == 1800000000  # older API versions: on the subscription
    assert purchases == [{"client_id": "123.456", "transaction_id": "in_1", "value": 4.99, "currency": "usd"}]


def test_webhook_is_off_without_billing(monkeypatch):
    monkeypatch.setattr(billing, "BILLING_ENABLED", False)
    payload = event("customer.subscription.updated", SUB)
    assert post(payload, signed(payload)).status_code == 404
