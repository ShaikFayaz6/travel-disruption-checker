"""The paywall in the check endpoints: HTTP 402, counting only successful checks, and the mode hook."""
import asyncio
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import assess as assess_mod, gate, http, main
from app.models import AssessRequest
from tests.test_pipeline import fake_get, stub  # noqa: F401  (stub: the offline network fixture, autouse)

client = TestClient(main.app)  # no `with`: the startup tasks don't run
TOMORROW = date.today() + timedelta(days=1)
BODY = {"origin": "New York, NY", "destination": "SFO", "date": TOMORROW.isoformat(), "mode": "fly"}


def test_on_mode_gets_the_resolved_mode():
    seen = []

    async def on_mode(mode):
        seen.append(mode)

    asyncio.run(assess_mod.assess(AssessRequest(origin="New York, NY", destination="SFO", mode="auto",
                                                date=TOMORROW), on_mode))
    asyncio.run(assess_mod.assess(AssessRequest(origin="Denton, TX", destination="Austin, TX", mode="auto",
                                                date=TOMORROW), on_mode))
    assert seen == ["fly", "drive"]


def test_a_blocked_check_fetches_no_data(monkeypatch):
    urls = []

    async def counting_get(url, **kw):
        urls.append(url)
        return await fake_get(url, **kw)

    monkeypatch.setattr(http, "get", counting_get)

    async def block(mode):
        raise gate.PaywallBlocked(gate.Decision(False, code="free_limit_reached"), signed_in=False)

    with pytest.raises(gate.PaywallBlocked):
        asyncio.run(assess_mod.assess(AssessRequest(origin="New York, NY", destination="SFO", date=TOMORROW), block))
    # only the place lookup ran (a city name, or an airport code like SFO from the airport list): no weather,
    # airport status or news
    assert urls and all("geocoding" in u or "airports.csv" in u for u in urls)


@pytest.mark.parametrize("path", ["/api/assess", "/api/assess/stream"])
def test_blocked_air_check_is_402(monkeypatch, path):
    recorded = []

    async def decide(user, anon_id, ip_hash, mode):
        return gate.Decision(allowed=False, code="free_limit_reached")

    async def record(*a):
        recorded.append(a)

    monkeypatch.setattr(gate, "decide", decide)
    monkeypatch.setattr(gate, "record", record)
    r = client.post(path, json=BODY, headers={"X-Anon-Id": str(uuid.uuid4())})
    assert r.status_code == 402
    assert r.json()["detail"] == {"code": "free_limit_reached", "message": gate.MESSAGES["free_limit_reached"],
                                  "signed_in": False}
    assert recorded == []  # a blocked attempt doesn't count


@pytest.mark.parametrize("path", ["/api/assess", "/api/assess/stream"])
def test_successful_check_is_recorded_once(monkeypatch, path):
    recorded = []

    async def record(user, anon_id, ip_hash, mode, req):
        recorded.append(mode)

    monkeypatch.setattr(gate, "record", record)
    r = client.post(path, json=BODY)
    assert r.status_code == 200
    assert recorded == ["fly"]


def test_failed_check_is_not_recorded(monkeypatch):
    recorded = []

    async def record(*a):
        recorded.append(a)

    monkeypatch.setattr(gate, "record", record)
    past = BODY | {"date": (date.today() - timedelta(days=3)).isoformat()}
    assert client.post("/api/assess/stream", json=past).status_code == 400
    assert recorded == []
