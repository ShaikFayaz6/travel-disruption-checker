"""Type-ahead suggestions, against a small GeoNames sample (no network)."""
import asyncio

import pytest

from app.sources import airports, places
from tests.test_pipeline import CSV


def row(name, state, pop, lat=33.0, lon=-97.0, tz="America/Chicago"):
    f = [""] * 19
    f[1], f[4], f[5], f[8], f[10], f[14], f[17] = name, str(lat), str(lon), "US", state, str(pop), tz
    return "\t".join(f)


SAMPLE = "\n".join([
    row("Denver", "CO", 715000), row("Denton", "TX", 139000), row("Dayton", "OH", 137000),
    row("Denton", "MD", 4400), row("Austin", "TX", 960000), row("New York City", "NY", 8800000),
])


@pytest.fixture(autouse=True)
def stub(monkeypatch, tmp_path):
    async def load_text():
        return SAMPLE

    async def fake_get(url, **_):
        return CSV

    monkeypatch.setattr(places, "_load_text", load_text)
    monkeypatch.setattr(places, "_cities", None)
    monkeypatch.setattr(places, "_by_key", {})
    monkeypatch.setattr(places.http, "get", fake_get)
    monkeypatch.setattr(airports, "CACHE_FILE", tmp_path / "airports.csv")
    monkeypatch.setattr(airports, "_rows", None)


def labels(q):
    return [s["label"] for s in asyncio.run(places.suggest(q))]


def test_prefix_ranked_by_population():
    assert labels("dent") == ["Denton, TX", "Denton, MD"]
    assert labels("de")[:2] == ["Denver, CO", "Denton, TX"]


def test_typo_suggests_real_city():
    assert "Denton, TX" in labels("danton")


def test_state_filter_and_word_prefix():
    assert labels("denton, md") == ["Denton, MD"]
    assert labels("york") == ["New York City, NY"]


def test_airport_code_first():
    assert labels("sfo")[0].startswith("SFO")


def test_too_short_returns_nothing():
    assert labels("d") == []


def test_suggestions_carry_time_zone():
    # the form uses it to say "Denton time · now 6:12 AM CST" to viewers in other zones
    s = asyncio.run(places.suggest("dent"))[0]
    assert s["label"] == "Denton, TX" and s["timezone"] == "America/Chicago"


def test_lookup_exact():
    hit = asyncio.run(places.lookup("Denton", "TX"))
    assert hit and hit.timezone == "America/Chicago"
