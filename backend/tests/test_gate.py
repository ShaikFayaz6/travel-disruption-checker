"""The free air-check rule (gate.decide), with the database faked in memory."""
import asyncio
import datetime as dt
import uuid

import pytest

from app import db, gate

USER = {"id": str(uuid.uuid4()), "email": "a@example.com"}


class FakeDB:
    """checks: the air checks already stored; profiles: user_id -> profile."""

    def __init__(self):
        self.checks: list[dict] = []
        self.profiles: dict[str, dict] = {}

    async def fly_check_used(self, *, user_id, anon_id, ip_hash):
        return any((user_id and c["user_id"] == user_id) or (anon_id and c["anon_id"] == anon_id)
                   or (ip_hash and c["ip_hash"] == ip_hash) for c in self.checks)

    async def get_profile(self, user_id):
        return self.profiles.get(user_id)

    def used(self, user_id=None, anon_id=None, ip_hash=None):
        self.checks.append({"user_id": user_id, "anon_id": anon_id, "ip_hash": ip_hash})


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    monkeypatch.setattr(gate, "PAYWALL_ENABLED", True)
    monkeypatch.setattr(db, "fly_check_used", f.fly_check_used)
    monkeypatch.setattr(db, "get_profile", f.get_profile)
    return f


def decide(user, anon, ip, mode="fly"):
    return asyncio.run(gate.decide(user, anon, ip, mode))


def test_drive_is_always_allowed(fake):
    anon = uuid.uuid4()
    fake.used(anon_id=anon, ip_hash="ip1")
    d = decide(None, anon, "ip1", mode="drive")
    assert d.allowed and not d.uses_free


def test_first_anonymous_air_check_is_free(fake):
    d = decide(None, uuid.uuid4(), "ip1")
    assert d.allowed and d.uses_free


def test_second_air_check_same_browser_is_blocked(fake):
    anon = uuid.uuid4()
    fake.used(anon_id=anon, ip_hash="ip1")
    d = decide(None, anon, "ip2")
    assert not d.allowed and d.code == "free_limit_reached"


def test_same_ip_with_new_browser_id_is_blocked(fake):
    fake.used(anon_id=uuid.uuid4(), ip_hash="ip1")  # localStorage cleared: a new anon ID, same network
    d = decide(None, uuid.uuid4(), "ip1")
    assert not d.allowed and d.code == "free_limit_reached"


def test_signed_in_free_user_who_used_it_needs_a_subscription(fake):
    fake.used(user_id=USER["id"], anon_id=uuid.uuid4(), ip_hash="ip1")
    d = decide(USER, uuid.uuid4(), "ip2")
    assert not d.allowed and d.code == "subscription_required"


def test_signing_up_does_not_give_a_second_free_check(fake):
    anon = uuid.uuid4()
    fake.used(anon_id=anon, ip_hash="ip1")  # used anonymously, then signed up on the same browser
    d = decide(USER, anon, "ip1")
    assert not d.allowed and d.code == "subscription_required"


def test_active_pro_is_allowed(fake):
    fake.used(user_id=USER["id"], ip_hash="ip1")
    fake.profiles[USER["id"]] = {"subscription_status": "active",
                                 "current_period_end": dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)}
    d = decide(USER, uuid.uuid4(), "ip1")
    assert d.allowed and not d.uses_free


def test_expired_pro_is_blocked(fake):
    fake.used(user_id=USER["id"], ip_hash="ip1")
    fake.profiles[USER["id"]] = {"subscription_status": "active",
                                 "current_period_end": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)}
    d = decide(USER, uuid.uuid4(), "ip1")
    assert not d.allowed and d.code == "subscription_required"


def test_canceled_pro_is_blocked(fake):
    fake.used(user_id=USER["id"])
    fake.profiles[USER["id"]] = {"subscription_status": "canceled",
                                 "current_period_end": dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)}
    assert not decide(USER, None, "ip9").allowed


def test_paywall_disabled_always_allows(fake, monkeypatch):
    monkeypatch.setattr(gate, "PAYWALL_ENABLED", False)
    anon = uuid.uuid4()
    fake.used(anon_id=anon, ip_hash="ip1")
    d = decide(None, anon, "ip1")
    assert d.allowed and not d.uses_free


def test_database_error_lets_the_check_run(fake, monkeypatch):
    async def down(**_):
        raise ConnectionError("db down")
    monkeypatch.setattr(db, "fly_check_used", down)
    assert decide(None, uuid.uuid4(), "ip1").allowed
