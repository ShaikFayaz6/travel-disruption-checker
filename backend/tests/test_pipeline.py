"""End-to-end pipeline test with the network stubbed out (exercises orchestration code paths).

The running app never uses these stubs; it always calls the live APIs.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app import ai, assess as assess_mod, http
from app.models import AssessRequest
from app.sources import airports, places
from tests.test_parsers import FAA_XML

CSV = (
    "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,"
    "municipality,scheduled_service,icao_code,iata_code,gps_code,local_code,home_link,wikipedia_link,keywords\n"
    "1,KJFK,large_airport,John F Kennedy Intl,40.639,-73.778,13,NA,US,US-NY,New York,yes,KJFK,JFK,KJFK,,,,\n"
    "2,KLGA,large_airport,LaGuardia,40.777,-73.872,21,NA,US,US-NY,New York,yes,KLGA,LGA,KLGA,,,,\n"
    "3,KSFO,large_airport,San Francisco Intl,37.619,-122.375,13,NA,US,US-CA,San Francisco,yes,KSFO,SFO,KSFO,,,,\n"
    "4,KDFW,large_airport,Dallas Fort Worth Intl,32.897,-97.038,607,NA,US,US-TX,Dallas-Fort Worth,yes,KDFW,DFW,KDFW,,,,\n"
)
GEO = {
    "New York": {"name": "New York", "latitude": 40.71, "longitude": -74.0, "country_code": "US",
                 "admin1": "New York", "timezone": "America/New_York", "population": 8000000},
    "San Francisco": {"name": "San Francisco", "latitude": 37.77, "longitude": -122.42, "country_code": "US",
                      "admin1": "California", "timezone": "America/Los_Angeles", "population": 800000},
    "Denton": {"name": "Denton", "latitude": 33.21, "longitude": -97.13, "country_code": "US",
               "admin1": "Texas", "timezone": "America/Chicago", "population": 140000},
    "Austin": {"name": "Austin", "latitude": 30.27, "longitude": -97.74, "country_code": "US",
               "admin1": "Texas", "timezone": "America/Chicago", "population": 950000},
}


_recent = format_datetime(datetime.now(timezone.utc) - timedelta(hours=6))
_old = format_datetime(datetime.now(timezone.utc) - timedelta(days=20))
GOOGLE_RSS = f"""<rss><channel>
<item><title>Ground stop at SFO amid fog delays - KQED</title><link>https://news.google.com/a</link>
  <pubDate>{_recent}</pubDate><source url="https://kqed.org">KQED</source></item>
<item><title>Celebrity wedding - Gossip Daily</title><link>https://news.google.com/b</link>
  <pubDate>{_recent}</pubDate><source url="https://x.test">Gossip Daily</source></item>
<item><title>SFO delays last month - Old News</title><link>https://news.google.com/c</link>
  <pubDate>{_old}</pubDate><source url="https://x.test">Old News</source></item>
