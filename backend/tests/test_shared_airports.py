"""An airport near both ends of a trip is listed and rated once."""
import asyncio
from collections import Counter
from datetime import date, timedelta

from app import assess as assess_mod
from app.models import Airport, AssessRequest, Signal
from tests.test_pipeline import stub  # noqa: F401  (autouse fixture: network stubbed)


def ap(iata, miles=0.0):
    return Airport(iata=iata, icao=f"K{iata}", name=iata, lat=33.0, lon=-97.0, type="large_airport", distance_miles=miles)


def iatas(airports):
    return [a.iata for a in airports]


def test_each_end_keeps_its_main_airport_and_drops_the_others_main():
    o, d = assess_mod._each_airport_on_one_side([ap("DAL", 6), ap("DFW", 18)], [ap("DFW", 22), ap("DAL", 33)])
    assert iatas(o) == ["DAL"] and iatas(d) == ["DFW"]


def test_an_extra_airport_near_both_ends_goes_to_the_nearer_one():
    o, d = assess_mod._each_airport_on_one_side([ap("AAA", 1), ap("CCC", 30)], [ap("BBB", 1), ap("CCC", 10)])
    assert iatas(o) == ["AAA"] and iatas(d) == ["BBB", "CCC"]
    o, d = assess_mod._each_airport_on_one_side([ap("AAA", 1), ap("CCC", 10)], [ap("BBB", 1), ap("CCC", 30)])
    assert iatas(o) == ["AAA", "CCC"] and iatas(d) == ["BBB"]


def test_airports_on_one_end_only_are_all_kept():
    o, d = assess_mod._each_airport_on_one_side([ap("JFK", 10), ap("LGA", 8)], [ap("SFO", 11), ap("OAK", 9)])
    assert iatas(o) == ["JFK", "LGA"] and iatas(d) == ["SFO", "OAK"]


def test_the_same_main_airport_at_both_ends_gives_one_item_the_worse_one(monkeypatch):
    async def same_airport(place, query):
        return [ap("DFW")]

    async def fetch(kind, icaos):
        return [{"icaoId": "KDFW"}] if kind == "taf" else []

    def tafs(found, airports, *, side, window):  # clear on the way out, worse by the time of landing
        return [Signal(id="taf-DFW", source="AviationWeather.gov TAF", category="aviation_weather", side=side,
                       location="DFW", severity=2 if side == "destination" else 0, title=side, detail="d")]

    monkeypatch.setattr(assess_mod, "resolve_airports", same_airport)
    monkeypatch.setattr(assess_mod.aviationweather, "fetch", fetch)
    monkeypatch.setattr(assess_mod.aviationweather, "evaluate_tafs", tafs)
    res = asyncio.run(assess_mod.assess(AssessRequest(origin="Denton, TX", destination="Austin, TX",
                                                      date=date.today() + timedelta(days=1), time="08:00", mode="fly")))
    assert max(Counter(s.id for s in res.signals).values()) == 1
    assert next(s for s in res.signals if s.id == "taf-DFW").title == "destination"