</channel></rss>"""


def om_payload(params):
    d = params["start_date"]
    return {"timezone": "America/New_York",
            "daily": {"weather_code": [95], "temperature_2m_max": [60], "temperature_2m_min": [45],
                      "precipitation_sum": [0.8], "precipitation_probability_max": [80],
                      "snowfall_sum": [0], "wind_gusts_10m_max": [35]},
            "hourly_units": {"visibility": "m"},
            "hourly": {"time": [f"{d}T{h:02d}:00" for h in range(24)], "visibility": [20000] * 24}}


async def fake_get(url, *, params=None, ttl=0, as_json=True, headers=None, **_):
    if "airports.csv" in url:
        return CSV
    if "geocoding" in url:
        return {"results": [GEO[params["name"]]]}
    if "api.open-meteo.com" in url:
        return om_payload(params)
    if "alerts/active" in url:
        return {"features": [{"properties": {"event": "Severe Thunderstorm Watch", "severity": "Severe",
                                             "onset": None, "effective": "2026-01-01T00:00:00Z",
                                             "expires": "2099-01-01T00:00:00Z", "headline": "Watch"}}]}
    if "/points/" in url:
        return {"properties": {"forecast": "https://api.weather.gov/gridpoints/X/1,1/forecast"}}
    if "gridpoints" in url:
        return {"properties": {"periods": []}}
    if "aviationweather" in url:
        return []
    if "nasstatus" in url:
        return FAA_XML
    if "news.google.com" in url:
        return GOOGLE_RSS
    if "gdeltproject" in url:
        return '{"articles": [{"title": "Ground stop at SFO amid fog delays", "url": "https://x.test/a", "domain": "x.test", "seendate": "20261026T120000Z"}, {"title": "Celebrity wedding", "url": "https://x.test/b", "domain": "x.test", "seendate": "20261026T120000Z"}]}'
    raise AssertionError(f"unexpected url {url}")


@pytest.fixture(autouse=True)
def stub(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(airports, "CACHE_FILE", tmp_path / "airports.csv")
    monkeypatch.setattr(airports, "_rows", None)
    monkeypatch.setattr(places, "_cities", [])  # empty city index: geocode falls through to the stubbed API
    monkeypatch.setattr(places, "_by_key", {})
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "")


def test_fly_pipeline():
    req = AssessRequest(origin="New York, NY", destination="SFO", date=date.today() + timedelta(days=1))
    res = asyncio.run(assess_mod.assess(req))
    assert res.mode == "fly"
    assert [a.iata for a in res.destination_airports] == ["SFO"]
    assert {a.iata for a in res.origin_airports} == {"JFK", "LGA"}
    assert res.risk_level in ("medium", "high")
    assert res.assessment.generated_by == "rules_fallback"
    news_sigs = [s for s in res.signals if s.category == "news"]
    # keyword fallback kept the recent SFO headline (publisher suffix stripped); dropped the wedding and the old story
    assert [(s.source, s.title) for s in news_sigs] == [("Google News", "Ground stop at SFO amid fog delays")]
    names = {s.name: s.status for s in res.sources}
    assert names["FAA NAS Status"] == "ok" and names["Gemini (AI)"] == "skipped"
    assert names["Google News"] == "ok" and names["GDELT news"] == "skipped"


def test_news_falls_back_to_gdelt(monkeypatch):
    async def google_down(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(assess_mod.news, "google_search", google_down)
    req = AssessRequest(origin="New York, NY", destination="SFO", date=date.today() + timedelta(days=1))
    res = asyncio.run(assess_mod.assess(req))
    names = {s.name: s.status for s in res.sources}
    assert names["Google News"] == "error" and names["GDELT news"] == "ok"
    assert any(s.source == "GDELT news" for s in res.signals)
    ids = {s.id for s in res.signals}
    assert all(i in ids for r in res.assessment.risks for i in r.evidence_ids)


def test_drive_pipeline():
    req = AssessRequest(origin="Denton, TX", destination="Austin, TX", date=date.today() + timedelta(days=3))
    res = asyncio.run(assess_mod.assess(req))
    assert res.mode == "drive" and not res.origin_airports
    assert any(s.side == "route" for s in res.signals)


def test_past_date_rejected():
    req = AssessRequest(origin="Denton, TX", destination="Austin, TX", date=date.today() - timedelta(days=2))
    with pytest.raises(assess_mod.TripError):
        asyncio.run(assess_mod.assess(req))


def test_synthesis_drops_unsupported_risks(monkeypatch):
    from app.models import Signal

    async def fake_generate(system, prompt, temperature=0.2):
        return {"headline": "h", "summary": "s", "uncertainty": "u",
                "risks": [{"title": "real", "severity": "high", "explanation": "e", "evidence_ids": ["om-o-wind", "bogus"]},
                          {"title": "made up", "severity": "high", "explanation": "e", "evidence_ids": ["nope"]}],
                "actions": [{"action": "a", "why": "w", "priority": "weird"}]}

    monkeypatch.setattr(ai, "_generate", fake_generate)
    sig = Signal(id="om-o-wind", source="Open-Meteo forecast", category="weather", side="origin",
                 location="JFK", severity=2, title="t", detail="d")
    out = asyncio.run(ai.synthesize({}, [sig]))
    assert [r.title for r in out.risks] == ["real"] and out.risks[0].evidence_ids == ["om-o-wind"]
    assert out.actions[0].priority == "before_trip" and out.generated_by == "gemini"
